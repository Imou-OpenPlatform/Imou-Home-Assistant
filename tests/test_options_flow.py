"""Tests for Imou Life options flow."""

from __future__ import annotations

import pytest
from custom_components.imou_life.config_flow import (
    SECTION_EVENT_PUSH_CALLBACK,
    SECTION_EVENT_PUSH_SUBSCRIPTIONS,
)
from custom_components.imou_life.const import (
    DOMAIN,
    PARAM_ENABLE_EVENT_PUSH,
    PARAM_ENABLE_POLLING,
    PARAM_EVENT_PUSH_TYPES,
    PARAM_UPDATE_INTERVAL,
    PARAM_WEBHOOK_URL,
)
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from . import USER_INPUT


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_flow_init_shows_general_settings(hass) -> None:
    """Options init step exposes polling and camera settings only."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    schema = result.get("data_schema") or result.get("schema")
    assert schema is not None
    assert PARAM_ENABLE_POLLING in schema.schema
    assert PARAM_UPDATE_INTERVAL in schema.schema
    assert PARAM_ENABLE_EVENT_PUSH not in schema.schema


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow_with_devices")
async def test_options_flow_event_push_step_shows_all_sections(hass) -> None:
    """Event push step always shows callback, subscriptions, and notifications."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {PARAM_UPDATE_INTERVAL: 120},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "event_push"
    schema = result.get("data_schema") or result.get("schema")
    assert schema is not None
    assert PARAM_ENABLE_EVENT_PUSH in schema.schema
    assert SECTION_EVENT_PUSH_CALLBACK in schema.schema
    assert SECTION_EVENT_PUSH_SUBSCRIPTIONS in schema.schema
    placeholders = result["description_placeholders"]
    assert placeholders["webhook_id"]
    assert placeholders["suggested_url"]


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow_with_devices")
async def test_options_flow_event_push_step_when_enabled(hass) -> None:
    """Event push step keeps subscription defaults when push is already enabled."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={PARAM_ENABLE_EVENT_PUSH: True},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {PARAM_UPDATE_INTERVAL: 120},
    )
    schema = result.get("data_schema") or result.get("schema")
    assert schema is not None
    assert SECTION_EVENT_PUSH_SUBSCRIPTIONS in schema.schema


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow_with_devices")
async def test_options_flow_event_push_flattens_sections(hass) -> None:
    """Section-based event push input is stored as flat options."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {PARAM_UPDATE_INTERVAL: 120},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            PARAM_ENABLE_EVENT_PUSH: True,
            SECTION_EVENT_PUSH_CALLBACK: {
                PARAM_WEBHOOK_URL: "https://example.test/hook",
            },
            SECTION_EVENT_PUSH_SUBSCRIPTIONS: {
                PARAM_EVENT_PUSH_TYPES: ["alarm"],
            },
        },
    )
    assert result["step_id"] == "devices"
