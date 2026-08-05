"""Tests for refresh behaviour when status polling is disabled."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from custom_components.imou_life.button import ImouButton
from custom_components.imou_life.const import (
    DOMAIN,
    PARAM_ENABLE_POLLING,
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
async def test_button_skips_refresh_when_polling_disabled(hass) -> None:
    """Button press does not refresh coordinator when enable_polling is false."""
    device = _mock_device()
    coordinator = _mock_coordinator(device)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={PARAM_ENABLE_POLLING: False},
    )
    entry.add_to_hass(hass)

    button = ImouButton(coordinator, entry, PARAM_RESTART_DEVICE, device)
    await button._async_do_press(500)

    coordinator.device_manager.async_press_button.assert_awaited_once()
    coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_button_refreshes_when_polling_enabled(hass) -> None:
    """Button press refreshes coordinator when enable_polling is true."""
    device = _mock_device()
    coordinator = _mock_coordinator(device)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={PARAM_ENABLE_POLLING: True},
    )
    entry.add_to_hass(hass)

    button = ImouButton(coordinator, entry, PARAM_RESTART_DEVICE, device)
    await button._async_do_press(500)

    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_text_skips_refresh_when_polling_disabled(hass) -> None:
    """Text write does not refresh coordinator when enable_polling is false."""
    device = _mock_device()
    coordinator = _mock_coordinator(device)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={PARAM_ENABLE_POLLING: False},
    )
    entry.add_to_hass(hass)

    text = ImouText(coordinator, entry, "count_down_switch", device)
    await text.async_set_value("10")

    coordinator.device_manager.async_set_text_value.assert_awaited_once()
    coordinator.async_request_refresh.assert_not_awaited()
