"""Tests for optimistic entity writes (no post-write coordinator refresh)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from custom_components.imou_life.button import ImouButton
from custom_components.imou_life.const import (
    DOMAIN,
    PARAM_RESTART_DEVICE,
    imou_life_device_key,
)
from custom_components.imou_life.text import ImouText
from pyimouapi.const import PARAM_STATE
from pyimouapi.ha_device import ImouHaDevice
from pytest_homeassistant_custom_component.common import MockConfigEntry

from . import USER_INPUT


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
    device.texts = {"count_down_switch": {PARAM_STATE: "0"}}
    return device


def _mock_coordinator(device: MagicMock) -> MagicMock:
    coordinator = MagicMock()
    device_key = imou_life_device_key(device)
    coordinator.devices_by_key = {device_key: device}
    coordinator.device_manager.async_press_button = AsyncMock()
    coordinator.device_manager.async_set_text_value = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_button_press_does_not_refresh(hass) -> None:
    """Button press does not trigger a coordinator refresh."""
    device = _mock_device()
    coordinator = _mock_coordinator(device)
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)

    button = ImouButton(coordinator, entry, PARAM_RESTART_DEVICE, device)
    await button._async_do_press(500)

    coordinator.device_manager.async_press_button.assert_awaited_once()
    coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_text_set_value_does_not_refresh(hass) -> None:
    """Text write updates HA state without a coordinator refresh."""
    device = _mock_device()
    coordinator = _mock_coordinator(device)
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)

    text = ImouText(coordinator, entry, "count_down_switch", device)
    text.async_write_ha_state = MagicMock()
    await text.async_set_value("10")

    coordinator.device_manager.async_set_text_value.assert_awaited_once()
    coordinator.async_request_refresh.assert_not_awaited()
    text.async_write_ha_state.assert_called_once()
