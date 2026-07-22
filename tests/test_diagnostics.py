"""Tests for Imou Life diagnostics."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from custom_components.imou_life.const import (
    DOMAIN,
    PARAM_EVENT_PUSH_TYPES,
    PARAM_WEBHOOK_ID,
)
from custom_components.imou_life.diagnostics import async_get_config_entry_diagnostics
from custom_components.imou_life.runtime_data import ImouRuntimeData
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
