"""Plug max power as a watt number instead of a text field."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from custom_components.imou_life.const import (
    DOMAIN,
    PARAM_OVERCHARGE_SWITCH,
    PARAM_STATUS,
    imou_life_device_key,
)
from custom_components.imou_life.number import (
    ImouOverchargeNumber,
    _iter_countdowns,
    _iter_overcharge,
)
from homeassistant.components.number import NumberDeviceClass, NumberMode
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant
from pyimouapi.const import PARAM_REF, PARAM_STATE
from pyimouapi.ha_device import DeviceStatus, ImouHaDevice
from pytest_homeassistant_custom_component.common import MockConfigEntry

from . import USER_INPUT


def _device(*, ref: str = "1008") -> MagicMock:
    device = MagicMock(spec=ImouHaDevice)
    device.device_id = "plug1"
    device.channel_id = None
    device.product_id = "prod1"
    device.device_name = "Plug"
    device.channel_name = None
    device.manufacturer = "Imou"
    device.model = "Plug"
    device.swversion = "1.0"
    device.sensors = {PARAM_STATUS: {PARAM_STATE: DeviceStatus.ONLINE.value}}
    device.texts = {
        PARAM_OVERCHARGE_SWITCH: {PARAM_STATE: "100", PARAM_REF: ref},
    }
    return device


def _coordinator(device: MagicMock) -> MagicMock:
    coordinator = MagicMock()
    key = imou_life_device_key(device)
    coordinator.devices = [device]
    coordinator.devices_by_key = {key: device}
    coordinator.last_update_success = True
    coordinator.device_manager.async_set_text_value = AsyncMock()
    return coordinator


def _number(hass: HomeAssistant, device: MagicMock) -> ImouOverchargeNumber:
    coordinator = _coordinator(device)
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    return ImouOverchargeNumber(coordinator, entry, PARAM_OVERCHARGE_SWITCH, device)


def test_overcharge_is_a_watt_number() -> None:
    """Home Assistant can show watts and a box instead of a text field."""
    number = object.__new__(ImouOverchargeNumber)
    assert number.device_class is NumberDeviceClass.POWER
    assert number.native_unit_of_measurement is UnitOfPower.WATT
    assert number.native_min_value == 5
    assert number.native_step == 1
    assert number.mode is NumberMode.BOX


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_typical_plug_caps_at_2500_w(hass: HomeAssistant) -> None:
    """Most plugs stop at 2500 W."""
    number = _number(hass, _device(ref="1008"))
    assert number.native_max_value == 2500
    assert number.native_value == 100


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_higher_capacity_plug_caps_at_3680_w(hass: HomeAssistant) -> None:
    """A few plugs accept a higher watt limit."""
    number = _number(hass, _device(ref="128900"))
    assert number.native_max_value == 3680


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_set_watts_writes_the_device(hass: HomeAssistant) -> None:
    """A write must reach the same cloud setter the text entity used."""
    device = _device()
    number = _number(hass, device)
    number.async_write_ha_state = MagicMock()

    await number.async_set_native_value(200)

    number.coordinator.device_manager.async_set_text_value.assert_awaited_once_with(
        device, PARAM_OVERCHARGE_SWITCH, "200"
    )
    number.async_write_ha_state.assert_called_once()


def test_iterators_split_countdown_and_overcharge() -> None:
    """Each number type is built only for devices that expose it."""
    device = _device()
    device.texts["count_down_switch"] = {PARAM_STATE: "0"}
    coordinator = _coordinator(device)

    assert {key for key, _ in _iter_overcharge(coordinator)} == {
        PARAM_OVERCHARGE_SWITCH
    }
    assert {key for key, _ in _iter_countdowns(coordinator)} == {"count_down_switch"}
