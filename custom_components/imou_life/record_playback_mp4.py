"""Remux Imou cloud/local playback to MP4 for the media browser."""

from __future__ import annotations

import asyncio
import hashlib
import logging
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
CACHE_SUBDIR = Path("www") / "imou" / "playback"
_REMUX_TIMEOUT = 900
_PROCESS_STOP_TIMEOUT = 5


@dataclass(frozen=True, slots=True)
class PlaybackSource:
    """A recording source provider owned by one config entry."""

    entry_id: str
    provider: Callable[[], Awaitable[RecordHlsDescriptor]]


def _cache_dir(hass: HomeAssistant) -> Path:
    return Path(hass.config.path()) / CACHE_SUBDIR


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


def _remux_command(source_url: str, output: Path) -> list[str]:
    """Build the ffmpeg remux command."""
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-loglevel",
        "error",
    ]
    if ".m3u8" in source_url.split("?", 1)[0].lower():
        cmd.extend(["-allowed_extensions", "ALL"])
    cmd.extend(
        [
            "-i",
            source_url,
            "-c",
            "copy",
            "-f",
            "mp4",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )
    return cmd


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


async def _async_remux_source_to_mp4(source_url: str, output: Path) -> None:
    """Remux one source with a child process that cannot survive cancellation."""
    process = await asyncio.create_subprocess_exec(
        *_remux_command(source_url, output),
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
                try:
                    await _async_remux_source_to_mp4(source.playlist_url, partial)
                except (OSError, TimeoutError):
                    if not _source_is_expired(source):
                        raise
                    partial.unlink(missing_ok=True)
                    try:
                        source = await source_provider()
                    except Exception as refresh_err:
                        async_create_record_playback_issue(hass, entry_id, refresh_err)
                        raise
                    await _async_remux_source_to_mp4(source.playlist_url, partial)
                if not partial.is_file() or partial.stat().st_size < 1:
                    raise OSError("ffmpeg produced an empty recording file")
                partial.replace(output)
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
                _LOGGER.error("Imou recording transcode failed for %s", cache_id)
                raise web.HTTPServiceUnavailable() from err
        if not hass.config.is_allowed_path(str(path)):
            raise web.HTTPNotFound()
        return web.FileResponse(path)
