"""Tests for alarm thumbnail download, decrypt, and www write."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import AsyncIterator, Iterable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.imou_life.const import (
    DOMAIN,
    PARAM_ATTACH_DECRYPTED_THUMBNAIL,
    PARAM_DEVICE_PASSWORDS,
)
from custom_components.imou_life.pic_thumbnail import (
    _async_download_picture,
    _sync_decrypt_and_write,
    async_maybe_decrypt_thumbnail,
    public_media_url,
)
from custom_components.imou_life.runtime_data import ImouRuntimeData
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from . import USER_INPUT

CIPHERTEXT = b"DHAV" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 10


async def _aiter(chunks: Iterable[bytes]) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


class _AsyncCtx:
    """Minimal async context manager around a mocked aiohttp response."""

    def __init__(self, response: Any) -> None:
        self._response = response

    async def __aenter__(self) -> Any:
        return self._response

    async def __aexit__(self, *_exc: object) -> bool:
        return False


def _runtime_with_access_token(token: str = "openapi-token") -> ImouRuntimeData:
    client = MagicMock()
    client.access_token = token
    client.async_get_token = AsyncMock()
    return ImouRuntimeData(coordinator=MagicMock(), client=client)


def _runtime_with_decoder(jpeg: bytes = JPEG) -> tuple[ImouRuntimeData, MagicMock]:
    decoder = MagicMock()
    decoder.decrypt_bytes.return_value = jpeg
    runtime = _runtime_with_access_token()
    runtime.pic_decoder = decoder
    return runtime, decoder


def _patched_decoder() -> Any:
    """Pretend the native libraries loaded, without touching the real .so files."""
    return patch(
        "custom_components.imou_life.pic_thumbnail._load_decoder",
        return_value=MagicMock(),
    )


def _entry(hass: HomeAssistant, *, attach: bool = True) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={PARAM_ATTACH_DECRYPTED_THUMBNAIL: attach},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.mark.usefixtures("enable_custom_integrations")
def test_sync_decrypt_and_write_success(hass: HomeAssistant) -> None:
    """Ciphertext we downloaded is decrypted locally and written under www."""
    runtime, decoder = _runtime_with_decoder()
    _entry(hass)

    result = _sync_decrypt_and_write(
        decoder,
        runtime,
        hass,
        {"alarm_id": "alarm123", "device_id": "SN1"},
        "https://example.com/pic",
        CIPHERTEXT,
        "encrypt_key",
        "SN1",
        False,
    )

    assert result == ("/local/imou_life/thumbs/alarm123.jpg", 0)
    dest = Path(hass.config.path("www", "imou_life", "thumbs", "alarm123.jpg"))
    assert dest.read_bytes() == JPEG
    decoder.decrypt_bytes.assert_called_once_with(
        CIPHERTEXT,
        device_id="SN1",
        encrypt_key="encrypt_key",
        use_tcm=False,
    )


@pytest.mark.usefixtures("enable_custom_integrations")
def test_sync_decrypt_and_write_sha256_filename(hass: HomeAssistant) -> None:
    """Without alarm_id, filename falls back to SHA-256 of pic URL."""
    runtime, decoder = _runtime_with_decoder()
    _entry(hass)
    pic_url = "https://example.com/unique-pic"
    digest = hashlib.sha256(pic_url.encode()).hexdigest()[:16]

    result = _sync_decrypt_and_write(
        decoder,
        runtime,
        hass,
        {"device_id": "SN1"},
        pic_url,
        CIPHERTEXT,
        "encrypt_key",
        "SN1",
        False,
    )

    assert result == (f"/local/imou_life/thumbs/{digest}.jpg", 0)


@pytest.mark.usefixtures("enable_custom_integrations")
def test_thumb_filename_rejects_path_traversal() -> None:
    """Hostile alarm_id values must not become relative path segments."""
    from custom_components.imou_life.pic_thumbnail import _thumb_filename

    assert ".." not in _thumb_filename("../../etc/passwd", "https://x")
    assert "/" not in _thumb_filename("a/b", "https://x")
    assert "\\" not in _thumb_filename("a\\b", "https://x")
    assert _thumb_filename("safe-Alarm_1.id", "https://x") == "safe-Alarm_1.id.jpg"


@pytest.mark.usefixtures("enable_custom_integrations")
def test_sync_decrypt_and_write_hashes_unsafe_alarm_id(hass: HomeAssistant) -> None:
    """Traversal-style alarm_id falls back to a hashed single-segment name."""
    runtime, decoder = _runtime_with_decoder()
    _entry(hass)
    alarm_id = "../../escape"
    digest = hashlib.sha256(alarm_id.encode()).hexdigest()[:16]

    result = _sync_decrypt_and_write(
        decoder,
        runtime,
        hass,
        {"alarm_id": alarm_id, "device_id": "SN1"},
        "https://example.com/pic",
        CIPHERTEXT,
        "encrypt_key",
        "SN1",
        False,
    )

    assert result == (f"/local/imou_life/thumbs/{digest}.jpg", 0)
    thumbs = Path(hass.config.path("www", "imou_life", "thumbs"))
    assert (thumbs / f"{digest}.jpg").is_file()
    assert not (thumbs.parent.parent / "escape.jpg").exists()
    dest = Path(hass.config.path("www", "imou_life", "thumbs", f"{digest}.jpg"))
    assert dest.read_bytes() == JPEG


def test_native_platform_supported_linux_x86_64(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Official Demo .so files are linux x86-64 only."""
    import sys

    from custom_components.imou_life import pic_thumbnail

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(pic_thumbnail.platform, "machine", lambda: "x86_64")
    assert pic_thumbnail.native_platform_supported() is True
    assert pic_thumbnail.native_platform_label() == "linux x86_64"

    monkeypatch.setattr(pic_thumbnail.platform, "machine", lambda: "aarch64")
    assert pic_thumbnail.native_platform_supported() is False
    assert "aarch64" in pic_thumbnail.native_platform_label()

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(pic_thumbnail.platform, "machine", lambda: "x86_64")
    assert pic_thumbnail.native_platform_supported() is False

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(pic_thumbnail.platform, "machine", lambda: "x86_64")
    assert pic_thumbnail.native_support_status("en") == "supported (linux x86-64)"
    assert pic_thumbnail.native_support_status("zh-Hans") == "支持 (linux x86-64)"

    monkeypatch.setattr(pic_thumbnail.platform, "machine", lambda: "aarch64")
    assert "not supported" in pic_thumbnail.native_support_status("en")
    zh_status = pic_thumbnail.native_support_status("zh-Hans")
    assert zh_status.startswith("不支持")
    assert "linux aarch64" in zh_status


@pytest.mark.usefixtures("enable_custom_integrations")
def test_load_decoder_skips_unsupported_platform(hass: HomeAssistant) -> None:
    """Do not load native libs when the host is not linux x86-64."""
    from custom_components.imou_life import pic_thumbnail

    runtime = ImouRuntimeData(coordinator=MagicMock())
    _entry(hass)
    with patch.object(pic_thumbnail, "native_platform_supported", return_value=False):
        assert pic_thumbnail._load_decoder(runtime, hass) is None
    assert runtime.pic_decoder is None
    assert runtime.pic_decoder_failed is True


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_maybe_decrypt_skips_download_without_native_libs(
    hass: HomeAssistant,
) -> None:
    """A host that cannot decrypt must not spend bandwidth on the ciphertext."""
    entry = _entry(hass)
    runtime = _runtime_with_access_token()
    event_data = {
        "device_id": "SN1",
        "alarm_id": "a1",
        "raw": {"picUrl": "https://example.com/only.jpg"},
    }

    with (
        patch(
            "custom_components.imou_life.pic_thumbnail._load_decoder",
            return_value=None,
        ),
        patch(
            "custom_components.imou_life.pic_thumbnail._async_download_picture",
            AsyncMock(),
        ) as mock_download,
    ):
        result = await async_maybe_decrypt_thumbnail(hass, entry, runtime, event_data)

    assert result is None
    mock_download.assert_not_awaited()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_maybe_decrypt_logs_when_push_has_no_picture(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Switch on still logs a skip reason when the push has no image URL."""
    caplog.set_level("DEBUG", logger="custom_components.imou_life.pic_thumbnail")
    entry = _entry(hass)
    runtime = ImouRuntimeData(coordinator=MagicMock())
    result = await async_maybe_decrypt_thumbnail(
        hass,
        entry,
        runtime,
        {"device_id": "SN1", "raw": {"msgType": "abAlarmSound"}},
    )
    assert result is None
    assert "no picture url" in caplog.text.lower()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_maybe_decrypt_downloads_then_decrypts_without_token(
    hass: HomeAssistant,
) -> None:
    """The normal path fetches the picture itself and never needs a token."""
    entry = _entry(hass)
    runtime = _runtime_with_access_token()
    event_data = {
        "device_id": "SN1",
        "alarm_id": "a1",
        "raw": {"picUrl": "https://example.com/only.jpg"},
    }

    with (
        _patched_decoder(),
        patch(
            "custom_components.imou_life.pic_thumbnail._async_download_picture",
            AsyncMock(return_value=CIPHERTEXT),
        ) as mock_download,
        patch(
            "custom_components.imou_life.pic_thumbnail._sync_decrypt_and_write",
            return_value=("/local/imou_life/thumbs/a1.jpg", 0),
        ) as mock_decrypt,
    ):
        result = await async_maybe_decrypt_thumbnail(hass, entry, runtime, event_data)

    assert result == "/local/imou_life/thumbs/a1.jpg"
    assert mock_download.await_args.args[1] == "https://example.com/only.jpg"
    assert mock_decrypt.call_args.args[5] == CIPHERTEXT
    runtime.client.async_get_token.assert_not_awaited()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_maybe_decrypt_uses_pic_url_arr(hass: HomeAssistant) -> None:
    """IoT pushes use picUrlArr, not picUrlArray."""
    entry = _entry(hass)
    runtime = _runtime_with_access_token()
    event_data = {
        "device_id": "SN1",
        "alarm_id": "a1",
        "raw": {"picUrlArr": ["https://example.com/arr.jpg"]},
    }

    with (
        _patched_decoder(),
        patch(
            "custom_components.imou_life.pic_thumbnail._async_download_picture",
            AsyncMock(return_value=CIPHERTEXT),
        ) as mock_download,
        patch(
            "custom_components.imou_life.pic_thumbnail._sync_decrypt_and_write",
            return_value=("/local/imou_life/thumbs/a1.jpg", 0),
        ),
    ):
        result = await async_maybe_decrypt_thumbnail(hass, entry, runtime, event_data)

    assert result == "/local/imou_life/thumbs/a1.jpg"
    assert mock_download.await_args.args[1] == "https://example.com/arr.jpg"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_maybe_decrypt_stops_when_download_fails(hass: HomeAssistant) -> None:
    """No picture bytes means nothing to decrypt, and no token is spent."""
    entry = _entry(hass)
    runtime = _runtime_with_access_token()
    event_data = {
        "device_id": "SN1",
        "alarm_id": "a1",
        "raw": {"picUrl": "https://example.com/only.jpg"},
    }

    with (
        _patched_decoder(),
        patch(
            "custom_components.imou_life.pic_thumbnail._async_download_picture",
            AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.imou_life.pic_thumbnail._sync_decrypt_and_write",
        ) as mock_decrypt,
    ):
        result = await async_maybe_decrypt_thumbnail(hass, entry, runtime, event_data)

    assert result is None
    mock_decrypt.assert_not_called()
    runtime.client.async_get_token.assert_not_awaited()


@pytest.mark.usefixtures("enable_custom_integrations")
def test_sync_decrypt_logs_wrong_key_hint(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A wrong device password is reported as such, with the ciphertext size."""
    from pyimouapi.pic_decode import PicDecodeError

    runtime, decoder = _runtime_with_decoder()
    decoder.decrypt_bytes.side_effect = PicDecodeError(2, "sdk 2")
    _entry(hass)

    with caplog.at_level("WARNING"):
        result = _sync_decrypt_and_write(
            decoder,
            runtime,
            hass,
            {"alarm_id": "alarm123", "device_id": "SN1"},
            "https://example.com/pic",
            CIPHERTEXT,
            "encrypt_key",
            "SN1",
            True,
        )

    assert result == (None, 2)
    assert "wrong key" in caplog.text
    assert "tcm=True" in caplog.text
    assert f"{len(CIPHERTEXT)} bytes" in caplog.text


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_maybe_decrypt_wrong_password_points_at_the_option(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A rejected device password is called out where the user can fix it."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={
            PARAM_ATTACH_DECRYPTED_THUMBNAIL: True,
            PARAM_DEVICE_PASSWORDS: {"SN1": "wrong"},
        },
    )
    entry.add_to_hass(hass)
    runtime = _runtime_with_access_token()
    device = MagicMock()
    device.device_id = "SN1"
    device.device_ability = "WLAN,TCM"
    runtime.coordinator.devices_by_key = {"SN1_0": device}
    event_data = {
        "device_id": "SN1",
        "alarm_id": "a1",
        "raw": {"picUrl": "https://example.com/only.jpg"},
    }

    with (
        _patched_decoder(),
        patch(
            "custom_components.imou_life.pic_thumbnail._async_download_picture",
            AsyncMock(return_value=CIPHERTEXT),
        ),
        patch(
            "custom_components.imou_life.pic_thumbnail._sync_decrypt_and_write",
            return_value=(None, 2),
        ),
        caplog.at_level("WARNING"),
    ):
        result = await async_maybe_decrypt_thumbnail(hass, entry, runtime, event_data)

    assert result is None
    runtime.client.async_get_token.assert_not_awaited()
    assert "configured device password" in caplog.text


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_download_picture_reads_whole_body(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A single read() can return a partial body, so the download must drain it."""
    body = b"DHAV" + bytes(range(256)) * 400
    aioclient_mock.get("https://example.com/pic", content=body)

    assert await _async_download_picture(hass, "https://example.com/pic") == body


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_download_picture_gives_up_after_retries(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A URL that never serves the picture must not retry forever."""
    from custom_components.imou_life import pic_thumbnail

    aioclient_mock.get("https://example.com/pic", status=403, content=b"denied")

    with patch.object(pic_thumbnail.asyncio, "sleep", AsyncMock()):
        assert await _async_download_picture(hass, "https://example.com/pic") is None
    assert aioclient_mock.call_count == len(pic_thumbnail._DOWNLOAD_RETRY_DELAYS) + 1


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_download_picture_waits_for_a_late_upload(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """The push often beats the picture onto the CDN, which 404s until it lands."""
    from custom_components.imou_life import pic_thumbnail

    body = b"DHAV" + b"\x00" * 32
    missing = MagicMock()
    missing.status = 404
    late = MagicMock()
    late.status = 200
    late.headers = {"Content-Length": str(len(body))}
    late.content.iter_chunked = lambda _size: _aiter([body])
    session = MagicMock()
    session.get = MagicMock(side_effect=[_AsyncCtx(missing), _AsyncCtx(late)])

    with (
        patch.object(pic_thumbnail, "async_get_clientsession", return_value=session),
        patch.object(pic_thumbnail.asyncio, "sleep", AsyncMock()) as mock_sleep,
        caplog.at_level("DEBUG", logger="custom_components.imou_life.pic_thumbnail"),
    ):
        result = await pic_thumbnail._async_download_picture(hass, "https://a/pic")

    assert result == body
    assert mock_sleep.await_count == 1
    assert "attempt 2" in caplog.text


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_download_picture_retries_a_short_body(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """A body shorter than Content-Length is a half-uploaded picture, so retry."""
    from custom_components.imou_life import pic_thumbnail

    response = MagicMock()
    response.status = 200
    response.headers = {"Content-Length": "100"}
    response.content.iter_chunked = lambda _size: _aiter([b"short"])
    session = MagicMock()
    session.get = MagicMock(return_value=_AsyncCtx(response))

    with (
        patch.object(pic_thumbnail, "async_get_clientsession", return_value=session),
        patch.object(pic_thumbnail.asyncio, "sleep", AsyncMock()) as mock_sleep,
        caplog.at_level("WARNING"),
    ):
        result = await pic_thumbnail._async_download_picture(hass, "https://a/pic")

    assert result is None
    assert "truncated" in caplog.text
    assert mock_sleep.await_count == len(pic_thumbnail._DOWNLOAD_RETRY_DELAYS)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_download_picture_does_not_retry_an_oversized_body(
    hass: HomeAssistant,
) -> None:
    """An object far too large to be an alarm still will not shrink on a retry."""
    from custom_components.imou_life import pic_thumbnail

    huge = b"\x00" * (pic_thumbnail._MAX_PICTURE_BYTES + 1)
    response = MagicMock()
    response.status = 200
    response.headers = {}
    response.content.iter_chunked = lambda _size: _aiter([huge])
    session = MagicMock()
    session.get = MagicMock(return_value=_AsyncCtx(response))

    with (
        patch.object(pic_thumbnail, "async_get_clientsession", return_value=session),
        patch.object(pic_thumbnail.asyncio, "sleep", AsyncMock()) as mock_sleep,
    ):
        result = await pic_thumbnail._async_download_picture(hass, "https://a/pic")

    assert result is None
    mock_sleep.assert_not_awaited()


@pytest.mark.usefixtures("enable_custom_integrations")
def test_public_media_url_uses_external_url(hass: HomeAssistant) -> None:
    """Companion image URLs must be absolute so phones off-LAN can fetch them."""
    hass.config.external_url = "https://ha.example.com"
    assert (
        public_media_url(hass, "/local/imou_life/thumbs/a.jpg")
        == "https://ha.example.com/local/imou_life/thumbs/a.jpg"
    )
    assert (
        public_media_url(hass, "https://cdn.example/a.jpg")
        == "https://cdn.example/a.jpg"
    )


@pytest.mark.usefixtures("enable_custom_integrations")
def test_public_media_url_external_url_is_not_warned_about(
    hass: HomeAssistant, caplog
) -> None:
    """A properly configured instance must not be nagged."""
    hass.config.external_url = "https://ha.example.com"
    hass.config.internal_url = "http://192.168.1.5:8123"

    with caplog.at_level(logging.WARNING):
        public_media_url(hass, "/local/imou_life/thumbs/a.jpg")

    assert caplog.text == ""


@pytest.mark.usefixtures("enable_custom_integrations")
def test_public_media_url_warns_when_only_internal_is_set(
    hass: HomeAssistant, caplog
) -> None:
    """A LAN address silently produces notifications with no picture on cellular.

    ``get_url`` falls back to the internal URL, which looks like success, so
    the fallback has to say so or the missing picture is undiagnosable.
    """
    hass.config.external_url = None
    hass.config.internal_url = "http://192.168.1.5:8123"

    with caplog.at_level(logging.WARNING):
        url = public_media_url(hass, "/local/imou_life/thumbs/a.jpg")

    # Still absolute: a phone that is on the LAN can load this one.
    assert url == "http://192.168.1.5:8123/local/imou_life/thumbs/a.jpg"
    assert "http://192.168.1.5:8123" in caplog.text
    assert "external url" in caplog.text.lower()


@pytest.mark.usefixtures("enable_custom_integrations")
def test_public_media_url_warns_when_no_url_is_available(
    hass: HomeAssistant, caplog
) -> None:
    """With no address at all the relative path is all that is left."""
    from custom_components.imou_life import pic_thumbnail

    hass.config.external_url = None
    hass.config.internal_url = None

    with (
        patch.object(
            pic_thumbnail,
            "get_url",
            side_effect=pic_thumbnail.NoURLAvailableError,
        ),
        caplog.at_level(logging.WARNING),
    ):
        url = public_media_url(hass, "/local/imou_life/thumbs/a.jpg")

    assert url == "/local/imou_life/thumbs/a.jpg"
    assert "external url" in caplog.text.lower()
