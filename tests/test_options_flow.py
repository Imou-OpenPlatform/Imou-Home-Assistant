"""Tests for Imou Life options flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

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
    PARAM_SELECTED_DEVICES,
    PARAM_UPDATE_INTERVAL,
    PARAM_WEBHOOK_URL,
)
from homeassistant.data_entry_flow import FlowResultType
from pyimouapi.exceptions import RequestFailedException
from pytest_homeassistant_custom_component.common import MockConfigEntry

from . import USER_INPUT

_MIN_EVENT_PUSH_INPUT = {
    PARAM_ENABLE_EVENT_PUSH: False,
    SECTION_EVENT_PUSH_CALLBACK: {PARAM_WEBHOOK_URL: ""},
    SECTION_EVENT_PUSH_SUBSCRIPTIONS: {PARAM_EVENT_PUSH_TYPES: ["alarm"]},
}


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


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow")
async def test_options_devices_empty_shows_menu(hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {PARAM_UPDATE_INTERVAL: 120},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        _MIN_EVENT_PUSH_INPUT,
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "no_devices_menu"
    assert set(result["menu_options"]) >= {"bind_device", "finish_without_bind"}


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow")
async def test_options_finish_without_bind_saves_options(hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {PARAM_UPDATE_INTERVAL: 120},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        _MIN_EVENT_PUSH_INPUT,
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "finish_without_bind"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][PARAM_UPDATE_INTERVAL] == 120
    assert result["data"][PARAM_ENABLE_EVENT_PUSH] is False
    assert result["data"][PARAM_SELECTED_DEVICES] == []


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow")
async def test_options_bind_device_success_merges_selection(hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {PARAM_UPDATE_INTERVAL: 120},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        _MIN_EVENT_PUSH_INPUT,
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "bind_device"},
    )
    assert result["step_id"] == "bind_device"
    with (
        patch(
            "custom_components.imou_life.config_flow.ImouDeviceManager",
        ) as mock_mgr_cls,
        patch(
            "custom_components.imou_life.config_flow.async_build_device_map",
            AsyncMock(return_value={"SN001": "Camera SN001 [Online]"}),
        ),
    ):
        mock_mgr_cls.return_value.async_bind_device = AsyncMock()
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={"device_id": "SN001", "code": "123456"},
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert "SN001" in result["data"][PARAM_SELECTED_DEVICES]


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow")
async def test_options_bind_device_failure_stays_on_form(hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {PARAM_UPDATE_INTERVAL: 120},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        _MIN_EVENT_PUSH_INPUT,
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "bind_device"},
    )
    with patch(
        "custom_components.imou_life.config_flow.ImouDeviceManager",
    ) as mock_mgr_cls:
        mock_mgr_cls.return_value.async_bind_device = AsyncMock(
            side_effect=RequestFailedException("OP1009:device already bound")
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={"device_id": "SN001", "code": "bad"},
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bind_device"
    assert result["errors"]["base"] == "request_failed"
