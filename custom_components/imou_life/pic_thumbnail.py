"""Decrypt alarm push thumbnails and write them under www for Companion notify."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from pyimouapi.pic_decode import (
    LCOpenPicDecoder,
    PicDecodeError,
    is_tcm_ability,
    resolve_encrypt_key,
)
from pyimouapi.push import pic_urls_from_payload, preferred_pic_url

from .const import (
    PARAM_API_URL,
    PARAM_APP_ID,
    PARAM_APP_SECRET,
    PARAM_ATTACH_DECRYPTED_THUMBNAIL,
    PARAM_DEFAULT_DEVICE_PASSWORD,
    PARAM_DEVICE_PASSWORDS,
    imou_life_device_keys_from_ids,
)

if TYPE_CHECKING:
    from .runtime_data import ImouRuntimeData

_LOGGER = logging.getLogger(__name__)

_THUMB_SUBDIR = Path("imou_life") / "thumbs"
_THUMB_MAX_AGE_SECONDS = 24 * 60 * 60
_NATIVE_API_PORT = 443
_DECRYPT_TIMEOUT_SECONDS = 10
_PIC_DECODER_INIT_LOCK = threading.Lock()


def password_for_device(options: Mapping[str, Any], device_id: str) -> str | None:
    """Resolve per-serial password from entry options."""
    passwords = options.get(PARAM_DEVICE_PASSWORDS)
    if isinstance(passwords, dict):
        per_device = passwords.get(device_id)
        if isinstance(per_device, str) and per_device:
            return per_device
    default = options.get(PARAM_DEFAULT_DEVICE_PASSWORD)
    if isinstance(default, str) and default:
        return default
    return None


def _device_for_event(
    runtime: ImouRuntimeData, event_data: dict[str, Any]
) -> Any | None:
    device_id = event_data.get("device_id")
    if not device_id:
        return None
    devices_by_key = runtime.coordinator.devices_by_key
    for key in imou_life_device_keys_from_ids(
        device_id,
        event_data.get("channel_id"),
        event_data.get("product_id"),
    ):
        device = devices_by_key.get(key)
        if device is not None:
            return device
    for device in devices_by_key.values():
        if device.device_id == device_id:
            return device
    return None


def _thumb_filename(alarm_id: str | None, pic_url: str) -> str:
    if alarm_id:
        return f"{alarm_id}.jpg"
    digest = hashlib.sha256(pic_url.encode()).hexdigest()[:16]
    return f"{digest}.jpg"


def _prune_old_thumbs(thumbs_dir: Path) -> None:
    cutoff = time.time() - _THUMB_MAX_AGE_SECONDS
    try:
        for path in thumbs_dir.iterdir():
            if not path.is_file():
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                _LOGGER.debug("Could not prune old thumb %s", path.name)
    except OSError:
        _LOGGER.debug("Could not list thumb directory %s", thumbs_dir)


def _sync_decrypt_and_write(
    runtime: ImouRuntimeData,
    entry: ConfigEntry,
    hass: HomeAssistant,
    event_data: dict[str, Any],
    pic_url: str,
    encrypt_key: str,
    device_id: str,
    use_tcm: bool,
    token: str,
) -> str | None:
    native_dir = Path(hass.config.path("imou_life", "native"))
    try:
        with _PIC_DECODER_INIT_LOCK:
            if runtime.pic_decoder is None:
                runtime.pic_decoder = LCOpenPicDecoder(native_dir)
            decoder = runtime.pic_decoder
            if not runtime.pic_decoder_initialized:
                decoder.load()
                host = str(entry.data[PARAM_API_URL])
                decoder.init_open_api(
                    host,
                    _NATIVE_API_PORT,
                    entry.data[PARAM_APP_ID],
                    entry.data[PARAM_APP_SECRET],
                )
                runtime.pic_decoder_initialized = True
    except Exception:
        runtime.pic_decoder_failed = True
        _LOGGER.warning(
            "LCOpenSDK native libs failed to load from %s; skipping decrypt",
            native_dir,
            exc_info=True,
        )
        return None

    try:
        jpeg = decoder.decrypt_picture(
            pic_url=pic_url,
            encrypt_key=encrypt_key,
            device_id=device_id,
            token=token,
            use_tcm=use_tcm,
        )
    except PicDecodeError as err:
        _LOGGER.debug(
            "DecryptPicture failed for device %s (code=%s)",
            device_id,
            err.code,
        )
        return None
    except Exception:
        _LOGGER.warning(
            "DecryptPicture failed for device %s",
            device_id,
            exc_info=True,
        )
        return None

    www_dir = Path(hass.config.path("www"))
    www_existed = www_dir.is_dir()
    thumbs_dir = www_dir / _THUMB_SUBDIR
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    if not www_existed:
        _LOGGER.warning(
            "Created %s; restart Home Assistant for /local/ to serve alarm thumbnails",
            www_dir,
        )
    _prune_old_thumbs(thumbs_dir)
    filename = _thumb_filename(event_data.get("alarm_id"), pic_url)
    dest = thumbs_dir / filename
    try:
        dest.write_bytes(jpeg)
    except OSError:
        _LOGGER.warning("Could not write alarm thumb %s", dest, exc_info=True)
        return None

    return f"/local/{_THUMB_SUBDIR.as_posix()}/{filename}"


async def async_maybe_decrypt_thumbnail(
    hass: HomeAssistant,
    entry: ConfigEntry,
    runtime: ImouRuntimeData,
    event_data: dict[str, Any],
) -> str | None:
    """Decrypt push image when enabled; return /local/... URL or None."""
    if not entry.options.get(PARAM_ATTACH_DECRYPTED_THUMBNAIL):
        return None
    if runtime.pic_decoder_failed:
        return None

    raw = event_data.get("raw")
    if not isinstance(raw, dict):
        return None

    pic_urls = pic_urls_from_payload(raw)
    pic_url = preferred_pic_url(pic_urls)
    if not pic_url:
        return None

    device_id = event_data.get("device_id")
    if not device_id:
        return None

    device = _device_for_event(runtime, event_data)
    device_ability = device.device_ability if device is not None else ""
    is_tcm = is_tcm_ability(device_ability)
    device_password = password_for_device(entry.options, str(device_id))
    encrypt_key = resolve_encrypt_key(
        is_tcm=is_tcm,
        device_id=str(device_id),
        device_password=device_password,
    )
    if encrypt_key is None:
        if is_tcm:
            _LOGGER.debug(
                "Skipping TCM decrypt for %s: no device password configured",
                device_id,
            )
        return None

    token = event_data.get("token")
    token_str = str(token) if token else ""

    try:
        return await asyncio.wait_for(
            hass.async_add_executor_job(
                _sync_decrypt_and_write,
                runtime,
                entry,
                hass,
                event_data,
                pic_url,
                encrypt_key,
                str(device_id),
                is_tcm,
                token_str,
            ),
            timeout=_DECRYPT_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        _LOGGER.warning(
            "Alarm thumbnail decrypt timed out for device %s",
            device_id,
        )
        return None
