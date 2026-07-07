"""Tests for Imou Life diagnostics."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from custom_components.imou_life.const import DOMAIN, PARAM_WEBHOOK_ID
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
