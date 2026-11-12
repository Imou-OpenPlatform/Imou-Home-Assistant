"""Tests for Imou media source identifiers and browse."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from custom_components.imou_life.const import DOMAIN
from custom_components.imou_life.media_source import (
    ImouRecordMediaSource,
    MEDIA_SOURCE_NAME,
    _split,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from . import USER_INPUT


def test_split_identifier() -> None:
    assert _split(None) == []
    assert _split("entry:abc|cam:SN1:0|cloud") == [
        "entry:abc",
        "cam:SN1:0",
        "cloud",
    ]


@pytest.mark.asyncio
async def test_browse_root_lists_loaded_entries(hass: HomeAssistant) -> None:
    """Media browser root returns Imou Life without raising."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, title="Imou test")
    entry.add_to_hass(hass)
    object.__setattr__(entry, "state", ConfigEntryState.LOADED)
    source = ImouRecordMediaSource(hass)
    item = MagicMock()
    item.identifier = None
    item.domain = DOMAIN
    browse = await source.async_browse_media(item)
    assert browse.title == MEDIA_SOURCE_NAME
    assert any(child.title == "Imou test" for child in browse.children or [])
