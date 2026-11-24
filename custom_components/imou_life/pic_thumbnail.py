"""Decrypt alarm push thumbnails and write them to authenticated media storage."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import platform
import re
import sys
import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple
from urllib.parse import urlsplit

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.network import NoURLAvailableError, get_url
from pyimouapi.pic_decode import (
    CLIENT_LIB,
    SDK_LIB,
    LCOpenPicDecoder,
    PicDecodeError,
    is_tcm_ability,
)
from pyimouapi.play_token import login_password_from_play_token
from pyimouapi.push import alarm_picture_url

from .const import (
    imou_life_device_key_from_ids,
    imou_life_device_keys_from_ids,
)
from .helpers import fill_template, selector_option_label
from .media_view import (
    URL_PREFIX as MEDIA_URL_PREFIX,
)
from .media_view import (
    media_file_path,
    media_root,
    media_url_for_path,
    signed_media_path,
)
from .record_http_fetch import (
    UrlFetchBodyTooLargeError,
    UrlFetchUnsafeError,
    async_fetch_url_response,
)

if TYPE_CHECKING:
    from .runtime_data import ImouRuntimeData

_LOGGER = logging.getLogger(__name__)

_THUMB_SUBDIR = Path("imou_life") / "thumbs"
_THUMB_MAX_AGE_SECONDS = 24 * 60 * 60
# Push alarm ids become media filenames; reject path separators and traversal.
_SAFE_THUMB_STEM = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_DECRYPT_TIMEOUT_SECONDS = 30
_DOWNLOAD_TIMEOUT_SECONDS = 20
_MAX_PICTURE_BYTES = 20 * 1024 * 1024
# The alarm notification waits on the download, so this budget stays short.
_DOWNLOAD_RETRY_DELAYS = (1.0, 2.0, 4.0)
_SDK_CODE_WRONG_KEY = 2
_SDK_CODE_HINT = {
    1: "truncated or corrupt picture data",
    2: "wrong key",
    3: "not encrypted",
    4: "unsupported encryption",
    5: "buffer too small",
}
_PIC_DECODER_INIT_LOCK = threading.Lock()

NATIVE_CLIENT_SO = CLIENT_LIB
NATIVE_SDK_SO = SDK_LIB
_SUPPORTED_MACHINES = frozenset({"x86_64", "amd64"})


def native_lib_dir(hass: HomeAssistant) -> Path:
    """Return the folder where official Image Decryption Demo libraries go."""
    return Path(hass.config.path("imou_life", "native"))


def last_alarm_image_path(hass: HomeAssistant, device_key: str) -> Path | None:
    """Return the on-disk last still for a camera, outside /local/."""
    stem = (
        device_key
        if _SAFE_THUMB_STEM.fullmatch(device_key)
        else hashlib.sha256(device_key.encode()).hexdigest()[:16]
    )
    dest_dir = Path(hass.config.path("imou_life", "last_alarm"))
    dest = (dest_dir / f"{stem}.jpg").resolve()
    if not dest.is_relative_to(dest_dir.resolve()):
        return None
    return dest


def persist_last_alarm_image(hass: HomeAssistant, device_key: str, jpeg: bytes) -> None:
    """Keep the last decrypted still for the dashboard image entity."""
    dest = last_alarm_image_path(hass, device_key)
    if dest is None:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        dest.write_bytes(jpeg)
    except OSError:
        _LOGGER.debug("Could not persist last alarm image for %s", device_key)


def read_last_alarm_image(
    hass: HomeAssistant, device_key: str
) -> tuple[bytes, datetime] | None:
    """Return the persisted last still and its mtime, if present."""
    path = last_alarm_image_path(hass, device_key)
    if path is None or not path.is_file():
        return None
    try:
        data = path.read_bytes()
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except OSError:
        return None
    if not data:
        return None
    return data, mtime


def jpeg_from_local_url(hass: HomeAssistant, local_url: str) -> bytes | None:
    """Read a jpeg previously written by this integration, or None if gone."""
    path = media_file_path(hass, local_url)
    if path is None:
        return None
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return data or None


def adopt_local_thumb(
    hass: HomeAssistant, device_key: str, local_url: str
) -> bytes | None:
    """Copy a /local/ still onto the last-alarm file; return jpeg bytes."""
    jpeg = jpeg_from_local_url(hass, local_url)
    if not jpeg:
        return None
    persist_last_alarm_image(hass, device_key, jpeg)
    return jpeg


def native_platform_label() -> str:
    """Return a short os/arch string for UI and logs."""
    return f"{sys.platform} {platform.machine()}"


def native_platform_supported() -> bool:
    """Official Demo libraries are linux x86-64 only."""
    return sys.platform.startswith("linux") and (
        platform.machine().lower() in _SUPPORTED_MACHINES
    )


def native_support_status(hass: HomeAssistant, language: str) -> str:
    """Return a localized sentence: supported, or explicitly not supported."""
    if native_platform_supported():
        return selector_option_label(
            hass,
            language,
            "native_hint",
            "supported",
            "supported (linux x86-64)",
        )
    fallback = "not supported (needs linux x86-64; this host is {arch})"
    template = selector_option_label(
        hass, language, "native_hint", "unsupported", fallback
    )
    return fill_template(template, fallback, arch=native_platform_label())


def _redact_picture_url(url: str) -> str:
    """Keep only a validated authority when a picture URL is logged."""
    if any(not character.isprintable() or character.isspace() for character in url):
        return "***"
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        return "***"
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or "%" in hostname
        or parsed.username is not None
        or parsed.password is not None
        or port == 0
    ):
        return "***"
    host = f"[{hostname}]" if ":" in hostname else hostname
    authority = f"{host}:{port}" if port is not None else host
    return f"{parsed.scheme}://{authority}"


def native_libs_found(hass: HomeAssistant) -> int:
    """Return how many of the two required .so files exist (0 to 2)."""
    native_dir = native_lib_dir(hass)
    return sum(
        1 for name in (NATIVE_CLIENT_SO, NATIVE_SDK_SO) if (native_dir / name).is_file()
    )


def native_libs_present(hass: HomeAssistant) -> bool:
    """Return whether both official Demo native libraries exist."""
    return native_libs_found(hass) == 2


def native_libraries_hint(hass: HomeAssistant, language: str) -> str:
    """Return one line about the decrypt libraries for the options form.

    Spell out the filenames and the folder only while something is missing;
    a host that is already set up does not need the install instructions.
    """
    if not native_platform_supported():
        return native_support_status(hass, language)
    found = native_libs_found(hass)
    if found == 2:
        return selector_option_label(
            hass,
            language,
            "native_hint",
            "ready",
            "decrypt libraries ready (linux x86-64)",
        )
    native_dir = native_lib_dir(hass)
    fallback = (
        "decrypt libraries missing ({found}/2 found). Copy {client_so} "
        "and {sdk_so} into {native_dir}"
    )
    template = selector_option_label(hass, language, "native_hint", "missing", fallback)
    return fill_template(
        template,
        fallback,
        found=str(found),
        client_so=NATIVE_CLIENT_SO,
        sdk_so=NATIVE_SDK_SO,
        native_dir=str(native_dir),
    )


async def async_remember_picture_passwords(
    runtime: ImouRuntimeData, devices: Any
) -> None:
    """Read each device's picture password once for this process.

    A later password change in the Imou app is not picked up until reload.
    """
    client = runtime.client
    if client is None:
        return
    seen: set[str] = set()
    for device in devices:
        device_id = getattr(device, "device_id", None)
        if not isinstance(device_id, str) or not device_id or device_id in seen:
            continue
        seen.add(device_id)
        if device_id in runtime.picture_password_ready:
            continue
        token = getattr(device, "play_token_v2", None)
        if not isinstance(token, str) or not token.strip():
            continue
        runtime.picture_password_ready.add(device_id)
        try:
            key = await client.async_get_play_token_key(device_id)
        except Exception:
            _LOGGER.warning("Picture password was not read for %s", device_id)
            continue
        if not isinstance(key, str) or not key.strip():
            continue
        password = login_password_from_play_token(play_token=token, play_token_key=key)
        if password:
            runtime.picture_passwords[device_id] = password


def _picture_password(runtime: ImouRuntimeData, device_id: str) -> str | None:
    """Return the password read at load, if this device has one."""
    password = runtime.picture_passwords.get(device_id)
    if isinstance(password, str) and password:
        return password
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


def _preferred_push_pic_url(raw: dict[str, Any]) -> str | None:
    """Use picUrlArray[0], then older thumb and url fields."""
    return alarm_picture_url(raw)


def _thumb_filename(alarm_id: str | None, pic_url: str) -> str:
    """Return a single-segment jpeg name that cannot escape the thumbs dir."""
    if isinstance(alarm_id, str) and _SAFE_THUMB_STEM.fullmatch(alarm_id):
        return f"{alarm_id}.jpg"
    digest_source = alarm_id if isinstance(alarm_id, str) and alarm_id else pic_url
    digest = hashlib.sha256(digest_source.encode()).hexdigest()[:16]
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


def _load_decoder(
    runtime: ImouRuntimeData, hass: HomeAssistant
) -> LCOpenPicDecoder | None:
    """Load the native libraries once, or return None when unusable."""
    if not native_platform_supported():
        if not runtime.pic_decoder_failed:
            runtime.pic_decoder_failed = True
            _LOGGER.warning(
                "Decrypted alarm thumbnails need linux x86-64; this host is %s",
                native_platform_label(),
            )
        return None

    native_dir = native_lib_dir(hass)
    try:
        with _PIC_DECODER_INIT_LOCK:
            if runtime.pic_decoder is None:
                runtime.pic_decoder = LCOpenPicDecoder(native_dir)
            runtime.pic_decoder.load()
            return runtime.pic_decoder
    except Exception:
        runtime.pic_decoder_failed = True
        _LOGGER.warning(
            "LCOpenSDK native libs failed to load from %s; skipping decrypt",
            native_dir,
            exc_info=True,
        )
        return None


def _write_thumb(
    runtime: ImouRuntimeData,
    hass: HomeAssistant,
    event_data: dict[str, Any],
    pic_url: str,
    jpeg: bytes,
) -> str | None:
    thumbs_dir = media_root(hass) / _THUMB_SUBDIR
    thumbs_dir.mkdir(parents=True, exist_ok=True)
    _prune_old_thumbs(thumbs_dir)
    filename = _thumb_filename(event_data.get("alarm_id"), pic_url)
    thumbs_root = thumbs_dir.resolve()
    dest = (thumbs_dir / filename).resolve()
    if not dest.is_relative_to(thumbs_root):
        _LOGGER.warning("Refusing alarm thumb path outside thumbs dir: %s", dest)
        return None
    try:
        dest.write_bytes(jpeg)
    except OSError:
        _LOGGER.warning("Could not write alarm thumb %s", dest, exc_info=True)
        return None

    media_url = media_url_for_path(hass, dest)
    if media_url is None:
        return None
    device_key = imou_life_device_key_from_ids(
        event_data.get("device_id"),
        event_data.get("channel_id"),
        event_data.get("product_id"),
    )
    if device_key:
        persist_last_alarm_image(hass, device_key, jpeg)
    _LOGGER.debug("Wrote decrypted alarm thumb %s (%s bytes)", media_url, len(jpeg))
    return media_url


def _sync_decrypt_and_write(
    decoder: LCOpenPicDecoder,
    runtime: ImouRuntimeData,
    hass: HomeAssistant,
    event_data: dict[str, Any],
    pic_url: str,
    data: bytes,
    encrypt_key: str,
    device_id: str,
    use_tcm: bool,
) -> tuple[str | None, int | None]:
    """Decrypt ciphertext this integration downloaded, then write it to media.

    Returns the authenticated media URL and the SDK code, so the caller can tell
    a wrong device password from one worth reporting differently.
    """
    try:
        jpeg = decoder.decrypt_bytes(
            data,
            device_id=device_id,
            encrypt_key=encrypt_key,
            use_tcm=use_tcm,
        )
    except PicDecodeError as err:
        hint = _SDK_CODE_HINT.get(err.code)
        extra = f", {hint}" if hint else ""
        _LOGGER.warning(
            "Alarm picture decrypt failed for device %s (code=%s%s, tcm=%s, %s bytes)",
            device_id,
            err.code,
            extra,
            use_tcm,
            len(data),
        )
        return None, err.code
    except Exception:
        _LOGGER.warning(
            "Alarm picture decrypt failed for device %s", device_id, exc_info=True
        )
        return None, None

    return _write_thumb(runtime, hass, event_data, pic_url, jpeg), 0


def _content_length(headers: Mapping[str, Any]) -> int | None:
    raw = next(
        (value for name, value in headers.items() if name.lower() == "content-length"),
        None,
    )
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


class _DownloadFailure(NamedTuple):
    """Why one download attempt produced no picture."""

    reason: str
    may_retry: bool


async def _async_try_download_picture(
    hass: HomeAssistant, pic_url: str
) -> bytes | _DownloadFailure:
    """Safely fetch the picture once, or report why it did not arrive.

    Every redirect is validated and DNS-pinned by the shared media fetcher. Its
    streaming and curl limits stop an oversized body before it is buffered.
    """
    try:
        response = await async_fetch_url_response(
            hass,
            pic_url,
            max_bytes=_MAX_PICTURE_BYTES,
            timeout_seconds=_DOWNLOAD_TIMEOUT_SECONDS,
        )
    except UrlFetchBodyTooLargeError:
        return _DownloadFailure(f"larger than {_MAX_PICTURE_BYTES} bytes", False)
    except UrlFetchUnsafeError as err:
        return _DownloadFailure(f"{type(err).__name__}: {err}", False)
    except (TimeoutError, ClientError, OSError, ValueError) as err:
        return _DownloadFailure(f"{type(err).__name__}: {err}", True)

    if response.status != 200:
        return _DownloadFailure(f"HTTP {response.status}", True)
    data = response.body
    if not data:
        return _DownloadFailure("empty body", True)
    expected = _content_length(response.headers)
    if expected is not None and len(data) != expected:
        return _DownloadFailure(f"truncated: got {len(data)} of {expected} bytes", True)
    return data


async def _async_download_picture(
    hass: HomeAssistant, pic_url: str, *, retry: bool = True
) -> bytes | None:
    """Download the encrypted alarm picture.

    Alarm pushes often arrive before the picture is on the CDN, so those
    downloads retry a short 404. Search already has a stored URL and does not.
    """
    started = time.monotonic()
    attempts = 0
    last_reason = ""
    delays = (*_DOWNLOAD_RETRY_DELAYS, None) if retry else (None,)
    for delay in delays:
        attempts += 1
        result = await _async_try_download_picture(hass, pic_url)
        if not isinstance(result, _DownloadFailure):
            _LOGGER.debug(
                "Downloaded encrypted alarm picture (%s bytes, attempt %s, %.1fs)",
                len(result),
                attempts,
                time.monotonic() - started,
            )
            return result
        last_reason = result.reason
        _LOGGER.debug("Alarm picture attempt %s: %s", attempts, last_reason)
        if not result.may_retry or delay is None:
            break
        await asyncio.sleep(delay)

    _LOGGER.warning(
        "Alarm picture never downloaded after %s attempt(s) over %.1fs (%s)",
        attempts,
        time.monotonic() - started,
        last_reason,
    )
    return None


def public_media_url(hass: HomeAssistant, local_path: str) -> str:
    """Turn a media path into an absolute URL phones can fetch off-LAN.

    The phone fetches this URL itself, so a LAN address means no picture on
    cellular. ``get_url(prefer_external=True)`` quietly falls back to the
    internal URL, which is indistinguishable from success; ask for an
    externally reachable address on its own first so the fallback can say what
    happened. The internal address is still returned, since a phone on the LAN
    can load it.
    """
    if local_path.startswith(("http://", "https://")):
        return local_path
    if not local_path.startswith("/"):
        return local_path
    if local_path.startswith(f"{MEDIA_URL_PREFIX}/"):
        local_path = signed_media_path(hass, local_path)
    try:
        return f"{get_url(hass, allow_internal=False).rstrip('/')}{local_path}"
    except NoURLAvailableError:
        pass
    try:
        base = get_url(hass, prefer_external=True)
    except NoURLAvailableError:
        _LOGGER.warning(
            "Cannot build a URL for the alarm thumbnail; set Home Assistant's "
            "external URL (Settings → System → Network) so phones outside the "
            "LAN can load /local/ images"
        )
        return local_path
    _LOGGER.warning(
        "Alarm thumbnails will be sent as %s%s because no externally reachable "
        "address is configured. A phone that is not on this LAN cannot load "
        "that, so the notification arrives without a picture. Set Home "
        "Assistant's external URL (Settings → System → Network)",
        base.rstrip("/"),
        local_path,
    )
    return f"{base.rstrip('/')}{local_path}"


async def async_maybe_decrypt_thumbnail(
    hass: HomeAssistant,
    entry: ConfigEntry,
    runtime: ImouRuntimeData,
    event_data: dict[str, Any],
    *,
    ignore_switch: bool = False,
    retry: bool = True,
) -> str | None:
    """Decrypt an alarm picture; return its media URL or None.

    Pictures are always decrypted. ``ignore_switch`` is kept so existing
    callers stay valid; the old per-account switch is no longer consulted.
    """
    del ignore_switch
    if runtime.pic_decoder_failed:
        _LOGGER.debug("Skip decrypt: native decoder previously failed to load")
        return None

    raw = event_data.get("raw")
    if not isinstance(raw, dict):
        _LOGGER.debug("Skip decrypt: push has no raw payload")
        return None

    pic_url = _preferred_push_pic_url(raw)
    if not pic_url:
        _LOGGER.debug(
            "Skip decrypt for %s: no picture url (thumbUrl/picUrlArray/picUrl)",
            event_data.get("device_id"),
        )
        return None

    device_id = event_data.get("device_id")
    if not device_id:
        _LOGGER.debug("Skip decrypt: push has no device_id")
        return None

    device = _device_for_event(runtime, event_data)
    device_ability = device.device_ability if device is not None else ""
    is_tcm = is_tcm_ability(device_ability)
    device_password = _picture_password(runtime, str(device_id))
    if is_tcm and not device_password:
        _LOGGER.debug(
            "Skip decrypt for TCM device %s: configure a picture password or reload "
            "after signing in",
            device_id,
        )
        return None
    # A recovered password is tried first. The SDK then falls back to the serial.
    encrypt_key = device_password or str(device_id)

    # Keyed per channel, not per serial: the lenses of one camera alarm
    # independently, and each alarm carries its own picture. Same-lens
    # decrypts wait in line so every notification can still attach a still.
    channel_id = event_data.get("channel_id")
    inflight_key = f"{device_id}_{'x' if channel_id is None else channel_id}"
    lock = runtime.thumb_decrypt_locks.setdefault(inflight_key, asyncio.Lock())
    async with lock:
        return await _async_decrypt_thumbnail_locked(
            hass,
            runtime,
            event_data,
            pic_url,
            str(device_id),
            is_tcm,
            encrypt_key,
            bool(device_password),
            retry,
        )


async def _async_decrypt_thumbnail_locked(
    hass: HomeAssistant,
    runtime: ImouRuntimeData,
    event_data: dict[str, Any],
    pic_url: str,
    device_id: str,
    is_tcm: bool,
    encrypt_key: str,
    used_password: bool,
    retry: bool = True,
) -> str | None:
    """Download and decrypt after the per-device in-flight lock is held."""
    # Load before downloading: a host without the libraries can never use the
    # picture, and the load is cached after the first alarm.
    decoder = await hass.async_add_executor_job(_load_decoder, runtime, hass)
    if decoder is None:
        return None

    _LOGGER.debug(
        "Decrypting alarm thumb for %s tcm=%s key=%s url=%s",
        device_id,
        is_tcm,
        "password" if used_password else "serial",
        _redact_picture_url(pic_url),
    )

    data = await _async_download_picture(hass, pic_url, retry=retry)
    if not data:
        return None

    try:
        result, code = await asyncio.wait_for(
            hass.async_add_executor_job(
                _sync_decrypt_and_write,
                decoder,
                runtime,
                hass,
                event_data,
                pic_url,
                data,
                encrypt_key,
                device_id,
                is_tcm,
            ),
            timeout=_DECRYPT_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        _LOGGER.warning(
            "Alarm thumbnail decrypt timed out for device %s",
            device_id,
        )
        return None

    if result is None and code == _SDK_CODE_WRONG_KEY:
        _LOGGER.warning(
            "Alarm picture for %s could not be decrypted on this Home Assistant",
            device_id,
        )
    return result
