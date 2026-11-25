"""Remux Imou recordings to HLS for every media browser client."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import shutil
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant, callback

from .record_hls_proxy import (
    RecordHlsDescriptor,
    async_remove_record_hls_proxy_playlist_session,
)
from .repairs import (
    async_create_record_playback_issue,
    async_delete_record_playback_issue,
)

_LOGGER = logging.getLogger(__name__)

DATA_RECORD_PLAYBACK_HLS = "imou_life_record_playback_hls"
PLAYLIST_URL = "/api/imou_life/record_play/{cache_id}.m3u8"
FILE_URL = "/api/imou_life/record_play_file/{cache_id}/{name}"
CACHE_SUBDIR = Path("imou_life") / "playback_hls"
RECORD_REMUX_TIMEOUT_SECONDS = 900
_PROCESS_STOP_TIMEOUT = 5
_PROBE_TIMEOUT = 30
_CACHE_TTL_SECONDS = 24 * 3600
_CACHE_MAX_DIRS = 32
_FILE_SIGNED_SECONDS = 24 * 3600
_STOP_GRACE_SECONDS = 60
_FIRST_SEGMENT_TIMEOUT = 45.0
_POLL_SECONDS = 0.2
_SEGMENT_FILE_RE = re.compile(r"(?:init\.mp4|seg_\d{5}\.m4s)")
_PLAYLIST_FILE = "index.m3u8"
_INIT_FILE = "init.mp4"
_PART_SUFFIX = ".part"


@dataclass(frozen=True, slots=True)
class PlaybackSource:
    """A recording source provider owned by one config entry."""

    entry_id: str
    provider: Callable[[], Awaitable[RecordHlsDescriptor]]
    live_timeout_seconds: float | None = None


@dataclass(slots=True)
class HlsPlaybackJob:
    """One ffmpeg remux feeding HLS playback of a recording clip."""

    cache_id: str
    entry_id: str
    part_dir: Path
    final_dir: Path
    task: asyncio.Task[None] | None = None
    live_deadline: float | None = None
    finished: bool = False
    stop_timer: asyncio.TimerHandle | None = None
    last_access: float = field(default_factory=time.time)


def _store(hass: HomeAssistant) -> dict[str, Any]:
    return hass.data.setdefault(
        DATA_RECORD_PLAYBACK_HLS,
        {
            "closed": set(),
            "locks": {},
            "owners": {},
            "signed": {},
            "sources": {},
            "jobs": {},
        },
    )


def _cache_dir(hass: HomeAssistant) -> Path:
    return Path(hass.config.path()) / CACHE_SUBDIR


def _final_dir(hass: HomeAssistant, cache_id: str) -> Path:
    return _cache_dir(hass) / cache_id


def record_playback_cache_id(identifier: str) -> str:
    """Stable file name for one playable recording clip."""
    return hashlib.sha256(identifier.encode()).hexdigest()[:32]


def async_register_record_playback_hls(hass: HomeAssistant) -> None:
    """Register HLS playback views and prepare the segment cache."""
    if hass.data.get(DATA_RECORD_PLAYBACK_HLS):
        return
    cache_dir = _cache_dir(hass)
    cache_dir.mkdir(parents=True, exist_ok=True)
    hass.async_add_executor_job(_prune_playback_cache, cache_dir)
    hass.http.register_view(ImouRecordPlaybackHlsView())
    hass.http.register_view(ImouRecordPlaybackFileView())
    _store(hass)


@callback
def register_playback_source(
    hass: HomeAssistant,
    cache_id: str,
    entry_id: str,
    source_provider: Callable[[], Awaitable[RecordHlsDescriptor]],
    *,
    live_timeout_seconds: float | None = None,
) -> None:
    """Remember how to request a stream URL while this clip can be replayed."""
    store = _store(hass)
    store["closed"].discard(cache_id)
    store["owners"][cache_id] = entry_id
    store["sources"][cache_id] = PlaybackSource(
        entry_id, source_provider, live_timeout_seconds
    )


def signed_record_playlist_url(
    hass: HomeAssistant, cache_id: str, ttl_seconds: float
) -> str:
    """Return an authenticated playlist URL for every media browser client.

    The playlist is reloaded for the whole clip (and on seek), so it lives as
    long as the signed segment URLs rather than only for the remux run.
    """
    return async_sign_path(
        hass,
        PLAYLIST_URL.format(cache_id=cache_id),
        timedelta(seconds=max(ttl_seconds, _FILE_SIGNED_SECONDS)),
        use_content_user=True,
    )


def _validate_proxy_source(source: RecordHlsDescriptor) -> None:
    """Require a signed playlist on this Home Assistant's HLS proxy."""
    if source.proxy_origin is None:
        raise ValueError("HLS source must be a local proxy playlist")
    parsed = urlsplit(source.playlist_url)
    origin = urlsplit(source.proxy_origin)
    if (
        parsed.scheme.lower() != origin.scheme.lower()
        or parsed.hostname != origin.hostname
        or parsed.port != origin.port
        or re.fullmatch(
            r"/api/imou_life/record_hls/[0-9a-f]{32}/playlist\.m3u8",
            parsed.path,
        )
        is None
        or not parse_qs(parsed.query).get("authSig")
    ):
        raise ValueError("HLS source must be a local proxy playlist")


def _hls_command(source_url: str, output_dir: Path, *, hevc: bool) -> list[str]:
    """Build the video-copy remux that feeds the browser-facing HLS playlist."""
    command = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-loglevel",
        "error",
        "-protocol_whitelist",
        "http,https,tcp,tls,crypto",
        # The proxy playlist starts at the clip's first segment; ffmpeg's
        # default (-3) would jump to the live edge and lose the opening.
        "-live_start_index",
        "0",
        "-i",
        source_url,
        "-c",
        "copy",
    ]
    if hevc:
        # Apple HLS rejects HEVC tagged hev1; ffmpeg copies it as hev1 unless
        # told otherwise.
        command.extend(["-tag:v", "hvc1"])
    command.extend(
        [
            # Cloud clips often carry an audio track that is shorter than the
            # video and has timestamp gaps where the camera sent no audio.
            # Players stop at the audio track's end, so rebuild a continuous
            # timeline (aresample) and pad the tail with silence (apad) up to
            # the video end; without the rebuild the fMP4 muxer drops the
            # padded tail behind the last real audio packet.
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            "-af",
            "aresample=async=1:first_pts=0,apad",
            "-shortest",
            "-f",
            "hls",
            "-hls_time",
            "2",
            "-hls_playlist_type",
            "event",
            "-hls_segment_type",
            "fmp4",
            "-hls_fmp4_init_filename",
            _INIT_FILE,
            "-hls_segment_filename",
            str(output_dir / "seg_%05d.m4s"),
            "-hls_flags",
            "independent_segments+temp_file",
            str(output_dir / _PLAYLIST_FILE),
        ]
    )
    return command


async def _async_stop_process(process: asyncio.subprocess.Process) -> None:
    """Terminate a child process, escalating to kill after a bounded wait."""
    if process.returncode is not None:
        await process.wait()
        return
    try:
        process.terminate()
    except ProcessLookupError:
        await process.wait()
        return
    except OSError:
        process.kill()
        await process.wait()
        return
    try:
        async with asyncio.timeout(_PROCESS_STOP_TIMEOUT):
            await process.wait()
    except TimeoutError:
        process.kill()
        await process.wait()


async def _async_probe_video_codec(source_url: str) -> str | None:
    """Return the first video stream codec, or None when it cannot be probed."""
    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "error",
            "-protocol_whitelist",
            "http,https,tcp,tls,crypto",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "default=nw=1:nk=1",
            source_url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        async with asyncio.timeout(_PROBE_TIMEOUT):
            stdout, _ = await process.communicate()
    except asyncio.CancelledError:
        if process is not None:
            await _async_stop_process(process)
        raise
    except (OSError, TimeoutError):
        if process is not None:
            await _async_stop_process(process)
        return None
    if process.returncode != 0:
        return None
    lines = stdout.decode(errors="replace").strip().splitlines()
    return lines[0].strip() if lines else None


def _remove_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _rename_dir(source: Path, target: Path) -> None:
    _remove_dir(target)
    source.rename(target)


def _dir_ready(path: Path) -> bool:
    return (
        (path / _PLAYLIST_FILE).is_file()
        and (path / _INIT_FILE).is_file()
        and any(path.glob("seg_*.m4s"))
    )


def _job_has_published_segments(job: HlsPlaybackJob) -> bool:
    """Return whether ffmpeg has already written media the player can fetch."""
    for directory in (job.part_dir, job.final_dir):
        if any(directory.glob("seg_*.m4s")):
            return True
    return False


async def _async_clear_incomplete_final(hass: HomeAssistant, final_dir: Path) -> None:
    """Drop a leftover cache that never reached ENDLIST before remuxing again."""
    body = await _async_read_playlist(hass, final_dir / _PLAYLIST_FILE)
    if body is not None and _is_complete(body):
        return
    await hass.async_add_executor_job(_remove_dir, final_dir)


def _prune_playback_cache(
    cache_dir: Path, *, keep_cache_ids: set[str] | None = None
) -> None:
    """Drop stale segment directories, keeping recent complete clips."""
    if not cache_dir.is_dir():
        return
    keep = keep_cache_ids or set()
    now = time.time()
    retained: list[tuple[float, Path]] = []
    for candidate in cache_dir.iterdir():
        if not candidate.is_dir():
            continue
        name = candidate.name
        is_partial = name.endswith(_PART_SUFFIX)
        cache_id = name.removesuffix(_PART_SUFFIX)
        if is_partial:
            # "<cache_id>.<uuid>.part": keep a running remux's output.
            cache_id = cache_id.rsplit(".", 1)[0]
        if cache_id in keep:
            continue
        try:
            mtime = candidate.stat().st_mtime
        except OSError:
            continue
        if is_partial or now - mtime > _CACHE_TTL_SECONDS:
            shutil.rmtree(candidate, ignore_errors=True)
            continue
        retained.append((mtime, candidate))
    if len(retained) > _CACHE_MAX_DIRS:
        retained.sort(key=lambda item: item[0], reverse=True)
        for _mtime, old_dir in retained[_CACHE_MAX_DIRS:]:
            shutil.rmtree(old_dir, ignore_errors=True)


def _has_segments(playlist: str) -> bool:
    return any(line.strip().startswith("#EXTINF:") for line in playlist.splitlines())


def _is_complete(playlist: str) -> bool:
    return "#EXT-X-ENDLIST" in playlist


async def _async_read_playlist(hass: HomeAssistant, path: Path) -> str | None:
    def read() -> str | None:
        try:
            body = path.read_text(encoding="utf-8")
        except OSError:
            return None
        if not body.startswith("#EXTM3U") or "#EXTINF:" not in body:
            return None
        return body

    return await hass.async_add_executor_job(read)


async def _async_run_playback(
    hass: HomeAssistant,
    job: HlsPlaybackJob,
    source_provider: Callable[[], Awaitable[RecordHlsDescriptor]],
) -> None:
    """Remux one recording into an HLS directory, then keep it as the cache."""
    store = _store(hass)
    playlist_url: str | None = None
    try:
        try:
            source = await source_provider()
            _validate_proxy_source(source)
            playlist_url = source.playlist_url
            video_codec = await _async_probe_video_codec(source.playlist_url)
            # ffmpeg opens <part>/init.mp4 itself and never creates directories.
            await hass.async_add_executor_job(
                lambda: job.part_dir.mkdir(parents=True, exist_ok=True)
            )
            process = await asyncio.create_subprocess_exec(
                *_hls_command(
                    source.playlist_url,
                    job.part_dir,
                    hevc=video_codec == "hevc",
                ),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                if job.live_deadline is None:
                    _, stderr = await process.communicate()
                else:
                    remaining = max(0.0, job.live_deadline - time.time())
                    async with asyncio.timeout(remaining):
                        _, stderr = await process.communicate()
            except asyncio.CancelledError:
                await _async_stop_process(process)
                raise
            except TimeoutError as err:
                await _async_stop_process(process)
                raise OSError("recording HLS deadline exceeded") from err
            if process.returncode != 0:
                detail = (stderr or b"").decode(errors="replace").strip()
                raise OSError(detail or f"ffmpeg failed ({process.returncode})")
            if not await hass.async_add_executor_job(_dir_ready, job.part_dir):
                raise OSError("ffmpeg produced no HLS segments")
            await hass.async_add_executor_job(_rename_dir, job.part_dir, job.final_dir)
            active_ids = {
                *store["jobs"],
                *store["owners"],
                job.cache_id,
            }
            await hass.async_add_executor_job(
                lambda: _prune_playback_cache(
                    _cache_dir(hass), keep_cache_ids=active_ids
                )
            )
            async_delete_record_playback_issue(hass, job.entry_id)
        except asyncio.CancelledError:
            await hass.async_add_executor_job(_remove_dir, job.part_dir)
            raise
        except Exception as err:
            _LOGGER.warning("Imou recording %s failed: %s", job.cache_id, err)
            await hass.async_add_executor_job(_remove_dir, job.part_dir)
            async_create_record_playback_issue(hass, job.entry_id, err)
    finally:
        if playlist_url is not None:
            await async_remove_record_hls_proxy_playlist_session(hass, playlist_url)
        job.finished = True
        if job.stop_timer is not None:
            job.stop_timer.cancel()
            job.stop_timer = None
        if store["jobs"].get(job.cache_id) is job:
            store["jobs"].pop(job.cache_id, None)


def _async_stop_idle_job(hass: HomeAssistant, job: HlsPlaybackJob) -> None:
    """Stop an abandoned remux that never published a playable segment."""
    job.stop_timer = None
    if job.finished:
        return
    remaining = _STOP_GRACE_SECONDS - (time.time() - job.last_access)
    if remaining > 0:
        job.stop_timer = hass.loop.call_later(
            remaining, _async_stop_idle_job, hass, job
        )
        return
    # Pause/seek stops playlist polls for a while; once segments exist, keep
    # remuxing so a long clip can still finish into a complete cache.
    if _job_has_published_segments(job):
        return
    store = hass.data.get(DATA_RECORD_PLAYBACK_HLS)
    if not store:
        return
    if store["jobs"].get(job.cache_id) is job:
        store["jobs"].pop(job.cache_id, None)
    if job.task is not None and not job.task.done():
        job.task.cancel()


def _touch_job(hass: HomeAssistant, job: HlsPlaybackJob) -> None:
    job.last_access = time.time()
    if job.stop_timer is not None:
        job.stop_timer.cancel()
    job.stop_timer = hass.loop.call_later(
        _STOP_GRACE_SECONDS, _async_stop_idle_job, hass, job
    )


async def _async_ensure_job(
    hass: HomeAssistant,
    cache_id: str,
    source: PlaybackSource,
) -> HlsPlaybackJob:
    """Return the running remux for this clip, starting one when needed."""
    store = _store(hass)
    if cache_id in store["closed"]:
        raise LookupError(cache_id)
    existing = store["jobs"].get(cache_id)
    if existing is not None and not existing.finished:
        _touch_job(hass, existing)
        return existing
    lock = store["locks"].setdefault(cache_id, asyncio.Lock())
    async with lock:
        if cache_id in store["closed"]:
            raise LookupError(cache_id)
        existing = store["jobs"].get(cache_id)
        if existing is not None and not existing.finished:
            _touch_job(hass, existing)
            return existing
        cache_dir = _cache_dir(hass)
        cache_dir.mkdir(parents=True, exist_ok=True)
        final_dir = _final_dir(hass, cache_id)
        # An incomplete final playlist would otherwise be served while a new
        # remux writes to part/, and the player could stick on stale segments.
        await _async_clear_incomplete_final(hass, final_dir)
        job = HlsPlaybackJob(
            cache_id=cache_id,
            entry_id=source.entry_id,
            part_dir=cache_dir / f"{cache_id}.{uuid.uuid4().hex}{_PART_SUFFIX}",
            final_dir=final_dir,
            live_deadline=(
                time.time() + source.live_timeout_seconds
                if source.live_timeout_seconds is not None
                else None
            ),
        )
        store["jobs"][cache_id] = job
        _touch_job(hass, job)
        job.task = hass.async_create_task(
            _async_run_playback(hass, job, source.provider)
        )
    return job


def _signed_file_url(
    hass: HomeAssistant,
    store: dict[str, Any],
    cache_id: str,
    name: str,
) -> str:
    """Sign one segment URL, reusing it until it is close to expiring."""
    key = (cache_id, name)
    now = time.time()
    cached = store["signed"].get(key)
    if cached is not None and cached[1] > now + 60:
        return cached[0]
    url = async_sign_path(
        hass,
        FILE_URL.format(cache_id=cache_id, name=name),
        timedelta(seconds=_FILE_SIGNED_SECONDS),
        use_content_user=True,
    )
    store["signed"][key] = (url, now + _FILE_SIGNED_SECONDS)
    return url


def _render_playlist(hass: HomeAssistant, cache_id: str, body: str) -> str:
    """Rewrite ffmpeg's relative segment URIs into signed local URLs."""
    store = _store(hass)
    lines: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        map_match = (
            re.search(r'URI="([^"]+)"', stripped)
            if stripped.startswith("#EXT-X-MAP:")
            else None
        )
        if map_match is not None:
            signed = _signed_file_url(
                hass, store, cache_id, _uri_name(map_match.group(1))
            )
            line = re.sub(r'URI="[^"]*"', f'URI="{signed}"', line, count=1)
        elif stripped and not stripped.startswith("#"):
            name = _uri_name(stripped)
            if _SEGMENT_FILE_RE.fullmatch(name):
                line = _signed_file_url(hass, store, cache_id, name)
        lines.append(line)
    return "\n".join(lines) + "\n"


def _uri_name(uri: str) -> str:
    return urlsplit(uri).path.rsplit("/", 1)[-1]


async def _async_wait_for_first_segments(
    hass: HomeAssistant, job: HlsPlaybackJob
) -> str | None:
    """Wait until ffmpeg published the first playable playlist."""
    deadline = time.monotonic() + _FIRST_SEGMENT_TIMEOUT
    while time.monotonic() < deadline:
        for path in (job.part_dir / _PLAYLIST_FILE, job.final_dir / _PLAYLIST_FILE):
            body = await _async_read_playlist(hass, path)
            if body is not None and _has_segments(body):
                return body
        if job.task is not None and job.task.done():
            break
        await asyncio.sleep(_POLL_SECONDS)
    final = await _async_read_playlist(hass, job.final_dir / _PLAYLIST_FILE)
    if final is not None and _has_segments(final):
        return final
    return None


def _find_segment_file(hass: HomeAssistant, cache_id: str, name: str) -> Path | None:
    final = _final_dir(hass, cache_id) / name
    if final.is_file():
        return final
    job = _store(hass)["jobs"].get(cache_id)
    if job is not None and not job.finished:
        part = job.part_dir / name
        if part.is_file():
            return part
    return None


class ImouRecordPlaybackHlsView(HomeAssistantView):
    """Serve the growing or completed fMP4 playlist of one recording."""

    requires_auth = True
    url = PLAYLIST_URL
    name = "api:imou_life_record_playback_hls"
    cors_allowed = True

    async def get(self, request: web.Request, cache_id: str) -> web.Response:
        """Remux the clip when needed and return its HLS playlist."""
        hass = request.app["hass"]
        if "/" in cache_id or "\\" in cache_id or ".." in cache_id:
            raise web.HTTPBadRequest()
        store = _store(hass)
        if cache_id not in store["owners"] or cache_id in store["closed"]:
            raise web.HTTPNotFound()
        body = await _async_read_playlist(
            hass, _final_dir(hass, cache_id) / _PLAYLIST_FILE
        )
        if body is not None and _is_complete(body):
            return self._response(hass, cache_id, body)
        source = store["sources"].get(cache_id)
        if source is None:
            raise web.HTTPNotFound()
        try:
            job = await _async_ensure_job(hass, cache_id, source)
        except LookupError as err:
            raise web.HTTPNotFound() from err
        _touch_job(hass, job)
        body = await _async_wait_for_first_segments(hass, job)
        if body is None:
            raise web.HTTPServiceUnavailable()
        return self._response(hass, cache_id, body)

    def _response(self, hass: HomeAssistant, cache_id: str, body: str) -> web.Response:
        return web.Response(
            text=_render_playlist(hass, cache_id, body),
            content_type="application/vnd.apple.mpegurl",
            headers={"Cache-Control": "no-store"},
        )


class ImouRecordPlaybackFileView(HomeAssistantView):
    """Serve the init and media segments of one recording."""

    requires_auth = True
    url = FILE_URL
    name = "api:imou_life_record_playback_file"
    cors_allowed = True

    async def get(
        self, request: web.Request, cache_id: str, name: str
    ) -> web.StreamResponse:
        """Return one fMP4 file from the live or completed cache."""
        hass = request.app["hass"]
        if (
            "/" in cache_id
            or "\\" in cache_id
            or ".." in cache_id
            or _SEGMENT_FILE_RE.fullmatch(name) is None
        ):
            raise web.HTTPBadRequest()
        store = _store(hass)
        if cache_id not in store["owners"] or cache_id in store["closed"]:
            raise web.HTTPNotFound()
        job = store["jobs"].get(cache_id)
        if job is not None and not job.finished:
            _touch_job(hass, job)
            deadline = time.monotonic() + _FIRST_SEGMENT_TIMEOUT
            while time.monotonic() < deadline:
                path = _find_segment_file(hass, cache_id, name)
                if path is not None:
                    return self._file_response(path, live=True)
                if job.task is not None and job.task.done():
                    break
                await asyncio.sleep(_POLL_SECONDS)
        path = _find_segment_file(hass, cache_id, name)
        if path is None:
            raise web.HTTPNotFound()
        return self._file_response(path, live=False)

    @staticmethod
    def _file_response(path: Path, *, live: bool) -> web.FileResponse:
        return web.FileResponse(
            path,
            headers={
                "Content-Type": "video/mp4",
                "Cache-Control": "no-store" if live else "private, max-age=86400",
            },
        )


async def async_remove_record_playback_sources(
    hass: HomeAssistant, entry_id: str
) -> None:
    """Remove one entry's HLS providers and stop its in-flight remuxes."""
    store = hass.data.get(DATA_RECORD_PLAYBACK_HLS)
    if not store:
        return
    cache_ids = {
        cache_id for cache_id, owner in store["owners"].items() if owner == entry_id
    }
    tasks: list[asyncio.Task[None]] = []
    for cache_id in cache_ids:
        store["closed"].add(cache_id)
        store["owners"].pop(cache_id, None)
        store["sources"].pop(cache_id, None)
    for cache_id, job in list(store["jobs"].items()):
        if job.entry_id != entry_id:
            continue
        cache_ids.add(cache_id)
        store["closed"].add(cache_id)
        store["jobs"].pop(cache_id, None)
        if job.stop_timer is not None:
            job.stop_timer.cancel()
            job.stop_timer = None
        if job.task is not None and not job.task.done():
            job.task.cancel()
            tasks.append(job.task)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    for key in list(store["signed"]):
        if key[0] in cache_ids:
            store["signed"].pop(key, None)

    def remove_cache_dirs() -> None:
        cache_dir = _cache_dir(hass)
        for cache_id in cache_ids:
            shutil.rmtree(cache_dir / cache_id, ignore_errors=True)
            for path in cache_dir.glob(f"{cache_id}.*{_PART_SUFFIX}"):
                shutil.rmtree(path, ignore_errors=True)

    await hass.async_add_executor_job(remove_cache_dirs)
