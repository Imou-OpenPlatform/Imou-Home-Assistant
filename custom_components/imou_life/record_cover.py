"""Serve a recording cover through Home Assistant so the browser can show it."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from functools import partial

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from pyimouapi.pic_decode import PicDecodeError, is_tcm_ability

from .pic_thumbnail import _async_download_picture, _load_decoder, _picture_password
from .runtime_data import get_runtime_data

_LOGGER = logging.getLogger(__name__)

DATA_RECORD_COVERS = "imou_life_record_covers"
COVER_URL = "/api/imou_life/record_cover/{token}"
_MAX_COVERS = 128


@dataclass
class _RecordCover:
    entry_id: str
    device_id: str
    channel_id: str
    url: str
    body: bytes | None = None
    content_type: str | None = None


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
        store[token] = _RecordCover(entry_id, device_id, channel_id, url)
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
    data = await _async_download_picture(hass, cover.url, retry=False)
    if not data:
        return None
    content_type = _image_content_type(data)
    if content_type is not None:
        return data, content_type
    decoded = await _decrypt_cover(hass, cover, data)
    if not decoded:
        return None
    return decoded, _image_content_type(decoded) or "image/jpeg"


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
