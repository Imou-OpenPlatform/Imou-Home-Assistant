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
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pyimouapi.const import BINARY_SENSOR_TYPE_REF
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
    device.is_ipc = True
    device.device_ability = "WLAN"
    device.channel_ability = "WLAN"
    device.device_name = "Front"
    device.channel_name = "Front"
    device.manufacturer = "Imou"
    device.model = "IPC"
    device.swversion = "1.0"
    device.sensors = {}
    return device


def _coordinator(
    devices: list[MagicMock],
    *,
    event_map: dict[str, str] | None = None,
) -> MagicMock:
    coordinator = MagicMock()
    coordinator.devices = devices
    coordinator.devices_by_key = {}
    coordinator.last_update_success = True
    coordinator.device_manager.delegate.cached_event_map = MagicMock(
        return_value=event_map or {}
    )
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
        ("motionDetect", True),
        ("alarmPIR", True),
        ("e_alarmPIR", True),
        ("pir_alarm", True),
        ("e_aiPerArea", True),
        ("aiPerArea", True),
        ("e_multiVideoAiPerArea", True),
        ("e_multiVideoAiPerAreaAlarm", True),
        ("e_smartMixDetect", True),
        ("crossLineDetection", True),
        ("e_crossLineDetection", True),
        ("e_areaDetect", True),
        ("e_areaDetectAlarm", True),
        ("e_multiVideoAreaDetect", True),
        ("e_multiVideoDetectAlarm", True),
        ("e_clearAlarmPIR", False),
        ("pir_cleared", False),
        ("smokeAlarm", None),
        ("gasAlarm", None),
        ("abAlarmSound", None),
        ("e_pet", None),
        ("e_aiVehArea", None),
        (None, None),
    ],
)
def test_motion_binary_state(msg_type: str | None, expected: bool | None) -> None:
    """Picture / human / PIR / person-in-area / line-crossing drive motion."""
    assert motion_binary_state(msg_type) is expected


def test_iter_motion_sensors_paas_needs_detect_ability() -> None:
    """PaaS motion follows picture-change / human abilities, not every camera."""
    camera = _device(channel_id="0")
    camera.channel_ability = "WLAN,MobileDetect"
    plug = _device(channel_id=None, device_id="PLUG")
    no_detect = _device(channel_id="0", device_id="IPC2")
    no_detect.channel_ability = "WLAN,CallAbility"
    pairs = _iter_motion_sensors(_coordinator([camera, plug, no_detect]))
    assert [(key, device.device_id) for key, device in pairs] == [(PARAM_MOTION, "SN1")]


def test_iter_motion_sensors_ipc_uses_device_ability_on_channel_0() -> None:
    """An IPC often lists picture-change on the device, not the channel."""
    camera = _device(channel_id="0")
    camera.device_ability = "WLAN,MobileDetect"
    camera.channel_ability = "WLAN"
    assert _iter_motion_sensors(_coordinator([camera])) == [(PARAM_MOTION, camera)]


def test_iter_motion_sensors_nvr_channel_ignores_device_ability() -> None:
    """An NVR channel only gets motion when that channel reports the ability."""
    camera = _device(channel_id="1")
    camera.is_ipc = False
    camera.device_ability = "MobileDetect"
    camera.channel_ability = "WLAN"
    assert _iter_motion_sensors(_coordinator([camera])) == []


@pytest.mark.parametrize(
    "ability",
    ["MobileDetect", "AlarmMD", "CRMD", "HeaderDetect", "AiHuman", "SMDH"],
)
def test_iter_motion_sensors_paas_accepts_detect_abilities(ability: str) -> None:
    """Picture-change and human-detect abilities each create the motion sensor."""
    camera = _device(channel_id="0")
    camera.channel_ability = f"WLAN,{ability}"
    assert _iter_motion_sensors(_coordinator([camera])) == [(PARAM_MOTION, camera)]


def test_iter_motion_sensors_iot_uses_product_model_events() -> None:
    """IoT motion follows thing-model motion events, not detect-switch refs."""
    camera = _device(channel_id="0")
    camera.product_id = "pidA"
    camera.channel_ability = "WLAN"
    pairs = _iter_motion_sensors(
        _coordinator([camera], event_map={"33000": "e_videoMotion"})
    )
    assert pairs == [(PARAM_MOTION, camera)]


def test_iter_motion_sensors_iot_switch_refs_are_not_events() -> None:
    """Property refs for the detect switch must not create a motion sensor."""
    camera = _device(channel_id="0")
    camera.product_id = "pidA"
    pairs = _iter_motion_sensors(
        _coordinator(
            [camera],
            event_map={"14800": "mdEnable", "108800": "mdEnable2"},
        )
    )
    assert pairs == []


def test_iter_motion_sensors_iot_without_motion_events_skipped() -> None:
    """An IoT camera with only call events must not get a motion sensor."""
    camera = _device(channel_id="0")
    camera.product_id = "pidA"
    camera.channel_ability = "WLAN,MobileDetect"
    pairs = _iter_motion_sensors(
        _coordinator([camera], event_map={"311000": "e_callEventCall"})
    )
    assert pairs == []


def test_motion_unique_id_does_not_collide_with_library_binary_sensors() -> None:
    """HA-only motion must not share a type key with a polled library sensor."""
    assert PARAM_MOTION not in BINARY_SENSOR_TYPE_REF


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
async def test_repeat_motion_resets_auto_off(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A second motion push must not flash off; it restarts the 15s hold."""
    device = _device(channel_id="0")
    coordinator = _coordinator([device])
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    entity = ImouMotionBinarySensor(coordinator, entry, PARAM_MOTION, device)
    async with _listening_motion(hass, entity):
        payload = {"device_id": "SN1", "channel_id": "0", "msg_type": "human"}
        hass.bus.async_fire(EVENT_IMOU_ALARM, payload)
        await hass.async_block_till_done()
        freezer.tick(timedelta(seconds=7))
        hass.bus.async_fire(EVENT_IMOU_ALARM, payload)
        await hass.async_block_till_done()
        freezer.tick(timedelta(seconds=9))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert entity.is_on is True
        freezer.tick(timedelta(seconds=7))
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
async def test_iot_person_area_turns_motion_on(hass: HomeAssistant) -> None:
    """IoT PTZ person-in-area identifiers drive the same motion entity."""
    device = _device(channel_id="0")
    coordinator = _coordinator([device])
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    entity = ImouMotionBinarySensor(coordinator, entry, PARAM_MOTION, device)
    async with _listening_motion(hass, entity):
        hass.bus.async_fire(
            EVENT_IMOU_ALARM,
            {
                "device_id": "SN1",
                "channel_id": "0",
                "msg_type": "e_multiVideoAiPerArea",
            },
        )
        await hass.async_block_till_done()
        assert entity.is_on is True


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
