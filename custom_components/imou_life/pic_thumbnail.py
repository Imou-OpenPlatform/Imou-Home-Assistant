"""Decrypt alarm push thumbnails and write them under www for Companion notify."""

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
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from aiohttp import ClientError, ClientTimeout
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import NoURLAvailableError, get_url
from pyimouapi.pic_decode import (
    CLIENT_LIB,
    SDK_LIB,
    LCOpenPicDecoder,
    PicDecodeError,
    is_tcm_ability,
    resolve_encrypt_key,
)
from pyimouapi.push import preferred_pic_url

from .const import (
    PARAM_ATTACH_DECRYPTED_THUMBNAIL,
    PARAM_DEFAULT_DEVICE_PASSWORD,
    PARAM_DEVICE_PASSWORDS,
    imou_life_device_keys_from_ids,
)
from .helpers import fill_template, selector_option_label

if TYPE_CHECKING:
    from .runtime_data import ImouRuntimeData

_LOGGER = logging.getLogger(__name__)

_THUMB_SUBDIR = Path("imou_life") / "thumbs"
_THUMB_MAX_AGE_SECONDS = 24 * 60 * 60
# Push alarm ids become www filenames; reject path separators and traversal.
_SAFE_THUMB_STEM = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_DECRYPT_TIMEOUT_SECONDS = 30
_DOWNLOAD_TIMEOUT_SECONDS = 20
_DOWNLOAD_CHUNK_BYTES = 64 * 1024
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
    template = selector_option_label(
        hass, language, "native_hint", "missing", fallback
    )
    return fill_template(
        template,
        fallback,
        found=str(found),
        client_so=NATIVE_CLIENT_SO,
        sdk_so=NATIVE_SDK_SO,
        native_dir=str(native_dir),
    )


def _nonempty_password(value: Any) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        if text:
            return text
    return None


def password_for_device(options: Mapping[str, Any], device_id: str) -> str | None:
    """Resolve per-serial password from entry options."""
    passwords = options.get(PARAM_DEVICE_PASSWORDS)
    if isinstance(passwords, dict):
        per_device = _nonempty_password(passwords.get(device_id))
        if per_device:
            return per_device
    return _nonempty_password(options.get(PARAM_DEFAULT_DEVICE_PASSWORD))


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


def _field_pic_urls(value: Any) -> list[str]:
    """Return non-empty URL strings from a list or a single string."""
    if isinstance(value, str) and value:
        return [value]
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _preferred_push_pic_url(raw: dict[str, Any]) -> str | None:
    """Prefer thumbUrl, then picUrlArray / picUrlArr / picUrl."""
    for key in ("thumbUrl", "picUrlArray", "picUrlArr", "picUrl"):
        picked = preferred_pic_url(_field_pic_urls(raw.get(key)))
        if picked:
            return picked
    return None


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

    local_url = f"/local/{_THUMB_SUBDIR.as_posix()}/{filename}"
    _LOGGER.debug("Wrote decrypted alarm thumb %s (%s bytes)", local_url, len(jpeg))
    return local_url


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
    """Decrypt ciphertext this integration downloaded, then write it under www.

    Returns the ``/local/`` URL and the SDK code, so the caller can tell a
    wrong device password from one worth reporting differently.
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
    raw = headers.get("Content-Length")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


class _DownloadFailure(NamedTuple):
    """Why one download attempt produced no picture."""

    reason: str
    may_retry: bool


async def _async_try_download_picture(
    session: Any, pic_url: str
) -> bytes | _DownloadFailure:
    """Fetch the picture once, or report why it did not arrive.

    The decrypt rejects a short read as corrupt, so the body has to be read to
    the end and checked against Content-Length rather than taken from a single
    read() that only returns what has arrived so far.
    """
    try:
        async with session.get(
            pic_url, timeout=ClientTimeout(total=_DOWNLOAD_TIMEOUT_SECONDS)
        ) as response:
            if response.status != 200:
                return _DownloadFailure(f"HTTP {response.status}", True)
            expected = _content_length(response.headers)
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.content.iter_chunked(_DOWNLOAD_CHUNK_BYTES):
                total += len(chunk)
                if total > _MAX_PICTURE_BYTES:
                    return _DownloadFailure(
                        f"larger than {_MAX_PICTURE_BYTES} bytes", False
                    )
                chunks.append(chunk)
    except (TimeoutError, ClientError) as err:
        return _DownloadFailure(f"{type(err).__name__}: {err}", True)

    data = b"".join(chunks)
    if not data:
        return _DownloadFailure("empty body", True)
    if expected is not None and len(data) != expected:
        return _DownloadFailure(f"truncated: got {len(data)} of {expected} bytes", True)
    return data


async def _async_download_picture(hass: HomeAssistant, pic_url: str) -> bytes | None:
    """Download the encrypted alarm picture, waiting out a late upload.

    The push regularly beats the picture onto the alarm CDN: the object 404s
    for a moment, or a half-uploaded one comes back short. Both look permanent
    on a single try, so retry briefly. The alarm notification is waiting on
    this, which is what keeps the budget small.
    """
    session = async_get_clientsession(hass)
    started = time.monotonic()
    attempts = 0
    last_reason = ""
    for delay in (*_DOWNLOAD_RETRY_DELAYS, None):
        attempts += 1
        result = await _async_try_download_picture(session, pic_url)
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
    """Turn ``/local/...`` into an absolute URL phones can fetch off-LAN.

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
) -> str | None:
    """Decrypt push image when enabled; return /local/... URL or None."""
    if not entry.options.get(PARAM_ATTACH_DECRYPTED_THUMBNAIL):
        _LOGGER.debug("Skip decrypt: attach_decrypted_thumbnail is off")
        return None
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
    device_password = password_for_device(entry.options, str(device_id))
    encrypt_key = resolve_encrypt_key(
        is_tcm=is_tcm,
        device_id=str(device_id),
        device_password=device_password,
    )
    if encrypt_key is None:
        _LOGGER.debug(
            "Skipping TCM decrypt for %s: no device password configured",
            device_id,
        )
        return None

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
        pic_url,
    )

    data = await _async_download_picture(hass, pic_url)
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
            "Alarm picture decrypt for %s rejected the %s; check Configure → "
            "Alarm pictures",
            device_id,
            "configured device password" if used_password else "serial key",
        )
    return result
