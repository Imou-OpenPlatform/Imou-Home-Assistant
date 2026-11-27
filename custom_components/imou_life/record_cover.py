"""Serve a recording cover through Home Assistant so the browser can show it."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from pyimouapi.pic_decode import PicDecodeError, is_tcm_ability

from .pic_thumbnail import (
    _async_try_download_picture,
    _DownloadFailure,
    _load_decoder,
    _picture_password,
)
from .runtime_data import get_runtime_data

_LOGGER = logging.getLogger(__name__)

DATA_RECORD_COVERS = "imou_life_record_covers"
DATA_RECORD_COVER_LISTINGS = "imou_life_record_cover_listings"
DATA_RECORD_COVER_LISTING_TASKS = "imou_life_record_cover_listing_tasks"
COVER_URL = "/api/imou_life/record_cover/{token}"
_MAX_COVERS = 128
# The cover CDN rejects a share of freshly signed URLs with 401 and serves the
# same file under the URL signing from a later listing, so a failed download
# asks the API for a new URL before giving up.
_COVER_REFRESH_RETRIES = 2
# One day listing serves every rejected cover of that device and day, so the
# retries of a media browser page do not each spend an API call.
_COVER_LIST_TTL_SECONDS = 60.0
_RECORD_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
_RECORD_DATE_FORMAT = "%Y-%m-%d"


@dataclass
class _RecordCover:
    entry_id: str
    device_id: str
    channel_id: str
    url: str
    kind: str = ""
    begin_time: str = ""
    end_time: str = ""
    body: bytes | None = None
    content_type: str | None = None


@dataclass
class _CoverListing:
    expires_at: float
    covers: dict[tuple[str, str], str]


def async_register_record_covers(hass: HomeAssistant) -> None:
    """Register the cover view once."""
    if hass.data.get(DATA_RECORD_COVERS) is not None:
        return
    hass.data[DATA_RECORD_COVERS] = {}
    if hass.http is not None:
        hass.http.register_view(ImouRecordCoverView())


def record_cover_thumbnail_url(
    hass: HomeAssistant,
    *,
    entry_id: str,
    device_id: str,
    channel_id: str,
    url: str,
    kind: str = "",
    begin_time: str = "",
    end_time: str = "",
) -> str:
    """Remember one cover and return a browser URL for it."""
    async_register_record_covers(hass)
    token = hashlib.sha256(
        f"{entry_id}|{device_id}|{channel_id}|{url}".encode()
    ).hexdigest()[:32]
    store: dict[str, _RecordCover] = hass.data[DATA_RECORD_COVERS]
    if token not in store:
        while len(store) >= _MAX_COVERS:
            store.pop(next(iter(store)))
        store[token] = _RecordCover(
            entry_id,
            device_id,
            channel_id,
            url,
            kind=kind,
            begin_time=begin_time,
            end_time=end_time,
        )
    return COVER_URL.format(token=token)


def _image_content_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


class ImouRecordCoverView(HomeAssistantView):
    """Download a clip cover and decrypt it when the bytes are not an image."""

    requires_auth = True
    url = COVER_URL
    name = "api:imou_life_record_cover"

    async def get(self, request: web.Request, token: str) -> web.Response:
        """Return one cover image."""
        hass: HomeAssistant = request.app["hass"]
        store: dict[str, _RecordCover] = hass.data.get(DATA_RECORD_COVERS) or {}
        cover = store.get(token)
        if cover is None:
            raise web.HTTPNotFound()
        if cover.body is None or cover.content_type is None:
            image = await _load_cover(hass, cover)
            if image is None:
                raise web.HTTPNotFound()
            cover.body, cover.content_type = image
        return web.Response(
            body=cover.body,
            content_type=cover.content_type,
            headers={"Cache-Control": "private, max-age=3600"},
        )


async def _load_cover(
    hass: HomeAssistant, cover: _RecordCover
) -> tuple[bytes, str] | None:
    data = await _async_download_cover(hass, cover)
    if not data:
        return None
    content_type = _image_content_type(data)
    if content_type is not None:
        return data, content_type
    decoded = await _decrypt_cover(hass, cover, data)
    if not decoded:
        return None
    return decoded, _image_content_type(decoded) or "image/jpeg"


async def _async_download_cover(
    hass: HomeAssistant, cover: _RecordCover
) -> bytes | None:
    """Download the cover, asking for a newly signed URL after a rejection."""
    for _attempt in range(_COVER_REFRESH_RETRIES + 1):
        result = await _async_try_download_picture(hass, cover.url)
        if not isinstance(result, _DownloadFailure):
            return result
        _LOGGER.debug("Recording cover for %s: %s", cover.device_id, result.reason)
        if not result.may_retry or not await _async_refresh_cover_url(hass, cover):
            return None
    return None


async def _async_refresh_cover_url(hass: HomeAssistant, cover: _RecordCover) -> bool:
    """Swap in this clip's cover URL from a freshly signed day listing."""
    if cover.kind != "cloud" or not cover.begin_time or not cover.end_time:
        return False
    covers = await _async_cover_listing(hass, cover)
    if covers is None:
        return False
    thumb_url = covers.get((cover.begin_time, cover.end_time))
    if not thumb_url:
        return False
    cover.url = thumb_url
    return True


async def _async_cover_listing(
    hass: HomeAssistant, cover: _RecordCover
) -> dict[tuple[str, str], str] | None:
    """Return one cached listing of the device's clip covers for that day."""
    day = cover.begin_time[:10]
    key = (cover.entry_id, cover.device_id, cover.channel_id, day)
    listings: dict[tuple[str, str, str, str], _CoverListing] = hass.data.setdefault(
        DATA_RECORD_COVER_LISTINGS, {}
    )
    now = time.monotonic()
    cached = listings.get(key)
    if cached is not None and cached.expires_at > now:
        return cached.covers
    tasks: dict[
        tuple[str, str, str, str], asyncio.Task[dict[tuple[str, str], str] | None]
    ] = hass.data.setdefault(DATA_RECORD_COVER_LISTING_TASKS, {})
    task = tasks.get(key)
    if task is None or task.done():
        task = hass.async_create_task(_async_list_day_covers(hass, cover, day))
        tasks[key] = task
        task.add_done_callback(lambda _task: tasks.pop(key, None))
    covers = await asyncio.shield(task)
    if covers is None:
        return None
    listings[key] = _CoverListing(time.monotonic() + _COVER_LIST_TTL_SECONDS, covers)
    for expired in [
        listing_key
        for listing_key, listing in listings.items()
        if listing.expires_at <= now
    ]:
        listings.pop(expired, None)
    return covers


async def _async_list_day_covers(
    hass: HomeAssistant, cover: _RecordCover, day: str
) -> dict[tuple[str, str], str] | None:
    """List one device/channel day and index its cover URLs by clip."""
    entry = hass.config_entries.async_get_entry(cover.entry_id)
    runtime = get_runtime_data(entry) if entry is not None else None
    if runtime is None:
        return None
    try:
        day_start = datetime.strptime(day, _RECORD_DATE_FORMAT)
    except ValueError:
        return None
    manager = runtime.coordinator.device_manager.delegate
    try:
        segments = await manager.async_list_cloud_record_segments(
            cover.device_id,
            cover.channel_id,
            begin_time=day_start.strftime(_RECORD_TIME_FORMAT),
            end_time=(day_start + timedelta(days=1, minutes=1)).strftime(
                _RECORD_TIME_FORMAT
            ),
        )
    except Exception:
        _LOGGER.debug(
            "Recording cover list failed for %s", cover.device_id, exc_info=True
        )
        return None
    return {
        (segment.begin_time, segment.end_time): segment.thumb_url
        for segment in segments
        if segment.thumb_url
    }


async def _decrypt_cover(
    hass: HomeAssistant, cover: _RecordCover, data: bytes
) -> bytes | None:
    entry = hass.config_entries.async_get_entry(cover.entry_id)
    runtime = get_runtime_data(entry) if entry is not None else None
    if runtime is None or runtime.pic_decoder_failed:
        return None
    decoder = await hass.async_add_executor_job(_load_decoder, runtime, hass)
    if decoder is None:
        return None
    device = runtime.coordinator.get_device(f"{cover.device_id}_{cover.channel_id}")
    ability = device.device_ability if device is not None else ""
    password = _picture_password(runtime, cover.device_id)
    tcm = is_tcm_ability(ability or "")
    if tcm and not password:
        return None
    encrypt_key = password or cover.device_id
    try:
        return await hass.async_add_executor_job(
            partial(
                decoder.decrypt_bytes,
                data,
                device_id=cover.device_id,
                encrypt_key=encrypt_key,
                use_tcm=tcm,
            )
        )
    except PicDecodeError:
        _LOGGER.debug("Recording cover for %s could not be decrypted", cover.device_id)
        return None
