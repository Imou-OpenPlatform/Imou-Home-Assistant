"""Tests for Imou Life diagnostics."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from custom_components.imou_life.const import (
    DOMAIN,
    PARAM_EVENT_PUSH_TYPES,
    PARAM_WEBHOOK_ID,
    imou_life_device_key,
)
from custom_components.imou_life.diagnostics import (
    async_get_config_entry_diagnostics,
    async_get_device_diagnostics,
)
from custom_components.imou_life.runtime_data import ImouRuntimeData
from homeassistant.helpers import device_registry as dr
from pyimouapi.const import PARAM_REF, PARAM_STATE, PARAM_SUPPORTED, PARAM_VALUE_TYPE
from pyimouapi.ha_device import DeviceStatus, ImouHaDevice
from pytest_homeassistant_custom_component.common import MockConfigEntry

from . import USER_INPUT


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_diagnostics_redacts_secrets(hass) -> None:
    """Diagnostics must not expose App Secret."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**USER_INPUT, PARAM_WEBHOOK_ID: "abcd1234efgh5678"},
        options={"enable_event_push": True},
    )
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.last_update_success = True
    entry.runtime_data = ImouRuntimeData(coordinator=coordinator)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert "app_secret" not in result
    assert result["app_id"] == "test…"
    assert result["webhook_id"] == "abcd1234…"
    assert result["event_push_enabled"] is True
    assert result["last_update_success"] is True
    assert "pyimouapi_version" in result
    assert result["event_push"]["enabled"] is True
    assert result["event_push"]["recent_msg_type_counts"] == {}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_diagnostics_includes_push_msg_counts(hass) -> None:
    """Diagnostics expose runtime push msgType counters."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**USER_INPUT, PARAM_WEBHOOK_ID: "abcd1234efgh5678"},
        options={
            "enable_event_push": True,
            PARAM_EVENT_PUSH_TYPES: ["alarm", "device_status"],
            "webhook_url": "https://example.com/hook",
        },
    )
    entry.add_to_hass(hass)
    runtime = ImouRuntimeData(coordinator=MagicMock())
    runtime.record_push_msg("closeCamera")
    runtime.record_push_msg("abAlarmSound")
    entry.runtime_data = runtime

    result = await async_get_config_entry_diagnostics(hass, entry)
    event_push = result["event_push"]

    assert event_push["enabled"] is True
    assert event_push["webhook_url_configured"] is True
    assert event_push["event_push_types"] == ["alarm", "device_status"]
    assert event_push["base_push"] == "1"
    assert event_push["recent_msg_type_counts"] == {
        "closeCamera": 1,
        "abAlarmSound": 1,
    }
    assert event_push["last_msg_type"] == "abAlarmSound"
    assert event_push["last_received_at"] is not None


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_device_diagnostics_includes_device_fields(hass) -> None:
    """Device diagnostics must carry ids, model, and entity summaries."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**USER_INPUT, PARAM_WEBHOOK_ID: "abcd1234efgh5678"},
        options={"selected_devices": ["SN123"]},
    )
    entry.add_to_hass(hass)

    device = ImouHaDevice("SN123", "Front", "Imou", "IPC-A1", "1.0.0")
    device.set_channel_id("0")
    device.set_product_id("PROD1")
    device.sensors["status"] = {PARAM_STATE: DeviceStatus.ONLINE.value}
    device.switches["motion_detect"] = {PARAM_STATE: True}
    device_key = imou_life_device_key(device)

    coordinator = MagicMock()
    coordinator.devices_by_key = {device_key: device}
    coordinator.last_update_success = True
    entry.runtime_data = ImouRuntimeData(
        coordinator=coordinator, selected_devices=["SN123"]
    )

    device_registry = dr.async_get(hass)
    device_entry = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, device_key)},
        name="Front",
        serial_number="SN123",
    )

    result = await async_get_device_diagnostics(hass, entry, device_entry)

    assert result["device_id"] == "SN123"
    assert result["channel_id"] == "0"
    assert result["product_id"] == "PROD1"
    assert result["model"] == "IPC-A1"
    assert result["manufacturer"] == "Imou"
    assert result["sw_version"] == "1.0.0"
    assert result["device_key"] == device_key
    assert result["present_in_coordinator"] is True
    assert result["selected"] is True
    assert result["status"] == DeviceStatus.ONLINE.value
    assert result["entities"]["switches"] == {"motion_detect": True}
    assert result["entities"]["alarm_control_panel"] is None

    panel = {
        PARAM_REF: "15200",
        PARAM_STATE: "away",
        PARAM_SUPPORTED: ["home", "away", "disarm"],
        PARAM_VALUE_TYPE: "int",
    }
    device.alarm_control_panel = panel
    result = await async_get_device_diagnostics(hass, entry, device_entry)
    assert result["entities"]["alarm_control_panel"] == panel
    assert "app_secret" not in result
