"""Tests for Imou Life reauth flow."""

from __future__ import annotations

import pytest
from custom_components.imou_life.const import DOMAIN, PARAM_APP_SECRET
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from . import USER_INPUT, patch_async_setup_entry


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow")
async def test_reauth_updates_app_secret(hass) -> None:
    """Reauth flow updates the stored App Secret and reloads."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    with patch_async_setup_entry() as mock_setup_entry:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={PARAM_APP_SECRET: "new_secret"},
        )

    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[PARAM_APP_SECRET] == "new_secret"
    assert len(mock_setup_entry.mock_calls) == 1


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow_exception")
async def test_reauth_rejects_invalid_secret(hass) -> None:
    """Reauth flow shows an error when the new App Secret is invalid."""
    from homeassistant.config_entries import SOURCE_REAUTH

    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={PARAM_APP_SECRET: "wrong_secret"},
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"]["base"] == "appIdOrSecret_invalid"
    assert entry.data[PARAM_APP_SECRET] == USER_INPUT["app_secret"]
