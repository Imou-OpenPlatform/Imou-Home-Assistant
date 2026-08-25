"""Webhook motion / human / PIR as a camera binary_sensor."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from custom_components.imou_life.binary_sensor import (
    ImouMotionBinarySensor,
    _iter_motion_sensors,
    motion_binary_state,
)
from custom_components.imou_life.const import (
    DOMAIN,
    EVENT_IMOU_ALARM,
    MOTION_OFF_DELAY,
    PARAM_MOTION,
)
from custom_components.imou_life.webhook import async_handle_imou_webhook
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pyimouapi.ha_device import ImouHaDevice
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from . import USER_INPUT
from .conftest import setup_imou_runtime
from .test_webhook import MockRequest


def _device(*, channel_id: object | None, device_id: str = "SN1") -> MagicMock:
    device = MagicMock(spec=ImouHaDevice)
    device.device_id = device_id
    device.channel_id = channel_id
    device.product_id = None
    device.device_name = "Front"
    device.channel_name = "Front"
    device.manufacturer = "Imou"
    device.model = "IPC"
    device.swversion = "1.0"
    device.sensors = {}
    return device


def _coordinator(devices: list[MagicMock]) -> MagicMock:
    coordinator = MagicMock()
    coordinator.devices = devices
    coordinator.devices_by_key = {}
    coordinator.last_update_success = True
    return coordinator


@asynccontextmanager
async def _listening_motion(
    hass: HomeAssistant, entity: ImouMotionBinarySensor
) -> AsyncIterator[ImouMotionBinarySensor]:
    """Subscribe the entity, then drop its bus listener and auto-off timer."""
    entity.hass = hass
    entity.entity_id = "binary_sensor.front_motion"
    await entity.async_added_to_hass()
    try:
        yield entity
    finally:
        if entity._on_remove is not None:
            while entity._on_remove:
                entity._on_remove.pop()()


@pytest.mark.parametrize(
    ("msg_type", "expected"),
    [
        ("videoMotion", True),
        ("e_videoMotion", True),
        ("human", True),
        ("mobileDetect", True),
        ("alarmPIR", True),
        ("e_alarmPIR", True),
        ("pir_alarm", True),
        ("e_clearAlarmPIR", False),
        ("pir_cleared", False),
        ("smokeAlarm", None),
        ("gasAlarm", None),
        ("abAlarmSound", None),
        ("e_pet", None),
        ("e_multiVideoAiPerArea", None),
        ("e_multiVideoAiPerAreaAlarm", None),
        (None, None),
    ],
)
def test_motion_binary_state(msg_type: str | None, expected: bool | None) -> None:
    """Only picture / human / PIR pushes drive the motion sensor."""
    assert motion_binary_state(msg_type) is expected


def test_iter_motion_sensors_cameras_only() -> None:
    """Motion is a camera channel entity, not a plug or bare hub."""
    camera = _device(channel_id="0")
    plug = _device(channel_id=None, device_id="PLUG")
    pairs = _iter_motion_sensors(_coordinator([camera, plug]))
    assert [(key, device) for key, device in pairs] == [(PARAM_MOTION, camera)]


def test_motion_entity_is_a_motion_class() -> None:
    """Dashboards and HomeKit treat this as a motion sensor."""
    device = _device(channel_id="0")
    entity = ImouMotionBinarySensor(
        _coordinator([device]),
        MockConfigEntry(domain=DOMAIN, data=USER_INPUT),
        PARAM_MOTION,
        device,
    )
    assert entity.device_class is BinarySensorDeviceClass.MOTION
    assert entity.is_on is False


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_alarm_event_turns_motion_on(hass: HomeAssistant) -> None:
    """A human-detect alarm for this camera turns the sensor on."""
    device = _device(channel_id="0")
    coordinator = _coordinator([device])
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    entity = ImouMotionBinarySensor(coordinator, entry, PARAM_MOTION, device)
    async with _listening_motion(hass, entity):
        hass.bus.async_fire(
            EVENT_IMOU_ALARM,
            {"device_id": "SN1", "channel_id": "0", "msg_type": "human"},
        )
        await hass.async_block_till_done()
        assert entity.is_on is True


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_other_camera_does_not_turn_motion_on(hass: HomeAssistant) -> None:
    """A push for another channel must not flip this sensor."""
    device = _device(channel_id="0")
    coordinator = _coordinator([device])
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    entity = ImouMotionBinarySensor(coordinator, entry, PARAM_MOTION, device)
    async with _listening_motion(hass, entity):
        hass.bus.async_fire(
            EVENT_IMOU_ALARM,
            {"device_id": "SN1", "channel_id": "1", "msg_type": "human"},
        )
        await hass.async_block_till_done()
        assert entity.is_on is False


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_motion_auto_off(hass: HomeAssistant) -> None:
    """Motion returns to off after the hold time when the cloud sends no clear."""
    device = _device(channel_id="0")
    coordinator = _coordinator([device])
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    entity = ImouMotionBinarySensor(coordinator, entry, PARAM_MOTION, device)
    async with _listening_motion(hass, entity):
        hass.bus.async_fire(
            EVENT_IMOU_ALARM,
            {"device_id": "SN1", "channel_id": "0", "msg_type": "videoMotion"},
        )
        await hass.async_block_till_done()
        assert entity.is_on is True
        async_fire_time_changed(
            hass, dt_util.utcnow() + timedelta(seconds=MOTION_OFF_DELAY)
        )
        await hass.async_block_till_done()
        assert entity.is_on is False


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_repeat_motion_resets_auto_off(hass: HomeAssistant, freezer) -> None:
    """A second motion push must not flash off; it restarts the 30s hold."""
    device = _device(channel_id="0")
    coordinator = _coordinator([device])
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    entity = ImouMotionBinarySensor(coordinator, entry, PARAM_MOTION, device)
    async with _listening_motion(hass, entity):
        payload = {"device_id": "SN1", "channel_id": "0", "msg_type": "human"}
        hass.bus.async_fire(EVENT_IMOU_ALARM, payload)
        await hass.async_block_till_done()
        freezer.tick(timedelta(seconds=15))
        hass.bus.async_fire(EVENT_IMOU_ALARM, payload)
        await hass.async_block_till_done()
        freezer.tick(timedelta(seconds=16))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert entity.is_on is True
        freezer.tick(timedelta(seconds=15))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert entity.is_on is False


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_pir_clear_turns_motion_off_immediately(hass: HomeAssistant) -> None:
    """A PIR-clear push must not wait for the auto-off timer."""
    device = _device(channel_id="0")
    coordinator = _coordinator([device])
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    entity = ImouMotionBinarySensor(coordinator, entry, PARAM_MOTION, device)
    async with _listening_motion(hass, entity):
        hass.bus.async_fire(
            EVENT_IMOU_ALARM,
            {"device_id": "SN1", "channel_id": "0", "msg_type": "alarmPIR"},
        )
        await hass.async_block_till_done()
        assert entity.is_on is True
        hass.bus.async_fire(
            EVENT_IMOU_ALARM,
            {"device_id": "SN1", "channel_id": "0", "msg_type": "clearAlarmPIR"},
        )
        await hass.async_block_till_done()
        assert entity.is_on is False


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_smoke_alarm_does_not_touch_motion(hass: HomeAssistant) -> None:
    """Fire / gas / other alarms stay on notify + imou_life_alarm, not this entity."""
    device = _device(channel_id="0")
    coordinator = _coordinator([device])
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    entity = ImouMotionBinarySensor(coordinator, entry, PARAM_MOTION, device)
    async with _listening_motion(hass, entity):
        hass.bus.async_fire(
            EVENT_IMOU_ALARM,
            {"device_id": "SN1", "channel_id": "0", "msg_type": "smokeAlarm"},
        )
        await hass.async_block_till_done()
        assert entity.is_on is False


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_human_turns_motion_on(hass: HomeAssistant) -> None:
    """The live webhook path turns the matching camera motion sensor on."""
    setup_imou_runtime(hass, selected_devices=["SN1"])
    device = _device(channel_id="0")
    coordinator = _coordinator([device])
    entry = next(iter(hass.config_entries.async_entries(DOMAIN)))
    entity = ImouMotionBinarySensor(coordinator, entry, PARAM_MOTION, device)
    async with _listening_motion(hass, entity):
        await async_handle_imou_webhook(
            hass,
            "webhook-id",
            MockRequest({"msgType": "human", "deviceId": "SN1", "channelId": "0"}),
        )
        await hass.async_block_till_done(wait_background_tasks=True)
        assert entity.is_on is True


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_removed_listener_ignores_later_alarms(hass: HomeAssistant) -> None:
    """Unload must cancel the bus listener so a later alarm cannot write state."""
    device = _device(channel_id="0")
    coordinator = _coordinator([device])
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    entity = ImouMotionBinarySensor(coordinator, entry, PARAM_MOTION, device)
    async with _listening_motion(hass, entity):
        pass
    hass.bus.async_fire(
        EVENT_IMOU_ALARM,
        {"device_id": "SN1", "channel_id": "0", "msg_type": "human"},
    )
    await hass.async_block_till_done()
    assert entity.is_on is False
