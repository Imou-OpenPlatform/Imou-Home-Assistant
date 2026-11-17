"""Tests for Imou media source identifiers and browse."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from custom_components.imou_life.const import (
    DOMAIN,
    imou_life_device_key,
)
from custom_components.imou_life.media_source import (
    MEDIA_SOURCE_NAME,
    ImouRecordMediaSource,
    _record_hls_expiry,
    _split,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pyimouapi.capability.catalog import CapabilitySet
from pyimouapi.device import ImouDeviceManager
from pyimouapi.exceptions import ImouException
from pyimouapi.ha_device import ImouHaDevice
from pyimouapi.local_record import LocalRecordSegment
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


def _record_device(
    device_id: str,
    channel_id: str,
    services: frozenset[str] | None,
) -> ImouHaDevice:
    device = ImouHaDevice(device_id, "Camera", "Imou", "IPC", "1.0")
    device.set_channel_id(channel_id)
    if services is not None:
        device.set_capability_set(
            CapabilitySet(
                properties={},
                services=services,
                events=frozenset(),
            )
        )
    return device


def _record_browse_source(
    hass: HomeAssistant,
    devices: list[ImouHaDevice],
    *,
    options: dict | None = None,
) -> tuple[ImouRecordMediaSource, MockConfigEntry, MagicMock]:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        title="Imou test",
        options=options,
    )
    entry.add_to_hass(hass)
    object.__setattr__(entry, "state", ConfigEntryState.LOADED)
    coordinator = MagicMock()
    coordinator.devices_by_key = {
        imou_life_device_key(device): device for device in devices
    }
    coordinator.get_device.side_effect = coordinator.devices_by_key.get
    entry.runtime_data = SimpleNamespace(coordinator=coordinator)
    return ImouRecordMediaSource(hass), entry, coordinator


async def _browse_record_kinds(
    source: ImouRecordMediaSource,
    entry: MockConfigEntry,
    *,
    device_id: str = "SN1",
    channel_id: str = "0",
) -> list[str]:
    browse = await source._browse_kinds(
        [f"entry:{entry.entry_id}", f"cam:{device_id}:{channel_id}"]
    )
    return [child.title for child in browse.children or []]


@pytest.mark.parametrize(
    "services",
    [
        None,
        frozenset(),
        frozenset({"record.file.bitmap"}),
        frozenset({"record.file.list"}),
    ],
)
async def test_local_folder_hidden_without_both_record_capabilities(
    hass: HomeAssistant, services: frozenset[str] | None
) -> None:
    """Local recordings require both bitmap and list services."""
    source, entry, _ = _record_browse_source(
        hass, [_record_device("SN1", "0", services)]
    )

    assert await _browse_record_kinds(source, entry) == [
        "Cloud recordings",
        "Record on alarm",
    ]


async def test_local_bitmap_failure_lists_no_days(hass: HomeAssistant) -> None:
    """A failed local bitmap leaves the month empty."""
    from pyimouapi.exceptions import ImouException

    device = _record_device(
        "SN1",
        "0",
        frozenset({"record.file.bitmap", "record.file.list"}),
    )
    source, entry, coordinator = _record_browse_source(hass, [device])
    manager = coordinator.device_manager.delegate
    manager.async_local_record_bitmap = AsyncMock(side_effect=ImouException("down"))

    browse = await source._browse_days(
        [
            f"entry:{entry.entry_id}",
            "cam:SN1:0",
            "local",
            "month:2026-11",
        ]
    )

    assert browse.children == []


async def test_local_folder_hidden_when_device_is_missing(
    hass: HomeAssistant,
) -> None:
    """A missing coordinator device does not expose local recordings."""
    source, entry, _ = _record_browse_source(hass, [])

    assert await _browse_record_kinds(source, entry) == [
        "Cloud recordings",
        "Record on alarm",
    ]


async def test_local_folder_shown_with_both_record_services(
    hass: HomeAssistant,
) -> None:
    """Both local-recording services expose the local folder."""
    source, entry, _ = _record_browse_source(
        hass,
        [
            _record_device(
                "SN1",
                "0",
                frozenset({"record.file.bitmap", "record.file.list"}),
            )
        ],
    )

    assert await _browse_record_kinds(source, entry) == [
        "Cloud recordings",
        "Local recordings",
        "Record on alarm",
    ]


async def test_local_folder_uses_channel_device_capabilities(
    hass: HomeAssistant,
) -> None:
    """A channel camera resolves the matching coordinator device."""
    source, entry, _ = _record_browse_source(
        hass,
        [
            _record_device("SN1", "0", frozenset()),
            _record_device(
                "SN1",
                "1",
                frozenset({"record.file.bitmap", "record.file.list"}),
            ),
        ],
    )

    assert "Local recordings" not in await _browse_record_kinds(source, entry)
    assert "Local recordings" in await _browse_record_kinds(
        source, entry, channel_id="1"
    )


async def test_local_folder_reflects_capability_rediscovery(
    hass: HomeAssistant,
) -> None:
    """Each browse reads the latest coordinator device capability set."""
    device = _record_device("SN1", "0", frozenset())
    source, entry, _ = _record_browse_source(hass, [device])
    assert "Local recordings" not in await _browse_record_kinds(source, entry)

    device.set_capability_set(
        CapabilitySet(
            properties={},
            services=frozenset({"record.file.bitmap", "record.file.list"}),
            events=frozenset(),
        )
    )

    assert "Local recordings" in await _browse_record_kinds(source, entry)


async def test_cloud_folder_stays_visible_without_record_data(
    hass: HomeAssistant,
) -> None:
    """Cloud browsing is offered without a device, records, or a query."""
    source, entry, coordinator = _record_browse_source(hass, [])
    coordinator.device_manager.delegate.async_query_cloud_record_bitmap.side_effect = (
        RuntimeError("query unavailable")
    )

    assert await _browse_record_kinds(source, entry) == [
        "Cloud recordings",
        "Record on alarm",
    ]
    coordinator.device_manager.delegate.async_query_cloud_record_bitmap.assert_not_called()


async def test_alarm_folder_is_always_in_the_media_browser(
    hass: HomeAssistant,
) -> None:
    """Alarm clips live under Media, so the folder does not need a saved path."""
    source, entry, _ = _record_browse_source(hass, [])
    titles = await _browse_record_kinds(source, entry)
    assert "Record on alarm" in titles


async def test_local_day_uses_aggregate_record_list(
    hass: HomeAssistant,
) -> None:
    """A local day uses the public aggregate and keeps DAV paths as metadata."""
    device = _record_device(
        "SN1",
        "0",
        frozenset({"record.file.bitmap", "record.file.list"}),
    )
    device.set_product_id("pid")
    source, entry, coordinator = _record_browse_source(hass, [device])
    manager = coordinator.device_manager.delegate
    dav_path = "/mnt/sd/2026-11-01/001/dav/00/00.06.55-00.08.10[M][0@0][0].dav"
    manager.async_list_local_record_segments = AsyncMock(
        return_value=[
            LocalRecordSegment(
                file_name=dav_path,
                begin_time="2026-11-01 00:06:55",
                end_time="2026-11-01 00:08:10",
            )
        ]
    )
    manager.async_local_record_list = AsyncMock()

    browse = await source._browse_clips(
        [
            f"entry:{entry.entry_id}",
            "cam:SN1:0",
            "local",
            "month:2026-11",
            "day:01",
        ]
    )

    manager.async_list_local_record_segments.assert_awaited_once_with(
        "SN1",
        "0",
        begin_time="2026-11-01 00:00:00",
        end_time="2026-11-01 23:59:59",
        product_id="pid",
    )
    manager.async_local_record_list.assert_not_awaited()
    assert len(browse.children or []) == 1
    child = (browse.children or [])[0]
    assert child.title == "00:06:55 - 00:08:10"
    assert dav_path not in (child.identifier or "")


async def test_local_day_is_empty_when_aggregate_fails(
    hass: HomeAssistant,
) -> None:
    """An unavailable SD card leaves the selected day empty."""
    device = _record_device(
        "SN1",
        "0",
        frozenset({"record.file.bitmap", "record.file.list"}),
    )
    source, entry, coordinator = _record_browse_source(hass, [device])
    manager = coordinator.device_manager.delegate
    manager.async_list_local_record_segments = AsyncMock(
        side_effect=ImouException("SD card unavailable")
    )

    browse = await source._browse_clips(
        [
            f"entry:{entry.entry_id}",
            "cam:SN1:0",
            "local",
            "month:2026-11",
            "day:01",
        ]
    )

    manager.async_list_local_record_segments.assert_awaited_once()
    assert browse.children == []
    assert browse.can_expand is False


async def test_local_month_bitmap_forwards_product_id(
    hass: HomeAssistant,
) -> None:
    """The month bitmap uses the same product identity as the recording list."""
    device = _record_device(
        "SN1",
        "0",
        frozenset({"record.file.bitmap", "record.file.list"}),
    )
    device.set_product_id("pid")
    source, entry, coordinator = _record_browse_source(hass, [device])
    manager = coordinator.device_manager.delegate
    manager.async_local_record_bitmap = AsyncMock(
        return_value={"outputData": {"bitmap": "1"}}
    )

    await source._browse_days(
        [
            f"entry:{entry.entry_id}",
            "cam:SN1:0",
            "local",
            "month:2026-11",
        ]
    )

    manager.async_local_record_bitmap.assert_awaited_once_with(
        "SN1",
        "0",
        year=2026,
        month=11,
        product_id="pid",
    )


async def test_library_playback_expiry_reaches_media_source() -> None:
    """Playback expiry survives the real library response instead of a mock shape."""
    client = AsyncMock()
    client.async_request_api.return_value = {
        "url": "https://example.test/record.m3u8",
        "expireTime": 1_800_000_000,
    }
    manager = ImouDeviceManager(client)

    data = await manager.async_create_device_record_hls(
        "SN1",
        "0",
        begin_time="2026-11-01 00:06:55",
        end_time="2026-11-01 00:08:10",
        record_type="cloudRecord",
    )

    assert _record_hls_expiry(data) == 1_800_000_000
