"""Webhook motion / human / PIR as a camera event."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import pytest
from custom_components.imou_life.const import (
    DOMAIN,
    EVENT_IMOU_ALARM,
    EVENT_PUSH_TYPE_IOT,
    EVENT_PUSH_TYPES_ALL,
    PARAM_ENABLE_EVENT_PUSH,
    PARAM_EVENT_PUSH_TYPES,
    PARAM_MOTION,
    PARAM_STATUS,
)
from custom_components.imou_life.event import (
    ImouMotionEvent,
    _iter_motion_events,
    motion_push,
)
from custom_components.imou_life.webhook import async_handle_imou_webhook
from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.event import EventDeviceClass
from homeassistant.core import HomeAssistant
from pyimouapi.const import BINARY_SENSOR_TYPE_REF, PARAM_STATE
from pyimouapi.ha_device import DeviceStatus, ImouHaDevice
from pytest_homeassistant_custom_component.common import MockConfigEntry

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
    device.capability_set = None
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


def _online_motion(
    device: MagicMock,
    *,
    push: bool,
    types: list[str] | None = None,
) -> ImouMotionEvent:
    """Motion entity for an online camera with the given push options."""
    coordinator = _coordinator([device])
    coordinator.last_update_success = True
    coordinator.devices_by_key = {f"{device.device_id}_{device.channel_id}": device}
    device.sensors = {PARAM_STATUS: {PARAM_STATE: DeviceStatus.ONLINE.value}}
    options: dict[str, object] = {PARAM_ENABLE_EVENT_PUSH: push}
    if types is not None:
        options[PARAM_EVENT_PUSH_TYPES] = types
    elif push:
        options[PARAM_EVENT_PUSH_TYPES] = list(EVENT_PUSH_TYPES_ALL)
    return ImouMotionEvent(
        coordinator,
        MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options=options),
        PARAM_MOTION,
        device,
    )


@asynccontextmanager
async def _listening_motion(
    hass: HomeAssistant, entity: ImouMotionEvent
) -> AsyncIterator[ImouMotionEvent]:
    """Subscribe the entity, then drop its bus listener and auto-off timer."""
    entity.hass = hass
    entity.entity_id = "event.front_motion"
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
        ("smokeAlarm", False),
        ("gasAlarm", False),
        ("abAlarmSound", False),
        ("e_pet", False),
        ("e_aiVehArea", False),
        (None, False),
    ],
)
def test_motion_push(msg_type: str | None, expected: bool) -> None:
    """Picture / human / PIR / person-in-area / line-crossing fire motion."""
    assert motion_push(msg_type) is expected


def _capability(*property_ids: str) -> MagicMock:
    capability = MagicMock()
    capability.properties = {property_id: object() for property_id in property_ids}
    capability.services = frozenset()
    capability.events = frozenset()
    return capability


def test_iter_motion_events_follows_v2_detect_properties() -> None:
    """Motion follows picture-change / person detect, not every camera."""
    camera = _device(channel_id="0")
    camera.capability_set = _capability("detection.motion.enabled")
    plug = _device(channel_id=None, device_id="PLUG")
    plug.capability_set = _capability("detection.motion.enabled")
    no_detect = _device(channel_id="0", device_id="IPC2")
    no_detect.capability_set = _capability("call.session.ringVolume")
    pairs = _iter_motion_events(_coordinator([camera, plug, no_detect]))
    assert [(key, device.device_id) for key, device in pairs] == [(PARAM_MOTION, "SN1")]


def test_iter_motion_events_ignores_legacy_ability_strings() -> None:
    """Old ability names do not create Motion once the capability list is known."""
    camera = _device(channel_id="0")
    camera.device_ability = "WLAN,MobileDetect"
    camera.channel_ability = "WLAN,HeaderDetect"
    assert _iter_motion_events(_coordinator([camera])) == []


def test_iter_motion_events_channel_without_detect_is_skipped() -> None:
    """A channel whose capability list has no detect property is skipped."""
    camera = _device(channel_id="1")
    camera.is_ipc = False
    camera.capability_set = _capability("detection.sound.enabled")
    assert _iter_motion_events(_coordinator([camera])) == []


@pytest.mark.parametrize(
    "property_id",
    [
        "detection.motion.enabled",
        "detection.intrusion.enabled",
        "detection.person.enabled",
        "detection.intrusion.personFilter",
    ],
)
def test_iter_motion_events_accepts_v2_detect_properties(property_id: str) -> None:
    """Picture-change and person detect, including fallbacks, create Motion."""
    camera = _device(channel_id="0")
    camera.capability_set = _capability(property_id)
    assert _iter_motion_events(_coordinator([camera])) == [(PARAM_MOTION, camera)]


@pytest.mark.parametrize(
    "property_id",
    ["detection.sound.enabled", "detection.pet.enabled", "detection.motion.plan"],
)
def test_iter_motion_events_ignores_unrelated_v2_properties(property_id: str) -> None:
    """Sound, pet, and the motion schedule are not the Motion sensor."""
    camera = _device(channel_id="0")
    camera.capability_set = _capability(property_id)
    assert _iter_motion_events(_coordinator([camera])) == []


def test_iter_motion_events_iot_uses_the_same_v2_properties() -> None:
    """A product id does not change the capability check."""
    camera = _device(channel_id="0")
    camera.product_id = "pidA"
    camera.capability_set = _capability("detection.person.enabled")
    pairs = _iter_motion_events(
        _coordinator([camera], event_map={"311000": "e_callEventCall"})
    )
    assert pairs == [(PARAM_MOTION, camera)]


def test_iter_motion_events_iot_event_refs_are_not_enough() -> None:
    """Old product-model refs do not create Motion without the v2 property."""
    camera = _device(channel_id="0")
    camera.product_id = "pidA"
    camera.channel_ability = "WLAN,MobileDetect"
    pairs = _iter_motion_events(
        _coordinator([camera], event_map={"33000": "e_videoMotion"})
    )
    assert pairs == []


def test_motion_unique_id_does_not_collide_with_library_binary_sensors() -> None:
    """HA-only motion must not share a type key with a polled library sensor."""
    assert PARAM_MOTION not in BINARY_SENSOR_TYPE_REF


def test_motion_entity_is_a_motion_event() -> None:
    """Dashboards and automations treat this as a motion event."""
    device = _device(channel_id="0")
    entity = ImouMotionEvent(
        _coordinator([device]),
        MockConfigEntry(domain=DOMAIN, data=USER_INPUT),
        PARAM_MOTION,
        device,
    )
    assert entity.device_class is EventDeviceClass.MOTION
    assert entity.event_types == ["motion"]
    assert entity.state is None


def test_motion_unavailable_when_alarm_push_is_off() -> None:
    """Off forever would lie; unavailable means we are not listening."""
    device = _device(channel_id="0")
    assert _online_motion(device, push=False).available is False


def test_motion_unavailable_when_alarm_type_not_subscribed() -> None:
    """iotProperty push does not carry motion / doorbell alarms."""
    device = _device(channel_id="0")
    entity = _online_motion(device, push=True, types=[EVENT_PUSH_TYPE_IOT])
    assert entity.available is False


def test_motion_available_when_alarm_push_is_on() -> None:
    """An online camera with alarm push can report motion."""
    device = _device(channel_id="0")
    assert _online_motion(device, push=True).available is True


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_alarm_event_fires_motion(hass: HomeAssistant) -> None:
    """A human-detect alarm for this camera fires the motion event."""
    device = _device(channel_id="0")
    coordinator = _coordinator([device])
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    entity = ImouMotionEvent(coordinator, entry, PARAM_MOTION, device)
    async with _listening_motion(hass, entity):
        hass.bus.async_fire(
            EVENT_IMOU_ALARM,
            {"device_id": "SN1", "channel_id": "0", "msg_type": "human"},
        )
        await hass.async_block_till_done()
        assert entity.state_attributes["event_type"] == "motion"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_other_camera_does_not_fire_motion(hass: HomeAssistant) -> None:
    """A push for another channel must not fire this event."""
    device = _device(channel_id="0")
    coordinator = _coordinator([device])
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    entity = ImouMotionEvent(coordinator, entry, PARAM_MOTION, device)
    async with _listening_motion(hass, entity):
        hass.bus.async_fire(
            EVENT_IMOU_ALARM,
            {"device_id": "SN1", "channel_id": "1", "msg_type": "human"},
        )
        await hass.async_block_till_done()
        assert entity.state is None


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_repeat_motion_fires_again(hass: HomeAssistant) -> None:
    """Each detection fires again. The event does not stay on."""
    device = _device(channel_id="0")
    coordinator = _coordinator([device])
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    entity = ImouMotionEvent(coordinator, entry, PARAM_MOTION, device)
    async with _listening_motion(hass, entity):
        payload = {"device_id": "SN1", "channel_id": "0", "msg_type": "human"}
        hass.bus.async_fire(EVENT_IMOU_ALARM, payload)
        await hass.async_block_till_done()
        first = entity.state
        hass.bus.async_fire(EVENT_IMOU_ALARM, payload)
        await hass.async_block_till_done()
        assert entity.state != first
        assert entity.state_attributes["event_type"] == "motion"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_pir_clear_does_not_fire_motion(hass: HomeAssistant) -> None:
    """A clear is not a detection, so it does not fire motion."""
    device = _device(channel_id="0")
    coordinator = _coordinator([device])
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    entity = ImouMotionEvent(coordinator, entry, PARAM_MOTION, device)
    async with _listening_motion(hass, entity):
        hass.bus.async_fire(
            EVENT_IMOU_ALARM,
            {"device_id": "SN1", "channel_id": "0", "msg_type": "alarmPIR"},
        )
        await hass.async_block_till_done()
        fired = entity.state
        hass.bus.async_fire(
            EVENT_IMOU_ALARM,
            {"device_id": "SN1", "channel_id": "0", "msg_type": "clearAlarmPIR"},
        )
        await hass.async_block_till_done()
        assert entity.state == fired


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_smoke_alarm_does_not_touch_motion(hass: HomeAssistant) -> None:
    """Fire / gas / other alarms stay on notify + imou_life_alarm, not this entity."""
    device = _device(channel_id="0")
    coordinator = _coordinator([device])
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    entity = ImouMotionEvent(coordinator, entry, PARAM_MOTION, device)
    async with _listening_motion(hass, entity):
        hass.bus.async_fire(
            EVENT_IMOU_ALARM,
            {"device_id": "SN1", "channel_id": "0", "msg_type": "smokeAlarm"},
        )
        await hass.async_block_till_done()
        assert entity.state is None


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_iot_person_area_turns_motion_on(hass: HomeAssistant) -> None:
    """IoT PTZ person-in-area identifiers drive the same motion entity."""
    device = _device(channel_id="0")
    coordinator = _coordinator([device])
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    entity = ImouMotionEvent(coordinator, entry, PARAM_MOTION, device)
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
        assert entity.state_attributes["event_type"] == "motion"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_human_turns_motion_on(hass: HomeAssistant) -> None:
    """The live webhook path turns the matching camera motion sensor on."""
    setup_imou_runtime(hass, selected_devices=["SN1"])
    device = _device(channel_id="0")
    coordinator = _coordinator([device])
    entry = next(iter(hass.config_entries.async_entries(DOMAIN)))
    entity = ImouMotionEvent(coordinator, entry, PARAM_MOTION, device)
    async with _listening_motion(hass, entity):
        await async_handle_imou_webhook(
            hass,
            "webhook-id",
            MockRequest({"msgType": "human", "deviceId": "SN1", "channelId": "0"}),
        )
        await hass.async_block_till_done(wait_background_tasks=True)
        assert entity.state_attributes["event_type"] == "motion"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_removed_listener_ignores_later_alarms(hass: HomeAssistant) -> None:
    """Unload must cancel the bus listener so a later alarm cannot write state."""
    device = _device(channel_id="0")
    coordinator = _coordinator([device])
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    entity = ImouMotionEvent(coordinator, entry, PARAM_MOTION, device)
    async with _listening_motion(hass, entity):
        pass
    hass.bus.async_fire(
        EVENT_IMOU_ALARM,
        {"device_id": "SN1", "channel_id": "0", "msg_type": "human"},
    )
    await hass.async_block_till_done()
    assert entity.state is None
