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
    assert result["step_id"] == "devices_menu"
    assert set(result["menu_options"]) >= {"select_poll_devices", "bind_device"}


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow_with_devices")
async def test_options_devices_menu_select_poll_saves(hass) -> None:
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
        user_input={"next_step_id": "select_poll_devices"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "select_poll_devices"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={PARAM_SELECTED_DEVICES: ["device_1"]},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][PARAM_SELECTED_DEVICES] == ["device_1"]


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
    # The account holds nothing yet, so no filter is recorded; storing an empty
    # list would keep a device bound later from ever being polled.
    assert PARAM_SELECTED_DEVICES not in result["data"]


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow")
async def test_saving_with_no_devices_clears_a_stale_selection(hass) -> None:
    """Account listed empty: drop the old whitelist (devices deleted in the app).

    Keeping the previous ids would leave ghost devices after save/reload, and
    would also filter out anything bound later from the Imou app. Cloud failures
    still preserve the selection via save_without_devices.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={PARAM_SELECTED_DEVICES: ["device_1"]},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {PARAM_UPDATE_INTERVAL: 120}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _MIN_EVENT_PUSH_INPUT
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "finish_without_bind"}
    )

    assert PARAM_SELECTED_DEVICES not in result["data"]


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow")
async def test_saving_with_no_devices_clears_selection_stored_in_data(hass) -> None:
    """Setup stores the whitelist in entry.data; options must clear that too."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**USER_INPUT, PARAM_SELECTED_DEVICES: ["device_1"]},
        options={},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {PARAM_UPDATE_INTERVAL: 120}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _MIN_EVENT_PUSH_INPUT
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "finish_without_bind"}
    )

    assert PARAM_SELECTED_DEVICES not in result["data"]
    assert PARAM_SELECTED_DEVICES not in entry.data


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow")
async def test_unreachable_cloud_still_lets_the_other_options_be_saved(hass) -> None:
    """Listing the account is the last step, and it must not hold the rest hostage.

    A dead cloud is exactly when someone wants to slow polling down or turn
    event push off, so failing here has to leave a way to save.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={PARAM_SELECTED_DEVICES: ["device_1"]},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {PARAM_UPDATE_INTERVAL: 120}
    )
    with patch(
        "custom_components.imou_life.config_flow.async_build_device_map",
        AsyncMock(side_effect=RequestFailedException("cloud down")),
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], _MIN_EVENT_PUSH_INPUT
        )
        assert result["type"] is FlowResultType.MENU
        assert result["step_id"] == "devices_unavailable"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={"next_step_id": "save_without_devices"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][PARAM_UPDATE_INTERVAL] == 120
    assert result["data"][PARAM_ENABLE_EVENT_PUSH] is False
    # Nothing was learned about the account, so the selection is left as it was.
    assert result["data"][PARAM_SELECTED_DEVICES] == ["device_1"]


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow_with_devices")
async def test_retrying_after_an_unreachable_cloud_reaches_the_devices(hass) -> None:
    """The retry option has to actually go back and list the account."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {PARAM_UPDATE_INTERVAL: 120}
    )
    with patch(
        "custom_components.imou_life.config_flow.async_build_device_map",
        AsyncMock(side_effect=RequestFailedException("cloud down")),
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], _MIN_EVENT_PUSH_INPUT
        )
    assert result["step_id"] == "devices_unavailable"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "devices"}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "devices_menu"


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow_with_devices")
async def test_options_bind_from_devices_menu(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={PARAM_SELECTED_DEVICES: ["device_1"]},
    )
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
    assert result["step_id"] == "devices_menu"
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
            AsyncMock(
                return_value={
                    "device_1": "Front Door (IPC) [Online]",
                    "SN001": "Camera SN001 [Online]",
                }
            ),
        ),
    ):
        mock_mgr_cls.return_value.async_bind_device = AsyncMock()
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={"device_id": "SN001", "code": "123456"},
        )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "devices_menu"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "select_poll_devices"},
    )
    assert result["step_id"] == "select_poll_devices"
    schema = result["data_schema"].schema
    selected_key = next(iter(schema))
    assert selected_key.default() == ["device_1", "SN001"]


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
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "devices_menu"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "select_poll_devices"},
    )
    assert result["step_id"] == "select_poll_devices"
    schema = result["data_schema"].schema
    selected_key = next(iter(schema))
    assert "SN001" in selected_key.default()


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
    assert result["errors"]["base"] == "bind_failed"
