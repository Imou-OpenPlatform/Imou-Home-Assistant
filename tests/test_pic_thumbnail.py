"""Tests for alarm thumbnail decrypt and www write."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from custom_components.imou_life.const import DOMAIN
from custom_components.imou_life.pic_thumbnail import _sync_decrypt_and_write
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
