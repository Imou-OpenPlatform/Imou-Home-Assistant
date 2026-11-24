"""Remux Imou cloud/local playback to MP4 for the media browser."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import parse_qs, urlsplit

from aiohttp import web
from aiohttp.abc import AbstractStreamWriter
from aiohttp.web_request import BaseRequest
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

DATA_RECORD_PLAYBACK_MP4 = "imou_life_record_playback_mp4"
PLAYBACK_URL = "/api/imou_life/record_play/{cache_id}.mp4"
STREAM_URL = "/api/imou_life/record_stream/{cache_id}.mp4"
CACHE_SUBDIR = Path("imou_life") / "playback_cache"
_LEGACY_PUBLIC_CACHE_SUBDIR = Path("www") / "imou" / "playback"
RECORD_REMUX_TIMEOUT_SECONDS = 900
_PROCESS_STOP_TIMEOUT = 5
_PROBE_TIMEOUT = 30
_CACHE_TTL_SECONDS = 24 * 3600
_CACHE_MAX_FILES = 32
# The growing fragmented file is streamed while ffmpeg downloads the clip;
# the child is stopped once nobody has been reading it for this long.
_LIVE_SPOOL_SUFFIX = ".live.part.mp4"
_STREAM_STOP_GRACE_SECONDS = 60
_STREAM_POLL_SECONDS = 0.2
_STREAM_CHUNK_BYTES = 256 * 1024
_STREAM_FIRST_BYTES_TIMEOUT = 45
_STREAM_SIGNED_SECONDS = 2 * 3600


@dataclass(frozen=True, slots=True)
class PlaybackSource:
    """A recording source provider owned by one config entry."""

    entry_id: str
    provider: Callable[[], Awaitable[RecordHlsDescriptor]]
    live_timeout_seconds: float | None = None


@dataclass(slots=True)
class LivePlaybackJob:
    """One remux feeding progressive MP4 playback of a recording clip."""

    cache_id: str
    entry_id: str
    spool: Path
    output: Path
    task: asyncio.Task[None] | None = None
    live_deadline: float | None = None
    viewers: int = 0
    finished: bool = False
    stop_timer: asyncio.TimerHandle | None = None


def _store(hass: HomeAssistant) -> dict[str, Any]:
    return hass.data.setdefault(
        DATA_RECORD_PLAYBACK_MP4,
        {
            "closed": set(),
            "locks": {},
            "owners": {},
            "sources": {},
            "tasks": {},
            "streams": {},
            "viewers": {},
        },
    )


def _cache_dir(hass: HomeAssistant) -> Path:
    return Path(hass.config.path()) / CACHE_SUBDIR


def _prune_playback_cache(
    cache_dir: Path,
    *,
    legacy_public_dir: Path | None = None,
    keep_cache_ids: set[str] | None = None,
) -> None:
    """Drop stale/excess cached MP4s and remove any old unauthenticated www copies."""
    if legacy_public_dir is not None and legacy_public_dir.is_dir():
        for stale in legacy_public_dir.glob("*.mp4"):
            stale.unlink(missing_ok=True)
    if not cache_dir.is_dir():
        return
    protected = keep_cache_ids or set()
    now = time.time()
    retained: list[tuple[float, Path]] = []
    for candidate in cache_dir.glob("*.mp4"):
        if candidate.name.endswith(".part.mp4"):
            try:
                spool_mtime = candidate.stat().st_mtime
            except OSError:
                continue
            if now - spool_mtime > _CACHE_TTL_SECONDS:
                candidate.unlink(missing_ok=True)
            continue
        stem = candidate.stem
        if stem in protected:
            continue
        try:
            mtime = candidate.stat().st_mtime
        except OSError:
            continue
        if now - mtime > _CACHE_TTL_SECONDS:
            candidate.unlink(missing_ok=True)
            continue
        retained.append((mtime, candidate))
    if len(retained) > _CACHE_MAX_FILES:
        retained.sort(key=lambda item: item[0], reverse=True)
        for _mtime, old_file in retained[_CACHE_MAX_FILES:]:
            old_file.unlink(missing_ok=True)


def record_playback_cache_id(identifier: str) -> str:
    """Stable file name for one playable recording clip."""
    digest = hashlib.sha256(identifier.encode()).hexdigest()
    return digest[:32]


def record_playback_play_path(cache_id: str) -> str:
    return PLAYBACK_URL.format(cache_id=cache_id)


def async_register_record_playback_mp4(hass: HomeAssistant) -> None:
    """Register playback cache view and ensure the cache folder exists."""
    if hass.data.get(DATA_RECORD_PLAYBACK_MP4):
        return
    cache_dir = _cache_dir(hass)
    cache_dir.mkdir(parents=True, exist_ok=True)
    legacy_dir = Path(hass.config.path()) / _LEGACY_PUBLIC_CACHE_SUBDIR
    hass.async_add_executor_job(
        lambda: _prune_playback_cache(cache_dir, legacy_public_dir=legacy_dir)
    )
    hass.http.register_view(ImouRecordPlaybackMp4View())
    hass.http.register_view(ImouRecordStreamMp4View())
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


def signed_record_playback_url(hass: HomeAssistant, cache_id: str) -> str:
    """Return a short-lived authenticated MP4 URL."""
    return async_sign_path(
        hass,
        record_playback_play_path(cache_id),
        timedelta(hours=2),
        use_content_user=True,
    )


def signed_record_stream_url(hass: HomeAssistant, cache_id: str) -> str:
    """Return an authenticated URL for progressive playback of a local clip."""
    return async_sign_path(
        hass,
        STREAM_URL.format(cache_id=cache_id),
        timedelta(seconds=_STREAM_SIGNED_SECONDS),
        use_content_user=True,
    )


def _validate_proxy_source_url(source_url: str) -> None:
    """Reject anything but a generated, signed local proxy playlist URL."""
    parsed = urlsplit(source_url)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or re.fullmatch(
            r"/api/imou_life/record_hls/[0-9a-f]{32}/playlist\.m3u8",
            parsed.path,
        )
        is None
        or not parse_qs(parsed.query).get("authSig")
        or parsed.fragment
    ):
        raise ValueError("MP4 source must be a generated local HLS proxy URL")


def _copy_options(video_codec: str | None) -> list[str]:
    """Return the stream-copy options shared by every remux."""
    options = ["-c", "copy"]
    if video_codec == "hevc":
        # Browsers expect the hvc1 tag (Home Assistant Stream forces it too);
        # ffmpeg labels copied HEVC as hev1 by default.
        options.extend(["-tag:v", "hvc1"])
    return options


def _remux_command(
    source_url: str, output: Path, *, video_codec: str | None = None
) -> list[str]:
    """Build the ffmpeg remux command for a complete clip."""
    _validate_proxy_source_url(source_url)
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-loglevel",
        "error",
        "-protocol_whitelist",
        "http,https,tcp,tls,crypto",
        "-i",
        source_url,
        *_copy_options(video_codec),
        "-bsf:a",
        "aac_adtstoasc",
        "-f",
        "mp4",
        "-movflags",
        "+faststart",
        str(output),
    ]
    return cmd


def _live_remux_command(
    source_url: str, output: Path, *, video_codec: str | None = None
) -> list[str]:
    """Build the ffmpeg command that muxes a clip while it plays."""
    _validate_proxy_source_url(source_url)
    return [
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
        *_copy_options(video_codec),
        "-bsf:a",
        "aac_adtstoasc",
        "-f",
        "mp4",
        "-movflags",
        "+frag_keyframe+empty_moov+default_base_moof",
        str(output),
    ]


def _finalize_command(
    spool: Path, output: Path, *, video_codec: str | None = None
) -> list[str]:
    """Build the command that adds duration metadata to a finished spool."""
    return [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(spool),
        *_copy_options(video_codec),
        "-f",
        "mp4",
        "-movflags",
        "+faststart",
        str(output),
    ]


def _validate_hls_proxy_source(source: RecordHlsDescriptor) -> None:
    """Require the exact trusted origin used to construct a signed proxy URL."""
    if source.proxy_origin is None:
        raise ValueError("MP4 source must be a local HLS proxy URL")
    parsed = urlsplit(source.playlist_url)
    origin = urlsplit(source.proxy_origin)
    if (
        parsed.scheme.lower() != origin.scheme.lower()
        or parsed.hostname != origin.hostname
        or parsed.port != origin.port
    ):
        raise ValueError("MP4 source must be a local HLS proxy URL")
    _remux_command(source.playlist_url, Path("unused.mp4"))


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


async def _async_remux_source_to_mp4(
    source_url: str, output: Path, *, video_codec: str | None = None
) -> None:
    """Remux one source with a child process that cannot survive cancellation."""
    process = await asyncio.create_subprocess_exec(
        *_remux_command(source_url, output, video_codec=video_codec),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        async with asyncio.timeout(RECORD_REMUX_TIMEOUT_SECONDS):
            _, stderr = await process.communicate()
    except asyncio.CancelledError:
        await _async_stop_process(process)
        raise
    except TimeoutError as err:
        await _async_stop_process(process)
        raise TimeoutError("ffmpeg timed out while preparing recording") from err
    except BaseException:
        await _async_stop_process(process)
        raise
    if process.returncode != 0:
        detail = (stderr or b"").decode(errors="replace").strip()
        msg = detail or f"ffmpeg failed ({process.returncode})"
        raise OSError(msg)


def _signed_url_expiry(source_url: str) -> float | None:
    """Return a signed URL expiry when one is explicitly encoded."""
    now = time.time()
    query = parse_qs(urlsplit(source_url).query)
    for key in ("expire", "expires", "expiry", "exp", "expireTime"):
        values = query.get(key)
        if not values:
            continue
        try:
            value = float(values[0])
        except ValueError:
            continue
        return value if value >= 1_000_000_000 else now + max(0, value)
    return None


def _source_is_expired(source: RecordHlsDescriptor) -> bool:
    """Return whether metadata proves this source is already expired."""
    expiries = [
        expiry
        for expiry in (source.expires_at, _signed_url_expiry(source.playlist_url))
        if expiry is not None
    ]
    return bool(expiries) and time.time() >= min(expiries)


async def async_prepare_record_mp4(
    hass: HomeAssistant,
    cache_id: str,
    entry_id: str,
    source_provider: Callable[[], Awaitable[RecordHlsDescriptor]],
) -> Path:
    """Download/remux once, then reuse the cached MP4."""
    cache_dir = _cache_dir(hass)
    cache_dir.mkdir(parents=True, exist_ok=True)
    output = cache_dir / f"{cache_id}.mp4"
    store = _store(hass)
    if cache_id in store["closed"]:
        raise LookupError(cache_id)
    store["owners"].setdefault(cache_id, entry_id)
    if output.is_file() and output.stat().st_size > 0:
        async_delete_record_playback_issue(hass, entry_id)
        return output
    lock_entry = store["locks"].setdefault(cache_id, (entry_id, asyncio.Lock()))
    lock = lock_entry[1]
    task = asyncio.current_task()
    if task is None:
        msg = "MP4 preparation requires an asyncio task"
        raise RuntimeError(msg)
    tasks = store["tasks"].setdefault(cache_id, set())
    tasks.add(task)
    partial = output.with_name(f"{output.stem}.part.mp4")
    try:
        async with lock:
            try:
                if output.is_file() and output.stat().st_size > 0:
                    async_delete_record_playback_issue(hass, entry_id)
                    return output
                partial.unlink(missing_ok=True)
                _LOGGER.debug("Preparing Imou recording MP4 cache %s", cache_id)
                try:
                    source = await source_provider()
                except Exception as err:
                    async_create_record_playback_issue(hass, entry_id, err)
                    raise
                _validate_hls_proxy_source(source)
                video_codec = await _async_probe_video_codec(source.playlist_url)
                try:
                    await _async_remux_source_to_mp4(
                        source.playlist_url, partial, video_codec=video_codec
                    )
                except (OSError, TimeoutError):
                    if not _source_is_expired(source):
                        raise
                    partial.unlink(missing_ok=True)
                    try:
                        source = await source_provider()
                    except Exception as refresh_err:
                        async_create_record_playback_issue(hass, entry_id, refresh_err)
                        raise
                    _validate_hls_proxy_source(source)
                    video_codec = await _async_probe_video_codec(source.playlist_url)
                    await _async_remux_source_to_mp4(
                        source.playlist_url, partial, video_codec=video_codec
                    )
                if not partial.is_file() or partial.stat().st_size < 1:
                    raise OSError("ffmpeg produced an empty recording file")
                partial.replace(output)
                active_ids = (
                    set(store.get("tasks", {}))
                    | set(store.get("viewers", {}))
                    | set(store.get("streams", {}))
                    | {cache_id}
                )
                await hass.async_add_executor_job(
                    lambda: _prune_playback_cache(cache_dir, keep_cache_ids=active_ids)
                )
                async_delete_record_playback_issue(hass, entry_id)
                return output
            finally:
                partial.unlink(missing_ok=True)
    finally:
        tasks.discard(task)
        if not tasks:
            store["tasks"].pop(cache_id, None)


def _remove_file(path: Path) -> None:
    path.unlink(missing_ok=True)


def _file_ready(path: Path) -> bool:
    return path.is_file() and path.stat().st_size > 0


def _replace_with_spool(spool: Path, output: Path) -> None:
    output.unlink(missing_ok=True)
    spool.replace(output)


async def _async_finalize_live_mp4(
    hass: HomeAssistant,
    spool: Path,
    output: Path,
    *,
    video_codec: str | None = None,
) -> None:
    """Give a finished live spool real duration metadata when possible."""
    await hass.async_add_executor_job(_remove_file, output)
    process: asyncio.subprocess.Process | None = None
    stderr: bytes | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            *_finalize_command(spool, output, video_codec=video_codec),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        async with asyncio.timeout(RECORD_REMUX_TIMEOUT_SECONDS):
            _, stderr = await process.communicate()
    except asyncio.CancelledError:
        if process is not None:
            await _async_stop_process(process)
        raise
    except (OSError, TimeoutError):
        if process is not None:
            await _async_stop_process(process)
        process = None
    if (
        process is None
        or process.returncode != 0
        or not await hass.async_add_executor_job(_file_ready, output)
    ):
        detail = (stderr or b"").decode(errors="replace").strip()
        # The fragmented file already plays; only duration/seek are missing.
        _LOGGER.warning(
            "Imou recording MP4 finalize failed (%s); keeping fragmented file",
            detail or "ffmpeg error",
        )
        await hass.async_add_executor_job(_replace_with_spool, spool, output)


async def _async_run_live_playback(
    hass: HomeAssistant,
    job: LivePlaybackJob,
    source_provider: Callable[[], Awaitable[RecordHlsDescriptor]],
) -> None:
    """Mux one recording while it plays, then keep a seekable MP4 cache."""
    store = _store(hass)
    cache_dir = _cache_dir(hass)
    cache_dir.mkdir(parents=True, exist_ok=True)
    proxy_playlist_url: str | None = None
    try:
        try:
            source = await source_provider()
            _validate_hls_proxy_source(source)
            proxy_playlist_url = source.playlist_url
            video_codec = await _async_probe_video_codec(source.playlist_url)
            process = await asyncio.create_subprocess_exec(
                *_live_remux_command(
                    source.playlist_url,
                    job.spool,
                    video_codec=video_codec,
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
                raise OSError("recording remux deadline exceeded") from err
            if process.returncode != 0:
                detail = (stderr or b"").decode(errors="replace").strip()
                raise OSError(detail or f"ffmpeg failed ({process.returncode})")
            if not job.spool.is_file() or job.spool.stat().st_size < 1:
                raise OSError("ffmpeg produced an empty recording file")
            await _async_finalize_live_mp4(
                hass,
                job.spool,
                job.output,
                video_codec=video_codec,
            )
            active_ids = (
                set(store.get("tasks", {}))
                | set(store.get("viewers", {}))
                | set(store.get("streams", {}))
                | {job.cache_id}
            )
            await hass.async_add_executor_job(
                lambda: _prune_playback_cache(cache_dir, keep_cache_ids=active_ids)
            )
            async_delete_record_playback_issue(hass, job.entry_id)
        except asyncio.CancelledError:
            job.spool.unlink(missing_ok=True)
            raise
        except Exception as err:
            _LOGGER.warning("Imou recording %s failed: %s", job.cache_id, err)
            job.spool.unlink(missing_ok=True)
            async_create_record_playback_issue(hass, job.entry_id, err)
    finally:
        if proxy_playlist_url is not None:
            await async_remove_record_hls_proxy_playlist_session(
                hass, proxy_playlist_url
            )
        job.finished = True
        if job.stop_timer is not None:
            job.stop_timer.cancel()
            job.stop_timer = None
        if store["streams"].get(job.cache_id) is job:
            store["streams"].pop(job.cache_id, None)
        if job.viewers <= 0:
            job.spool.unlink(missing_ok=True)


async def _async_ensure_live_job(
    hass: HomeAssistant,
    cache_id: str,
    entry_id: str,
    source_provider: Callable[[], Awaitable[RecordHlsDescriptor]],
    *,
    live_timeout_seconds: float | None = None,
) -> LivePlaybackJob:
    """Return the running remux for this clip, starting one when needed."""
    store = _store(hass)
    if cache_id in store["closed"]:
        raise LookupError(cache_id)
    existing = store["streams"].get(cache_id)
    if existing is not None and not existing.finished:
        if existing.stop_timer is not None:
            existing.stop_timer.cancel()
            existing.stop_timer = None
        return existing
    cache_dir = _cache_dir(hass)
    cache_dir.mkdir(parents=True, exist_ok=True)
    job = LivePlaybackJob(
        cache_id=cache_id,
        entry_id=entry_id,
        spool=cache_dir / f"{cache_id}.{uuid.uuid4().hex}{_LIVE_SPOOL_SUFFIX}",
        output=cache_dir / f"{cache_id}.mp4",
        live_deadline=(
            time.time() + live_timeout_seconds
            if live_timeout_seconds is not None
            else None
        ),
    )
    store["streams"][cache_id] = job
    job.task = hass.async_create_task(
        _async_run_live_playback(hass, job, source_provider)
    )
    return job


@callback
def _async_cancel_idle_live_job(hass: HomeAssistant, job: LivePlaybackJob) -> None:
    """Stop a remux nobody is reading anymore."""
    job.stop_timer = None
    if job.viewers > 0:
        return
    if job.finished:
        job.spool.unlink(missing_ok=True)
        return
    store = hass.data.get(DATA_RECORD_PLAYBACK_MP4)
    if store and store.get("streams", {}).get(job.cache_id) is job:
        store["streams"].pop(job.cache_id, None)
    if job.task is not None and not job.task.done():
        job.task.cancel()


def _open_spool_prefix(spool: Path) -> tuple[BinaryIO | None, bytes]:
    """Open the growing spool and read whatever bytes already exist."""
    try:
        spool_file = spool.open("rb")
    except FileNotFoundError:
        return None, b""
    try:
        return spool_file, spool_file.read(_STREAM_CHUNK_BYTES)
    except BaseException:
        spool_file.close()
        raise


async def _async_serve_live_mp4(
    hass: HomeAssistant, request: web.Request, job: LivePlaybackJob
) -> web.StreamResponse:
    """Stream a fragmented MP4 as the remux writes it."""
    job.viewers += 1
    if job.stop_timer is not None:
        job.stop_timer.cancel()
        job.stop_timer = None
    spool_file: BinaryIO | None = None
    response: web.StreamResponse | None = None
    try:
        deadline = time.monotonic() + _STREAM_FIRST_BYTES_TIMEOUT
        spool_file, first = await hass.async_add_executor_job(
            _open_spool_prefix, job.spool
        )
        while not first and not job.finished and time.monotonic() < deadline:
            await asyncio.sleep(_STREAM_POLL_SECONDS)
            spool_file, first = await hass.async_add_executor_job(
                _open_spool_prefix, job.spool
            )
        if not first:
            if job.output.is_file() and job.output.stat().st_size > 0:
                return web.FileResponse(job.output)
            raise web.HTTPServiceUnavailable()
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "video/mp4",
                "Cache-Control": "no-store",
                "Accept-Ranges": "none",
            },
        )
        await response.prepare(request)
        await response.write(first)
        assert spool_file is not None
        while True:
            chunk = await hass.async_add_executor_job(
                spool_file.read, _STREAM_CHUNK_BYTES
            )
            if chunk:
                await response.write(chunk)
                continue
            if job.finished:
                break
            await asyncio.sleep(_STREAM_POLL_SECONDS)
        await response.write_eof()
        return response
    except ConnectionResetError:
        if response is not None:
            return response
        raise
    finally:
        if spool_file is not None:
            await hass.async_add_executor_job(spool_file.close)
        job.viewers -= 1
        if job.viewers <= 0:
            if job.finished:
                job.spool.unlink(missing_ok=True)
            else:
                job.stop_timer = hass.loop.call_later(
                    _STREAM_STOP_GRACE_SECONDS,
                    _async_cancel_idle_live_job,
                    hass,
                    job,
                )


async def async_remove_record_playback_sources(
    hass: HomeAssistant, entry_id: str
) -> None:
    """Remove one entry's MP4 providers and stop its in-flight remuxes."""
    store = hass.data.get(DATA_RECORD_PLAYBACK_MP4)
    if not store:
        return
    cache_ids = {
        cache_id for cache_id, owner in store["owners"].items() if owner == entry_id
    }
    for cache_id in cache_ids:
        store["closed"].add(cache_id)
        store["owners"].pop(cache_id, None)
        store["sources"].pop(cache_id, None)
    current = asyncio.current_task()
    tasks = {
        task
        for cache_id in cache_ids
        for task in store["tasks"].get(cache_id, set())
        if task is not current
    }
    streams = store.get("streams", {})
    live_jobs: list[LivePlaybackJob] = []
    for cache_id, job in list(streams.items()):
        if job.entry_id != entry_id:
            continue
        cache_ids.add(cache_id)
        store["closed"].add(cache_id)
        streams.pop(cache_id, None)
        live_jobs.append(job)
        if job.stop_timer is not None:
            job.stop_timer.cancel()
            job.stop_timer = None
        if job.task is not None and not job.task.done():
            job.task.cancel()
            tasks.add(job.task)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    for cache_id in cache_ids:
        store["locks"].pop(cache_id, None)
        store["tasks"].pop(cache_id, None)

    def remove_cached_files() -> None:
        cache_dir = _cache_dir(hass)
        for cache_id in cache_ids:
            (cache_dir / f"{cache_id}.mp4").unlink(missing_ok=True)
            (cache_dir / f"{cache_id}.part.mp4").unlink(missing_ok=True)
            (cache_dir / f"{cache_id}{_LIVE_SPOOL_SUFFIX}").unlink(missing_ok=True)
        for job in live_jobs:
            job.spool.unlink(missing_ok=True)

    await hass.async_add_executor_job(remove_cached_files)


def _async_begin_playback_viewer(hass: HomeAssistant, cache_id: str) -> None:
    viewers = _store(hass)["viewers"]
    viewers[cache_id] = viewers.get(cache_id, 0) + 1


def _async_end_playback_viewer(hass: HomeAssistant, cache_id: str) -> None:
    viewers = _store(hass)["viewers"]
    remaining = viewers.get(cache_id, 0) - 1
    if remaining > 0:
        viewers[cache_id] = remaining
    else:
        viewers.pop(cache_id, None)


class _TrackedFileResponse(web.FileResponse):
    """Keep a cached clip protected until its response finishes."""

    def __init__(self, hass: HomeAssistant, cache_id: str, path: Path) -> None:
        super().__init__(path)
        self._hass = hass
        self._cache_id = cache_id
        self._released = False

    def _release(self) -> None:
        if not self._released:
            self._released = True
            _async_end_playback_viewer(self._hass, self._cache_id)

    async def prepare(self, request: BaseRequest) -> AbstractStreamWriter | None:
        try:
            return await super().prepare(request)
        except BaseException:
            self._release()
            raise

    async def write_eof(self, data: bytes = b"") -> None:
        try:
            await super().write_eof(data)
        finally:
            self._release()


class ImouRecordPlaybackMp4View(HomeAssistantView):
    """Serve a cached clip or stream it while ffmpeg remuxes."""

    requires_auth = True
    url = PLAYBACK_URL
    name = "api:imou_life_record_play"
    cors_allowed = True

    async def get(self, request: web.Request, cache_id: str) -> web.StreamResponse:
        """Stream one MP4 from the cache or a growing remux spool."""
        hass = request.app["hass"]
        if "/" in cache_id or "\\" in cache_id or ".." in cache_id:
            raise web.HTTPBadRequest()
        store = hass.data.get(DATA_RECORD_PLAYBACK_MP4) or {}
        if cache_id not in store.get("owners", {}):
            raise web.HTTPNotFound()
        path = _cache_dir(hass) / f"{cache_id}.mp4"
        if not path.is_file() or path.stat().st_size == 0:
            source = store.get("sources", {}).get(cache_id)
            if not source:
                raise web.HTTPNotFound()
            try:
                job = await _async_ensure_live_job(
                    hass,
                    cache_id,
                    source.entry_id,
                    source.provider,
                    live_timeout_seconds=source.live_timeout_seconds,
                )
            except LookupError as err:
                raise web.HTTPNotFound() from err
            return await _async_serve_live_mp4(hass, request, job)
        if not path.is_file() or path.stat().st_size == 0:
            raise web.HTTPNotFound()
        _async_begin_playback_viewer(hass, cache_id)
        return _TrackedFileResponse(hass, cache_id, path)


class ImouRecordStreamMp4View(HomeAssistantView):
    """Stream a local recording while it is muxed into progressive MP4."""

    requires_auth = True
    url = STREAM_URL
    name = "api:imou_life_record_stream"
    cors_allowed = True

    async def get(self, request: web.Request, cache_id: str) -> web.StreamResponse:
        """Serve the cached MP4 or start live muxing for this clip."""
        hass = request.app["hass"]
        if "/" in cache_id or "\\" in cache_id or ".." in cache_id:
            raise web.HTTPBadRequest()
        store = _store(hass)
        if cache_id not in store["owners"]:
            raise web.HTTPNotFound()
        path = _cache_dir(hass) / f"{cache_id}.mp4"
        if path.is_file() and path.stat().st_size > 0:
            _async_begin_playback_viewer(hass, cache_id)
            return _TrackedFileResponse(hass, cache_id, path)
        if cache_id in store["closed"]:
            raise web.HTTPNotFound()
        source = store["sources"].get(cache_id)
        if not source:
            raise web.HTTPNotFound()
        try:
            job = await _async_ensure_live_job(
                hass,
                cache_id,
                source.entry_id,
                source.provider,
                live_timeout_seconds=source.live_timeout_seconds,
            )
        except LookupError as err:
            raise web.HTTPNotFound() from err
        return await _async_serve_live_mp4(hass, request, job)
