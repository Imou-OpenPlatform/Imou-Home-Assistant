"""Test for the Imou integration."""

import pytest
from custom_components.imou_life.const import (
    DOMAIN,
    PARAM_SELECTED_DEVICES,
    PARAM_WEBHOOK_ID,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from . import USER_INPUT, patch_async_setup_entry


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow")
async def test_async_step_user_without_user_input(hass: HomeAssistant) -> None:
    """Test async_step_user with no user input."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "login"
    with patch_async_setup_entry() as mock_setup_entry:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=USER_INPUT
        )

    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert {key: result["data"][key] for key in USER_INPUT} == USER_INPUT
    assert PARAM_WEBHOOK_ID in result["data"]
    assert len(mock_setup_entry.mock_calls) == 1
    await hass.async_block_till_done()


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow_exception")
async def test_async_step_user_with_user_input_fail(hass: HomeAssistant) -> None:
    """Test async_step_user with user input fail."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "login"
    with patch_async_setup_entry():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=USER_INPUT
        )

    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "login"
    assert result["errors"]["base"] == "appIdOrSecret_invalid"


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow_exception")
async def test_async_step_user_with_user_input(hass: HomeAssistant) -> None:
    """Test async_step_user with user input success."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "login"
    with patch_async_setup_entry():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=USER_INPUT
        )

    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "login"
    assert result["errors"]["base"] == "appIdOrSecret_invalid"


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow_with_devices")
async def test_async_step_user_with_device_selection(hass: HomeAssistant) -> None:
    """Test setup flow device selection step."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "login"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input=USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "select_devices"

    with patch_async_setup_entry() as mock_setup_entry:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={PARAM_SELECTED_DEVICES: ["device_1"]},
        )

    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][PARAM_SELECTED_DEVICES] == ["device_1"]
    assert PARAM_WEBHOOK_ID in result["data"]
    assert len(mock_setup_entry.mock_calls) == 1
