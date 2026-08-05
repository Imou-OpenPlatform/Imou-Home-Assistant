"""Test for the Imou integration."""

import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.imou_life.const import (
    CONF_API_URL_SG,
    DOMAIN,
    PARAM_SELECTED_DEVICES,
    PARAM_WEBHOOK_ID,
    api_url_from_region,
    api_url_region_from_value,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pyimouapi.exceptions import RequestFailedException

from . import LOGIN_INPUT, USER_INPUT, patch_async_setup_entry

_CONFIG_FLOW_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "imou_life"
    / "config_flow.py"
)
# Config flow must stay i18n-only: no hardcoded Chinese or direct openapi paths.
assert not re.search(r"[\u4e00-\u9fff]", _CONFIG_FLOW_PATH.read_text(encoding="utf-8"))
assert "/openapi/" not in _CONFIG_FLOW_PATH.read_text(encoding="utf-8")


def test_api_url_region_mapping() -> None:
    """Login region keys map to stored hostnames and back."""
    assert api_url_from_region("sg") == CONF_API_URL_SG
    assert api_url_region_from_value(CONF_API_URL_SG) == "sg"
    assert api_url_region_from_value("sg") == "sg"


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow_with_devices")
async def test_async_step_user_without_user_input(hass: HomeAssistant) -> None:
    """Test async_step_user with no user input."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    with patch_async_setup_entry() as mock_setup_entry:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=LOGIN_INPUT
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "select_devices"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={PARAM_SELECTED_DEVICES: ["device_1", "device_2"]},
        )

    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Imou Life Official (test_app_id)"
    assert {key: result["data"][key] for key in USER_INPUT} == USER_INPUT
    assert PARAM_WEBHOOK_ID in result["data"]
    assert len(mock_setup_entry.mock_calls) == 1
    await hass.async_block_till_done()


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow")
async def test_async_step_user_aborts_when_no_devices(hass: HomeAssistant) -> None:
    """Setup aborts when the account has no devices."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input=LOGIN_INPUT
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_async_step_user_aborts_with_api_error_detail(
    hass: HomeAssistant,
) -> None:
    """Device-list API errors are shown in the abort placeholders."""
    with (
        patch(
            "custom_components.imou_life.config_flow.ImouOpenApiClient",
        ) as mock_client,
        patch(
            "custom_components.imou_life.config_flow.async_build_device_map",
            AsyncMock(
                side_effect=RequestFailedException(
                    "OP1013:Call interface times exceed limit (total)."
                )
            ),
        ),
    ):
        instance = MagicMock()
        instance.async_get_token = AsyncMock()
        instance.async_close = AsyncMock()
        mock_client.return_value = instance

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=LOGIN_INPUT
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "request_failed"
    assert "OP1013" in result["description_placeholders"]["error"]
    assert "exceed limit" in result["description_placeholders"]["error"]


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow_exception")
async def test_async_step_user_with_user_input_fail(hass: HomeAssistant) -> None:
    """Test async_step_user with user input fail."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    with patch_async_setup_entry():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=LOGIN_INPUT
        )

    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"]["base"] == "invalid_auth"


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow_exception")
async def test_async_step_user_with_user_input(hass: HomeAssistant) -> None:
    """Test async_step_user with user input success."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}, data=LOGIN_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    with patch_async_setup_entry():
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=LOGIN_INPUT
        )

    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"]["base"] == "invalid_auth"


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow_with_devices")
async def test_async_step_user_with_device_selection(hass: HomeAssistant) -> None:
    """Test setup flow device selection step."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input=LOGIN_INPUT
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
    assert result["title"] == "Imou Life Official (test_app_id)"
    assert result["data"][PARAM_SELECTED_DEVICES] == ["device_1"]
    assert PARAM_WEBHOOK_ID in result["data"]
    assert len(mock_setup_entry.mock_calls) == 1
