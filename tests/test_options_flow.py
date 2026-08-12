"""Tests for Imou Life options flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from custom_components.imou_life.config_flow import (
    SECTION_EVENT_PUSH_CALLBACK,
    SECTION_EVENT_PUSH_SUBSCRIPTIONS,
)
from custom_components.imou_life.const import (
    CONF_HD,
    CONF_HTTPS,
    DOMAIN,
    PARAM_DOWNLOAD_SNAP_WAIT_TIME,
    PARAM_ENABLE_EVENT_PUSH,
    PARAM_ENABLE_POLLING,
    PARAM_EVENT_PUSH_TYPES,
    PARAM_LIVE_PROTOCOL,
    PARAM_LIVE_RESOLUTION,
    PARAM_ROTATION_DURATION,
    PARAM_SELECTED_DEVICES,
    PARAM_UPDATE_INTERVAL,
    PARAM_WEBHOOK_URL,
)
from homeassistant.data_entry_flow import FlowResultType
from pyimouapi.exceptions import RequestFailedException
from pytest_homeassistant_custom_component.common import MockConfigEntry

from . import USER_INPUT


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_flow_init_shows_menu(hass) -> None:
    """Options init is a menu to pick which settings to edit."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "init"
    assert set(result["menu_options"]) >= {
        "general_settings",
        "event_push",
        "devices",
    }


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_general_settings_saves_without_devices(hass) -> None:
    """Editing general settings alone must not require the devices steps."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={
            PARAM_ENABLE_EVENT_PUSH: True,
            PARAM_SELECTED_DEVICES: ["device_1"],
            PARAM_UPDATE_INTERVAL: 60,
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": "general_settings"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "general_settings"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            PARAM_ENABLE_POLLING: True,
            PARAM_UPDATE_INTERVAL: 120,
            PARAM_DOWNLOAD_SNAP_WAIT_TIME: 3,
            PARAM_LIVE_RESOLUTION: CONF_HD,
            PARAM_LIVE_PROTOCOL: CONF_HTTPS,
            PARAM_ROTATION_DURATION: 500,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][PARAM_UPDATE_INTERVAL] == 120
    assert result["data"][PARAM_ENABLE_EVENT_PUSH] is True
    assert result["data"][PARAM_SELECTED_DEVICES] == ["device_1"]


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow_with_devices")
async def test_options_flow_event_push_step_shows_all_sections(hass) -> None:
    """Event push step always shows callback, subscriptions, and notifications."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": "event_push"},
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
        {"next_step_id": "event_push"},
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
        {"next_step_id": "event_push"},
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
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][PARAM_ENABLE_EVENT_PUSH] is True
    assert result["data"][PARAM_WEBHOOK_URL] == "https://example.test/hook"
    assert result["data"][PARAM_EVENT_PUSH_TYPES] == ["alarm"]


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow_with_devices")
async def test_options_event_push_preserves_general_and_devices(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={
            PARAM_UPDATE_INTERVAL: 90,
            PARAM_SELECTED_DEVICES: ["device_1"],
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "event_push"}
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
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][PARAM_ENABLE_EVENT_PUSH] is True
    assert result["data"][PARAM_WEBHOOK_URL] == "https://example.test/hook"
    assert result["data"][PARAM_UPDATE_INTERVAL] == 90
    assert result["data"][PARAM_SELECTED_DEVICES] == ["device_1"]


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow_with_devices")
async def test_options_select_poll_returns_to_menu_then_save_and_finish(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={PARAM_UPDATE_INTERVAL: 60},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "devices"}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "devices_menu"
    assert set(result["menu_options"]) >= {
        "select_poll_devices",
        "bind_device",
        "save_and_finish",
    }

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "select_poll_devices"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {PARAM_SELECTED_DEVICES: ["device_1"]},
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "devices_menu"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "save_and_finish"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][PARAM_SELECTED_DEVICES] == ["device_1"]
    assert result["data"][PARAM_UPDATE_INTERVAL] == 60


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow")
async def test_options_devices_empty_shows_menu(hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": "devices"},
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "no_devices_menu"
    assert set(result["menu_options"]) >= {"bind_device", "finish_without_bind"}


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow")
async def test_options_finish_without_bind_saves_options(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={
            PARAM_UPDATE_INTERVAL: 120,
            PARAM_ENABLE_EVENT_PUSH: False,
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": "devices"},
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
        result["flow_id"], {"next_step_id": "devices"}
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
        result["flow_id"], {"next_step_id": "devices"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "finish_without_bind"}
    )

    assert PARAM_SELECTED_DEVICES not in result["data"]
    assert PARAM_SELECTED_DEVICES not in entry.data


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow")
async def test_unreachable_cloud_still_lets_the_other_options_be_saved(hass) -> None:
    """Unreachable cloud in Manage devices must still allow save without devices.

    Listing failure must not trap the user; existing general options stay intact
    when they choose save without fetching the device list.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={
            PARAM_UPDATE_INTERVAL: 120,
            PARAM_SELECTED_DEVICES: ["device_1"],
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    with patch(
        "custom_components.imou_life.config_flow.async_build_device_map",
        AsyncMock(side_effect=RequestFailedException("cloud down")),
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "devices"}
        )
        assert result["type"] is FlowResultType.MENU
        assert result["step_id"] == "devices_unavailable"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={"next_step_id": "save_without_devices"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][PARAM_UPDATE_INTERVAL] == 120
    # Nothing was learned about the account, so the selection is left as it was.
    assert result["data"][PARAM_SELECTED_DEVICES] == ["device_1"]


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow_with_devices")
async def test_retrying_after_an_unreachable_cloud_reaches_the_devices(hass) -> None:
    """The retry option has to actually go back and list the account."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    with patch(
        "custom_components.imou_life.config_flow.async_build_device_map",
        AsyncMock(side_effect=RequestFailedException("cloud down")),
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "devices"}
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
        result["flow_id"], {"next_step_id": "devices"}
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
        user_input={"next_step_id": "save_and_finish"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][PARAM_SELECTED_DEVICES] == ["device_1", "SN001"]


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow")
async def test_options_bind_device_success_merges_selection(hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "devices"}
    )
    assert result["step_id"] == "no_devices_menu"
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
        user_input={"next_step_id": "save_and_finish"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][PARAM_SELECTED_DEVICES] == ["SN001"]


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow")
async def test_options_bind_device_failure_stays_on_form(hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": "devices"},
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
