"""Media browser shows saved alarm clips when a folder is configured."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from custom_components.imou_life.const import DOMAIN
from custom_components.imou_life.media_source import KIND_ALARM, ImouRecordMediaSource
from homeassistant.components.media_source.models import MediaSourceItem
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from . import USER_INPUT


@pytest.mark.asyncio
async def test_browse_kinds_includes_alarm_folder(
    hass: HomeAssistant, tmp_path: Path
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        title="Imou test",
    )
    entry.add_to_hass(hass)
    object.__setattr__(entry, "state", ConfigEntryState.LOADED)
    with patch(
        "custom_components.imou_life.media_source.alarm_clip_folder",
        return_value=tmp_path,
    ):
        source = ImouRecordMediaSource(hass)
        item = MagicMock()
        item.identifier = f"entry:{entry.entry_id}|cam:SN1:0"
        browse = await source.async_browse_media(item)
        titles = [child.title for child in browse.children or []]
        assert "Record on alarm" in titles


@pytest.mark.asyncio
async def test_resolve_saved_alarm_clip(hass: HomeAssistant, tmp_path: Path) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
    )
    entry.add_to_hass(hass)
    object.__setattr__(entry, "state", ConfigEntryState.LOADED)
    name = "SN1_0_20261112_120000.mp4"
    (tmp_path / name).write_bytes(b"mp4")
    source = ImouRecordMediaSource(hass)
    item = MediaSourceItem(
        hass,
        DOMAIN,
        f"play|{KIND_ALARM}|{entry.entry_id}|SN1|0|{name}",
        None,
    )
    with (
        patch(
            "custom_components.imou_life.media_source.alarm_clip_folder",
            return_value=tmp_path,
        ),
        patch.object(hass.config, "is_allowed_path", return_value=True),
        patch(
            "custom_components.imou_life.media_source.signed_saved_record_url",
            return_value="/api/imou_life/saved_record/test/file.mp4",
        ),
    ):
        play = await source.async_resolve_media(item)
    assert play.mime_type == "video/mp4"
    assert play.url.endswith(".mp4") or "saved_record" in play.url
