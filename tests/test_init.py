"""Tests for Imou Life setup and unload."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.imou_life import (
    async_migrate_entry,
    async_remove_config_entry_device,
    async_unload_entry,
)
from custom_components.imou_life.const import (
    DOMAIN,
    PARAM_SELECTED_DEVICES,
    PARAM_WEBHOOK_ID,
)
from custom_components.imou_life.runtime_data import ImouRuntimeData
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from . import USER_INPUT


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_migrate_entry_adds_missing_webhook_id(hass) -> None:
    """Legacy v1 entries receive a webhook_id during migration."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, version=1)
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is True
    assert entry.version == 2
    assert PARAM_WEBHOOK_ID in entry.data
    assert entry.data[PARAM_WEBHOOK_ID]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_unload_keeps_device_registry(hass) -> None:
    """Unload must not bulk-remove device registry entries."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    entry.runtime_data = ImouRuntimeData(coordinator=AsyncMock())

    device_registry = dr.async_get(hass)
    device_entry = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "device_key_1")},
        name="Front Door",
    )

    with patch(
        "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
        AsyncMock(return_value=True),
    ):
        assert await async_unload_entry(hass, entry) is True

    assert device_registry.async_get(device_entry.id) is not None


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_remove_device_updates_selected_devices(hass) -> None:
    """Removing a device persists exclusion in options so poll won't re-add it."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**USER_INPUT, PARAM_SELECTED_DEVICES: ["d1", "d2"]},
        options={PARAM_SELECTED_DEVICES: ["d1", "d2"]},
    )
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.devices_by_key = {}
    entry.runtime_data = ImouRuntimeData(
        coordinator=coordinator, selected_devices=["d1", "d2"]
    )

    device_registry = dr.async_get(hass)
    device_entry = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "d1_0")},
        serial_number="d1",
        name="Cam 1",
    )

    assert await async_remove_config_entry_device(hass, entry, device_entry) is True
    assert entry.options[PARAM_SELECTED_DEVICES] == ["d2"]
    assert entry.runtime_data.selected_devices == ["d2"]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_remove_device_materializes_allow_list_when_all_selected(hass) -> None:
    """When selection means all devices, remove persists remaining ids as allow-list."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)

    d1 = MagicMock()
    d1.device_id = "d1"
    d2 = MagicMock()
    d2.device_id = "d2"
    coordinator = MagicMock()
    coordinator.devices_by_key = {"d1_0": d1, "d2_0": d2}
    entry.runtime_data = ImouRuntimeData(coordinator=coordinator, selected_devices=None)

    device_registry = dr.async_get(hass)
    device_entry = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "d1_0")},
        serial_number="d1",
        name="Cam 1",
    )

    assert await async_remove_config_entry_device(hass, entry, device_entry) is True
    assert entry.options[PARAM_SELECTED_DEVICES] == ["d2"]
    assert entry.runtime_data.selected_devices == ["d2"]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_removing_one_channel_does_not_take_its_siblings(hass) -> None:
    """Exclusion is per account device, so one channel cannot be expressed in it.

    An NVR and a multi-lens camera arrive as one account device carrying several
    channels, each of which becomes its own device here. Excluding the account
    device would drop the siblings out of Home Assistant, losing whatever the
    user had named, placed, or automated on them.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={PARAM_SELECTED_DEVICES: ["nvr1", "d2"]},
    )
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.devices_by_key = {}
    entry.runtime_data = ImouRuntimeData(
        coordinator=coordinator, selected_devices=["nvr1", "d2"]
    )

    device_registry = dr.async_get(hass)
    channel_0 = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "nvr1_0")},
        serial_number="nvr1",
        name="Front Door",
    )
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "nvr1_1")},
        serial_number="nvr1",
        name="Driveway",
    )

    assert await async_remove_config_entry_device(hass, entry, channel_0) is False
    assert entry.options[PARAM_SELECTED_DEVICES] == ["nvr1", "d2"]
    assert entry.runtime_data.selected_devices == ["nvr1", "d2"]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_another_device_is_not_mistaken_for_a_sibling_channel(hass) -> None:
    """Only channels of the same account device count; other cameras must not."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={PARAM_SELECTED_DEVICES: ["d1", "d2"]},
    )
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.devices_by_key = {}
    entry.runtime_data = ImouRuntimeData(
        coordinator=coordinator, selected_devices=["d1", "d2"]
    )

    device_registry = dr.async_get(hass)
    device_entry = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "d1_0")},
        serial_number="d1",
        name="Cam 1",
    )
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "d2_0")},
        serial_number="d2",
        name="Cam 2",
    )

    assert await async_remove_config_entry_device(hass, entry, device_entry) is True
    assert entry.options[PARAM_SELECTED_DEVICES] == ["d2"]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_remove_device_without_runtime_refuses(hass) -> None:
    """Without runtime, do not rewrite 'all' into an empty allow-list."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)

    device_registry = dr.async_get(hass)
    device_entry = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "d1_0")},
        serial_number="d1",
        name="Cam 1",
    )

    assert await async_remove_config_entry_device(hass, entry, device_entry) is False
    assert PARAM_SELECTED_DEVICES not in entry.options


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_remove_device_refuses_empty_coordinator_map(hass) -> None:
    """Do not materialize an empty allow-list when devices_by_key is empty."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.devices_by_key = {}
    entry.runtime_data = ImouRuntimeData(coordinator=coordinator, selected_devices=None)

    device_registry = dr.async_get(hass)
    device_entry = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "d1_0")},
        serial_number="d1",
        name="Cam 1",
    )

    assert await async_remove_config_entry_device(hass, entry, device_entry) is False
    assert PARAM_SELECTED_DEVICES not in entry.options
