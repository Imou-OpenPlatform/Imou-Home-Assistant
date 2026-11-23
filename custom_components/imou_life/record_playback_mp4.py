"""Remux Imou cloud/local playback to MP4 for the media browser."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant, callback

from .record_hls_proxy import RecordHlsDescriptor
from .repairs import (
    async_create_record_playback_issue,
    async_delete_record_playback_issue,
)

_LOGGER = logging.getLogger(__name__)

DATA_RECORD_PLAYBACK_MP4 = "imou_life_record_playback_mp4"
PLAYBACK_URL = "/api/imou_life/record_play/{cache_id}.mp4"
CACHE_SUBDIR = Path("imou_life") / "playback_cache"
_LEGACY_PUBLIC_CACHE_SUBDIR = Path("www") / "imou" / "playback"
_REMUX_TIMEOUT = 900
_PROCESS_STOP_TIMEOUT = 5
_PROBE_TIMEOUT = 30
_CACHE_TTL_SECONDS = 24 * 3600
_CACHE_MAX_FILES = 32


@dataclass(frozen=True, slots=True)
class PlaybackSource:
    """A recording source provider owned by one config entry."""

    entry_id: str
    provider: Callable[[], Awaitable[RecordHlsDescriptor]]


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
    hass.data[DATA_RECORD_PLAYBACK_MP4] = {
        "closed": set(),
        "locks": {},
        "owners": {},
        "sources": {},
        "tasks": {},
    }


@callback
def register_playback_source(
    hass: HomeAssistant,
    cache_id: str,
    entry_id: str,
    source_provider: Callable[[], Awaitable[RecordHlsDescriptor]],
) -> None:
    """Remember how to request a stream URL until the MP4 cache file exists."""
    store = hass.data.setdefault(
        DATA_RECORD_PLAYBACK_MP4,
        {"closed": set(), "locks": {}, "owners": {}, "sources": {}, "tasks": {}},
    )
    store["closed"].discard(cache_id)
    store["owners"][cache_id] = entry_id
    store["sources"][cache_id] = PlaybackSource(entry_id, source_provider)


def signed_record_playback_url(hass: HomeAssistant, cache_id: str) -> str:
    """Return a short-lived authenticated MP4 URL."""
    return async_sign_path(
        hass,
        record_playback_play_path(cache_id),
        timedelta(hours=2),
        use_content_user=True,
    )


def _remux_command(
    source_url: str, output: Path, *, video_codec: str | None = None
) -> list[str]:
    """Build the ffmpeg remux command."""
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
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-loglevel",
        "error",
    ]
    cmd.extend(
        [
            "-protocol_whitelist",
            "http,https,tcp,tls,crypto",
            "-i",
            source_url,
            "-c",
            "copy",
        ]
    )
    if video_codec == "hevc":
        # Browsers expect the hvc1 tag (Home Assistant Stream forces it too);
        # ffmpeg labels copied HEVC as hev1 by default.
        cmd.extend(["-tag:v", "hvc1"])
    cmd.extend(
        [
            "-bsf:a",
            "aac_adtstoasc",
            "-f",
            "mp4",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )
    return cmd


def _validate_local_hls_proxy_source(source: RecordHlsDescriptor) -> None:
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
        async with asyncio.timeout(_REMUX_TIMEOUT):
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
    store = hass.data.setdefault(
        DATA_RECORD_PLAYBACK_MP4,
        {"closed": set(), "locks": {}, "owners": {}, "sources": {}, "tasks": {}},
    )
    if cache_id in store["closed"]:
        raise LookupError(cache_id)
    store["owners"].setdefault(cache_id, entry_id)
    if output.is_file() and output.stat().st_size > 0:
        async_delete_record_playback_issue(hass, entry_id)
        store["sources"].pop(cache_id, None)
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
                    store["sources"].pop(cache_id, None)
                    return output
                partial.unlink(missing_ok=True)
                _LOGGER.debug("Preparing Imou recording MP4 cache %s", cache_id)
                try:
                    source = await source_provider()
                except Exception as err:
                    async_create_record_playback_issue(hass, entry_id, err)
                    raise
                _validate_local_hls_proxy_source(source)
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
                    _validate_local_hls_proxy_source(source)
                    video_codec = await _async_probe_video_codec(source.playlist_url)
                    await _async_remux_source_to_mp4(
                        source.playlist_url, partial, video_codec=video_codec
                    )
                if not partial.is_file() or partial.stat().st_size < 1:
                    raise OSError("ffmpeg produced an empty recording file")
                partial.replace(output)
                active_ids = set(store.get("tasks", {})) | {cache_id}
                await hass.async_add_executor_job(
                    lambda: _prune_playback_cache(cache_dir, keep_cache_ids=active_ids)
                )
                async_delete_record_playback_issue(hass, entry_id)
                store["sources"].pop(cache_id, None)
                return output
            finally:
                partial.unlink(missing_ok=True)
    finally:
        tasks.discard(task)
        if not tasks:
            store["tasks"].pop(cache_id, None)


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

    await hass.async_add_executor_job(remove_cached_files)


class ImouRecordPlaybackMp4View(HomeAssistantView):
    """Serve a cached remuxed recording clip."""

    requires_auth = True
    url = PLAYBACK_URL
    name = "api:imou_life_record_play"
    cors_allowed = True

    async def get(self, request: web.Request, cache_id: str) -> web.StreamResponse:
        """Stream one mp4 from the cache directory."""
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
                path = await async_prepare_record_mp4(
                    hass, cache_id, source.entry_id, source.provider
                )
            except LookupError as err:
                raise web.HTTPNotFound() from err
            except Exception as err:
                _LOGGER.error("Imou recording remux failed for %s: %s", cache_id, err)
                raise web.HTTPServiceUnavailable() from err
        if not path.is_file() or path.stat().st_size == 0:
            raise web.HTTPNotFound()
        return web.FileResponse(path)
