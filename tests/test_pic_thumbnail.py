"""Tests for alarm thumbnail decrypt and www write."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from custom_components.imou_life.const import DOMAIN, PARAM_ATTACH_DECRYPTED_THUMBNAIL
from custom_components.imou_life.pic_thumbnail import (
    _sync_decrypt_and_write,
    async_maybe_decrypt_thumbnail,
)
from custom_components.imou_life.runtime_data import ImouRuntimeData
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from . import USER_INPUT


@pytest.mark.usefixtures("enable_custom_integrations")
def test_sync_decrypt_and_write_success(hass: HomeAssistant) -> None:
    """Mocked decoder writes JPEG under www and returns /local/ path."""
    jpeg_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 10
    mock_decoder = MagicMock()
    mock_decoder.decrypt_picture.return_value = jpeg_bytes

    runtime = ImouRuntimeData(coordinator=MagicMock())
    runtime.pic_decoder = mock_decoder
    runtime.pic_decoder_initialized = True

    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)

    event_data = {"alarm_id": "alarm123", "device_id": "SN1"}
    pic_url = "https://example.com/pic"

    result = _sync_decrypt_and_write(
        runtime,
        entry,
        hass,
        event_data,
        pic_url,
        "encrypt_key",
        "SN1",
        False,
        "token",
    )

    assert result == "/local/imou_life/thumbs/alarm123.jpg"
    dest = Path(hass.config.path("www", "imou_life", "thumbs", "alarm123.jpg"))
    assert dest.exists()
    assert dest.read_bytes() == jpeg_bytes
    mock_decoder.decrypt_picture.assert_called_once_with(
        pic_url=pic_url,
        encrypt_key="encrypt_key",
        device_id="SN1",
        token="token",
        use_tcm=False,
    )
    assert "thumbnail_local_url" not in event_data


@pytest.mark.usefixtures("enable_custom_integrations")
def test_sync_decrypt_and_write_sha256_filename(hass: HomeAssistant) -> None:
    """Without alarm_id, filename falls back to SHA-256 of pic URL."""
    jpeg_bytes = b"\xff\xd8\xff\xe0"
    mock_decoder = MagicMock()
    mock_decoder.decrypt_picture.return_value = jpeg_bytes

    runtime = ImouRuntimeData(coordinator=MagicMock())
    runtime.pic_decoder = mock_decoder
    runtime.pic_decoder_initialized = True

    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)

    pic_url = "https://example.com/unique-pic"
    digest = hashlib.sha256(pic_url.encode()).hexdigest()[:16]
    event_data = {"device_id": "SN1"}

    result = _sync_decrypt_and_write(
        runtime,
        entry,
        hass,
        event_data,
        pic_url,
        "encrypt_key",
        "SN1",
        False,
        "",
    )

    assert result == f"/local/imou_life/thumbs/{digest}.jpg"
    dest = Path(hass.config.path("www", "imou_life", "thumbs", f"{digest}.jpg"))
    assert dest.exists()
    assert dest.read_bytes() == jpeg_bytes


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
def test_sync_decrypt_skips_unsupported_platform(hass: HomeAssistant) -> None:
    """Do not load native libs when the host is not linux x86-64."""
    from custom_components.imou_life import pic_thumbnail

    runtime = ImouRuntimeData(coordinator=MagicMock())
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    with patch.object(pic_thumbnail, "native_platform_supported", return_value=False):
        result = pic_thumbnail._sync_decrypt_and_write(
            runtime,
            entry,
            hass,
            {"alarm_id": "alarm123", "device_id": "SN1"},
            "https://example.com/pic",
            "encrypt_key",
            "SN1",
            False,
            "token",
        )
    assert result is None
    assert runtime.pic_decoder is None


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_maybe_decrypt_logs_when_push_has_no_picture(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Switch on still logs a skip reason when the push has no image URL."""
    caplog.set_level("DEBUG", logger="custom_components.imou_life.pic_thumbnail")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={PARAM_ATTACH_DECRYPTED_THUMBNAIL: True},
    )
    entry.add_to_hass(hass)
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
async def test_maybe_decrypt_uses_pic_url_when_array_missing(
    hass: HomeAssistant,
) -> None:
    """PaaS pushes often have picUrl instead of picUrlArray."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={PARAM_ATTACH_DECRYPTED_THUMBNAIL: True},
    )
    entry.add_to_hass(hass)
    runtime = ImouRuntimeData(coordinator=MagicMock())
    event_data = {
        "device_id": "SN1",
        "alarm_id": "a1",
        "raw": {"picUrl": "https://example.com/only.jpg"},
    }
    with patch(
        "custom_components.imou_life.pic_thumbnail._sync_decrypt_and_write",
        return_value="/local/imou_life/thumbs/a1.jpg",
    ) as mock_decrypt:
        result = await async_maybe_decrypt_thumbnail(
            hass, entry, runtime, event_data
        )
    assert result == "/local/imou_life/thumbs/a1.jpg"
    assert mock_decrypt.call_args.args[4] == "https://example.com/only.jpg"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_maybe_decrypt_prefers_openapi_access_token(
    hass: HomeAssistant,
) -> None:
    """Official Demo token is the OpenAPI accessToken, not the push field."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={PARAM_ATTACH_DECRYPTED_THUMBNAIL: True},
    )
    entry.add_to_hass(hass)
    client = MagicMock()
    client.access_token = "openapi-token"
    runtime = ImouRuntimeData(coordinator=MagicMock(), client=client)
    event_data = {
        "device_id": "SN1",
        "alarm_id": "a1",
        "token": "push-token",
        "raw": {"picUrl": "https://example.com/only.jpg", "token": "push-token"},
    }
    with patch(
        "custom_components.imou_life.pic_thumbnail._sync_decrypt_and_write",
        return_value="/local/imou_life/thumbs/a1.jpg",
    ) as mock_decrypt:
        result = await async_maybe_decrypt_thumbnail(
            hass, entry, runtime, event_data
        )
    assert result == "/local/imou_life/thumbs/a1.jpg"
    assert mock_decrypt.call_args.args[8] == "openapi-token"
    assert mock_decrypt.call_args.args[9] == "push-token"


@pytest.mark.usefixtures("enable_custom_integrations")
def test_sync_decrypt_retries_push_token_after_url_auth_fail(
    hass: HomeAssistant,
) -> None:
    """code=-1 with the OpenAPI token still tries the push token."""
    from pyimouapi.pic_decode import PicDecodeError

    jpeg_bytes = b"\xff\xd8\xff\xe0"
    mock_decoder = MagicMock()
    mock_decoder.decrypt_picture.side_effect = [
        PicDecodeError(-1, "sdk -1"),
        jpeg_bytes,
    ]

    runtime = ImouRuntimeData(coordinator=MagicMock())
    runtime.pic_decoder = mock_decoder
    runtime.pic_decoder_initialized = True

    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)

    result = _sync_decrypt_and_write(
        runtime,
        entry,
        hass,
        {"alarm_id": "alarm123", "device_id": "SN1"},
        "https://example.com/pic",
        "encrypt_key",
        "SN1",
        False,
        "openapi-token",
        "push-token",
    )

    assert result == "/local/imou_life/thumbs/alarm123.jpg"
    assert mock_decoder.decrypt_picture.call_count == 2
    assert mock_decoder.decrypt_picture.call_args_list[0].kwargs["token"] == (
        "openapi-token"
    )
    assert mock_decoder.decrypt_picture.call_args_list[1].kwargs["token"] == (
        "push-token"
    )


@pytest.mark.usefixtures("enable_custom_integrations")
def test_sync_decrypt_logs_url_auth_meaning_for_code_minus_one(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    from pyimouapi.pic_decode import PicDecodeError

    mock_decoder = MagicMock()
    mock_decoder.decrypt_picture.side_effect = PicDecodeError(-1, "sdk -1")

    runtime = ImouRuntimeData(coordinator=MagicMock())
    runtime.pic_decoder = mock_decoder
    runtime.pic_decoder_initialized = True

    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)

    with caplog.at_level("WARNING"):
        result = _sync_decrypt_and_write(
            runtime,
            entry,
            hass,
            {"alarm_id": "alarm123", "device_id": "SN1"},
            "https://example.com/pic",
            "encrypt_key",
            "SN1",
            False,
            "token",
        )
    assert result is None
    assert "URL auth or download failed" in caplog.text
    assert "tcm=False" in caplog.text
