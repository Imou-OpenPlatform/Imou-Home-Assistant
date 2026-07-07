"""Tests for Imou Life options flow."""

from __future__ import annotations

import pytest
from custom_components.imou_life.const import DOMAIN, PARAM_ENABLE_EVENT_PUSH
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from . import USER_INPUT


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_flow_init_shows_event_push(hass) -> None:
    """Options init step exposes event push settings."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    schema = result.get("data_schema") or result.get("schema")
    assert schema is not None
    assert PARAM_ENABLE_EVENT_PUSH in schema.schema
