"""Plug countdown as a single number that counts down locally."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from custom_components.imou_life.const import (
    DOMAIN,
    PARAM_COUNT_DOWN_SWITCH,
    PARAM_OVERCHARGE_SWITCH,
    PARAM_STATUS,
    imou_life_device_key,
)
from custom_components.imou_life.countdown import CountdownTracker
from custom_components.imou_life.number import ImouCountdownNumber
from custom_components.imou_life.runtime_data import ImouRuntimeData
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.number import NumberDeviceClass, NumberMode
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pyimouapi.const import PARAM_STATE
from pyimouapi.ha_device import DeviceStatus, ImouHaDevice
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from . import USER_INPUT


def _device() -> MagicMock:
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
        PARAM_COUNT_DOWN_SWITCH: {PARAM_STATE: "0"},
        PARAM_OVERCHARGE_SWITCH: {PARAM_STATE: "100"},
    }
    return device


def _coordinator(device: MagicMock) -> MagicMock:
    coordinator = MagicMock()
    key = imou_life_device_key(device)
    coordinator.devices = [device]
    coordinator.devices_by_key = {key: device}
    coordinator.last_update_success = True
    coordinator.get_device = MagicMock(return_value=device)
    coordinator.device_manager.async_set_text_value = AsyncMock()
    coordinator.async_update_listeners = MagicMock()
    return coordinator


def _number(hass: HomeAssistant, device: MagicMock) -> ImouCountdownNumber:
    coordinator = _coordinator(device)
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    entry.runtime_data = ImouRuntimeData(coordinator=coordinator)
    number = ImouCountdownNumber(coordinator, entry, PARAM_COUNT_DOWN_SWITCH, device)
    number.hass = hass
    number.async_write_ha_state = MagicMock()
    return number


@pytest.fixture
def plug_number(hass: HomeAssistant):
    """Build a countdown number and cancel its local clock after the test."""
    device = _device()
    number = _number(hass, device)
    try:
        yield number, device
    finally:
        number._tracker.async_unload()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_setting_minutes_writes_the_device_and_starts_remaining(
    plug_number: tuple[ImouCountdownNumber, MagicMock],
) -> None:
    """The number is the delay you set; remaining starts at that value."""
    number, device = plug_number

    await number.async_set_native_value(10)

    number.coordinator.device_manager.async_set_text_value.assert_awaited_once_with(
        device, PARAM_COUNT_DOWN_SWITCH, "10"
    )
    assert number.native_value == 10


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_remaining_drops_without_a_cloud_poll(
    plug_number: tuple[ImouCountdownNumber, MagicMock],
    freezer: FrozenDateTimeFactory,
) -> None:
    """The device page must count down even when status polling is off."""
    number, _device = plug_number
    await number.async_set_native_value(10)

    freezer.tick(timedelta(minutes=3))

    assert number.native_value == 7


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_countdown_expires_to_zero_locally(
    plug_number: tuple[ImouCountdownNumber, MagicMock],
    freezer: FrozenDateTimeFactory,
    hass: HomeAssistant,
) -> None:
    """When the local clock runs out, remaining is 0 without waiting for a poll."""
    number, device = plug_number
    await number.async_set_native_value(2)

    freezer.tick(timedelta(minutes=2))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert number.native_value == 0
    assert device.texts[PARAM_COUNT_DOWN_SWITCH][PARAM_STATE] == "0"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_zero_cancels_the_countdown(
    plug_number: tuple[ImouCountdownNumber, MagicMock],
) -> None:
    """Setting 0 is how you cancel."""
    number, device = plug_number
    await number.async_set_native_value(10)
    await number.async_set_native_value(0)

    assert number.native_value == 0
    number.coordinator.device_manager.async_set_text_value.assert_awaited_with(
        device, PARAM_COUNT_DOWN_SWITCH, "0"
    )


def test_countdown_is_a_minute_duration_number() -> None:
    """Home Assistant can show a unit and a box instead of a text field."""
    number = object.__new__(ImouCountdownNumber)
    assert number.device_class is NumberDeviceClass.DURATION
    assert number.native_unit_of_measurement is UnitOfTime.MINUTES
    assert number.native_min_value == 0
    assert number.native_max_value == 1440
    assert number.native_step == 1
    assert number.mode is NumberMode.BOX


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_tracker_resyncs_remaining_from_the_device(hass: HomeAssistant) -> None:
    """A later poll that still has minutes left must not leave the local clock stale."""
    tracker = CountdownTracker()
    device = _device()
    device.texts[PARAM_COUNT_DOWN_SWITCH][PARAM_STATE] = "5"
    coordinator = _coordinator(device)

    try:
        tracker.sync_from_device(hass, coordinator, device)

        assert tracker.remaining_minutes(imou_life_device_key(device)) == 5
        end = tracker.ends_at(imou_life_device_key(device))
        assert end is not None
        assert (end - dt_util.utcnow()) <= timedelta(minutes=5)
    finally:
        tracker.async_unload()
