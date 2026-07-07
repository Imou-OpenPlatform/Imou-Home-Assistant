"""Tests for Imou Life setup and unload."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from custom_components.imou_life import async_migrate_entry, async_unload_entry
from custom_components.imou_life.const import DOMAIN, PARAM_WEBHOOK_ID
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
