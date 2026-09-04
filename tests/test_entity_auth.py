"""Tests for credential rejection during entity actions."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from custom_components.imou_life.const import (
    DOMAIN,
    PARAM_MOTION_DETECT,
    imou_life_device_key,
)
from custom_components.imou_life.switch import ImouSwitch, SWITCH_TYPES
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.exceptions import HomeAssistantError
from pyimouapi.const import PARAM_STATE
from pyimouapi.exceptions import InvalidAppIdOrSecretException
from pyimouapi.ha_device import ImouHaDevice
from pytest_homeassistant_custom_component.common import MockConfigEntry

from . import USER_INPUT


def _motion_switch_description():
    for description in SWITCH_TYPES:
        if description.key == PARAM_MOTION_DETECT:
            return description
    raise AssertionError("motion_detect switch description missing")


def _mock_device() -> MagicMock:
    device = MagicMock(spec=ImouHaDevice)
    device.device_id = "dev1"
    device.channel_id = "0"
    device.product_id = "prod1"
    device.device_name = "Front Door"
    device.channel_name = "Front Door"
    device.manufacturer = "Imou"
    device.model = "TestCam"
    device.swversion = "1.0.0"
    device.parent_device_id = None
    device.parent_product_id = None
    device.switches = {PARAM_MOTION_DETECT: {PARAM_STATE: False}}
    device.sensors = {"status": {PARAM_STATE: "online"}}
    return device


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_switch_turn_on_starts_reauth_on_invalid_credentials(hass) -> None:
    """A rejected switch write opens reauth instead of only showing an error."""
    device = _mock_device()
    device_key = imou_life_device_key(device)
    coordinator = MagicMock()
    coordinator.devices_by_key = {device_key: device}
    coordinator.device_manager.async_switch_operation = AsyncMock(
        side_effect=InvalidAppIdOrSecretException("bad secret")
    )
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)

    switch = ImouSwitch(
        coordinator,
        entry,
        _motion_switch_description(),
        device,
    )
    switch.hass = hass
    switch.async_write_ha_state = MagicMock()

    with pytest.raises(HomeAssistantError) as exc_info:
        await switch.async_turn_on()
    await hass.async_block_till_done()

    assert exc_info.value.translation_key == "invalid_auth"
    assert entry.async_get_active_flows(hass, {SOURCE_REAUTH})
