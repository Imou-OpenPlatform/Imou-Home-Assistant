"""Remux Imou cloud/local playback to MP4 for the media browser."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
import subprocess
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import Path

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant, callback

from .repairs import (
    async_create_record_playback_issue,
    async_delete_record_playback_issue,
)

_LOGGER = logging.getLogger(__name__)

DATA_RECORD_PLAYBACK_MP4 = "imou_life_record_playback_mp4"
PLAYBACK_URL = "/api/imou_life/record_play/{cache_id}.mp4"
CACHE_SUBDIR = Path("www") / "imou" / "playback"
_REMUX_TIMEOUT = 900


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
    hass.data[DATA_RECORD_PLAYBACK_MP4] = {"locks": {}, "sources": {}}


@callback
def register_playback_source(
    hass: HomeAssistant,
    cache_id: str,
    entry_id: str,
    source_provider: Callable[[], Awaitable[str]],
) -> None:
    """Remember how to request a stream URL until the MP4 cache file exists."""
    store = hass.data.setdefault(DATA_RECORD_PLAYBACK_MP4, {"locks": {}, "sources": {}})
    store["sources"][cache_id] = (entry_id, source_provider)


def signed_record_playback_url(hass: HomeAssistant, cache_id: str) -> str:
    """Return a short-lived authenticated MP4 URL."""
    return async_sign_path(
        hass,
        record_playback_play_path(cache_id),
        timedelta(hours=2),
        use_content_user=True,
    )


def _remux_source_to_mp4(source_url: str, output: Path) -> None:
    if shutil.which("ffmpeg") is None:
        msg = "ffmpeg is not installed"
        raise OSError(msg)
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
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            timeout=_REMUX_TIMEOUT,
        )
    except subprocess.TimeoutExpired as err:
        raise TimeoutError("ffmpeg timed out while preparing recording") from err
    if completed.returncode != 0:
        detail = (completed.stderr or b"").decode(errors="replace").strip()
        msg = detail or f"ffmpeg failed ({completed.returncode})"
        raise OSError(msg)
    if not output.is_file() or output.stat().st_size < 1:
        raise OSError("ffmpeg produced an empty recording file")


def _is_expired_input_error(error: BaseException) -> bool:
    """Return whether ffmpeg rejected an expired or unauthorized input URL."""
    text = str(error).lower()
    return any(
        marker in text
        for marker in (
            "401 unauthorized",
            "403 forbidden",
            "404 not found",
            "http error 401",
            "http error 403",
            "http error 404",
            "input url expired",
            "url has expired",
        )
    )


async def async_prepare_record_mp4(
    hass: HomeAssistant,
    cache_id: str,
    entry_id: str,
    source_provider: Callable[[], Awaitable[str]],
) -> Path:
    """Download/remux once, then reuse the cached MP4."""
    cache_dir = _cache_dir(hass)
    cache_dir.mkdir(parents=True, exist_ok=True)
    output = cache_dir / f"{cache_id}.mp4"
    if output.is_file() and output.stat().st_size > 0:
        async_delete_record_playback_issue(hass, entry_id)
        hass.data.get(DATA_RECORD_PLAYBACK_MP4, {}).get("sources", {}).pop(
            cache_id, None
        )
        return output
    store = hass.data.setdefault(DATA_RECORD_PLAYBACK_MP4, {"locks": {}, "sources": {}})
    locks: dict[str, asyncio.Lock] = store["locks"]
    lock = locks.setdefault(cache_id, asyncio.Lock())
    async with lock:
        if output.is_file() and output.stat().st_size > 0:
            async_delete_record_playback_issue(hass, entry_id)
            store["sources"].pop(cache_id, None)
            return output
        partial = output.with_name(f"{output.stem}.part.mp4")
        partial.unlink(missing_ok=True)
        _LOGGER.debug("Preparing Imou recording MP4 cache %s", cache_id)
        try:
            try:
                source_url = await source_provider()
            except Exception as err:
                async_create_record_playback_issue(hass, entry_id, err)
                raise
            try:
                await hass.async_add_executor_job(
                    _remux_source_to_mp4, source_url, partial
                )
            except (OSError, TimeoutError) as err:
                if not _is_expired_input_error(err):
                    async_create_record_playback_issue(hass, entry_id, err)
                    raise
                partial.unlink(missing_ok=True)
                try:
                    source_url = await source_provider()
                except Exception as refresh_err:
                    async_create_record_playback_issue(hass, entry_id, refresh_err)
                    raise
                try:
                    await hass.async_add_executor_job(
                        _remux_source_to_mp4, source_url, partial
                    )
                except (OSError, TimeoutError) as final_err:
                    async_create_record_playback_issue(hass, entry_id, final_err)
                    raise
            partial.replace(output)
            async_delete_record_playback_issue(hass, entry_id)
            store["sources"].pop(cache_id, None)
            return output
        finally:
            partial.unlink(missing_ok=True)


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
        path = _cache_dir(hass) / f"{cache_id}.mp4"
        if not path.is_file() or path.stat().st_size == 0:
            store = hass.data.get(DATA_RECORD_PLAYBACK_MP4) or {}
            source = store.get("sources", {}).get(cache_id)
            if not source:
                raise web.HTTPNotFound()
            entry_id, source_provider = source
            try:
                path = await async_prepare_record_mp4(
                    hass, cache_id, entry_id, source_provider
                )
            except Exception as err:
                _LOGGER.error("Imou recording transcode failed for %s", cache_id)
                raise web.HTTPServiceUnavailable() from err
        if not hass.config.is_allowed_path(str(path)):
            raise web.HTTPNotFound()
        return web.FileResponse(path)
