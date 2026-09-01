"""Webhook call / doorbell push as a camera event entity."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import MagicMock

import pytest
from custom_components.imou_life.const import (
    DEFAULT_EVENT_PUSH_TYPES,
    DOMAIN,
    EVENT_IMOU_ALARM,
    EVENT_PUSH_TYPE_IOT,
    PARAM_DOORBELL,
    PARAM_ENABLE_EVENT_PUSH,
    PARAM_EVENT_PUSH_TYPES,
    PARAM_STATUS,
)
from custom_components.imou_life.event import (
    ImouDoorbellEvent,
    _iter_doorbell_events,
    is_doorbell_call_msg_type,
)
from custom_components.imou_life.webhook import async_handle_imou_webhook
from homeassistant.components.event import EventDeviceClass
from homeassistant.core import HomeAssistant
from pyimouapi.const import PARAM_STATE
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


def _online_doorbell(
    device: MagicMock,
    *,
    push: bool,
    types: list[str] | None = None,
) -> ImouDoorbellEvent:
    """Doorbell entity for an online camera with the given push options."""
    coordinator = _coordinator([device])
    coordinator.last_update_success = True
    coordinator.devices_by_key = {f"{device.device_id}_{device.channel_id}": device}
    device.sensors = {PARAM_STATUS: {PARAM_STATE: DeviceStatus.ONLINE.value}}
    options: dict[str, object] = {PARAM_ENABLE_EVENT_PUSH: push}
    if types is not None:
        options[PARAM_EVENT_PUSH_TYPES] = types
    elif push:
        options[PARAM_EVENT_PUSH_TYPES] = list(DEFAULT_EVENT_PUSH_TYPES)
    return ImouDoorbellEvent(
        coordinator,
        MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options=options),
        PARAM_DOORBELL,
        device,
    )


@asynccontextmanager
async def _listening_doorbell(
    hass: HomeAssistant, entity: ImouDoorbellEvent
) -> AsyncIterator[ImouDoorbellEvent]:
    """Subscribe the entity, then drop its bus listener."""
    entity.hass = hass
    entity.entity_id = "event.front_doorbell"
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
        ("callBellEvent", True),
        ("callEvent", True),
        ("e_callQuickEvent", True),
        ("e_callSupplyEvent", True),
        ("e_callEventCall", True),
        ("callquickevent", True),
        ("309100", True),
        ("309200", True),
        ("311000", True),
        ("Doorbell", False),
        ("callNoAnswered", False),
        ("message", False),
        ("mobileDetect", False),
        ("human", False),
        ("smokeAlarm", False),
        (None, False),
    ],
)
def test_is_doorbell_call_msg_type(msg_type: str | None, expected: bool) -> None:
    """Only the agreed call / press types drive the doorbell event."""
    assert is_doorbell_call_msg_type(msg_type) is expected


def test_iter_doorbell_events_paas_needs_call_ability() -> None:
    """PaaS doorbell follows CallAbility, not every camera channel."""
    camera = _device(channel_id="0")
    camera.channel_ability = "WLAN,CallAbility"
    plug = _device(channel_id=None, device_id="PLUG")
    no_call = _device(channel_id="0", device_id="IPC2")
    no_call.channel_ability = "WLAN,MobileDetect"
    pairs = _iter_doorbell_events(_coordinator([camera, plug, no_call]))
    assert [(key, device.device_id) for key, device in pairs] == [
        (PARAM_DOORBELL, "SN1")
    ]


def test_iter_doorbell_events_ipc_uses_device_ability_on_channel_0() -> None:
    """An IPC often lists CallAbility on the device, not the channel."""
    camera = _device(channel_id="0")
    camera.device_ability = "WLAN,CallAbility"
    camera.channel_ability = "WLAN"
    pairs = _iter_doorbell_events(_coordinator([camera]))
    assert pairs == [(PARAM_DOORBELL, camera)]


def test_iter_doorbell_events_nvr_channel_ignores_device_ability() -> None:
    """An NVR channel only gets doorbell when that channel reports the ability."""
    camera = _device(channel_id="1")
    camera.is_ipc = False
    camera.device_ability = "CallAbility"
    camera.channel_ability = "WLAN"
    assert _iter_doorbell_events(_coordinator([camera])) == []


def test_iter_doorbell_events_iot_uses_product_model_events() -> None:
    """IoT doorbell follows thing-model call events, not PaaS abilities."""
    camera = _device(channel_id="0")
    camera.product_id = "pidA"
    camera.channel_ability = "WLAN"
    pairs = _iter_doorbell_events(
        _coordinator([camera], event_map={"311000": "e_callEventCall"})
    )
    assert pairs == [(PARAM_DOORBELL, camera)]


def test_iter_doorbell_events_iot_without_call_events_skipped() -> None:
    """An IoT camera with CallAbility but no call events must not get doorbell."""
    camera = _device(channel_id="0")
    camera.product_id = "pidA"
    camera.channel_ability = "WLAN,CallAbility"
    pairs = _iter_doorbell_events(
        _coordinator([camera], event_map={"33000": "e_videoMotion"})
    )
    assert pairs == []


def test_doorbell_entity_is_a_doorbell_class() -> None:
    """Device automations treat this as a doorbell ring."""
    device = _device(channel_id="0")
    entity = ImouDoorbellEvent(
        _coordinator([device]),
        MockConfigEntry(domain=DOMAIN, data=USER_INPUT),
        PARAM_DOORBELL,
        device,
    )
    assert entity.device_class is EventDeviceClass.DOORBELL
    assert "ring" in entity.event_types
    assert entity.state is None


def test_doorbell_unavailable_when_alarm_push_is_off() -> None:
    """A doorbell that cannot hear a press is unavailable, not idle."""
    device = _device(channel_id="0")
    assert _online_doorbell(device, push=False).available is False


def test_doorbell_unavailable_when_alarm_type_not_subscribed() -> None:
    """iotProperty push does not carry call / doorbell alarms."""
    device = _device(channel_id="0")
    entity = _online_doorbell(device, push=True, types=[EVENT_PUSH_TYPE_IOT])
    assert entity.available is False


def test_doorbell_available_when_alarm_push_is_on() -> None:
    """An online camera with alarm push can fire ring."""
    device = _device(channel_id="0")
    assert _online_doorbell(device, push=True).available is True


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_call_bell_event_rings(hass: HomeAssistant) -> None:
    """A callBellEvent for this camera fires ring and keeps the original type."""
    device = _device(channel_id="0")
    coordinator = _coordinator([device])
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    entity = ImouDoorbellEvent(coordinator, entry, PARAM_DOORBELL, device)
    async with _listening_doorbell(hass, entity):
        hass.bus.async_fire(
            EVENT_IMOU_ALARM,
            {"device_id": "SN1", "channel_id": "0", "msg_type": "callBellEvent"},
        )
        await hass.async_block_till_done()
        assert entity.state is not None
        assert entity.state_attributes["event_type"] == "ring"
        assert entity.state_attributes["msg_type"] == "callBellEvent"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_iot_call_supply_rings(hass: HomeAssistant) -> None:
    """The rewritten IoT identifier e_callSupplyEvent still rings."""
    device = _device(channel_id="0")
    coordinator = _coordinator([device])
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    entity = ImouDoorbellEvent(coordinator, entry, PARAM_DOORBELL, device)
    async with _listening_doorbell(hass, entity):
        hass.bus.async_fire(
            EVENT_IMOU_ALARM,
            {
                "device_id": "SN1",
                "channel_id": "0",
                "msg_type": "e_callSupplyEvent",
            },
        )
        await hass.async_block_till_done()
        assert entity.state is not None
        assert entity.state_attributes["msg_type"] == "e_callSupplyEvent"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_unresolved_iot_ref_rings(hass: HomeAssistant) -> None:
    """A numeric ref still rings when getProductModel has not rewritten it."""
    device = _device(channel_id="0")
    coordinator = _coordinator([device])
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    entity = ImouDoorbellEvent(coordinator, entry, PARAM_DOORBELL, device)
    async with _listening_doorbell(hass, entity):
        hass.bus.async_fire(
            EVENT_IMOU_ALARM,
            {"device_id": "SN1", "channel_id": "0", "msg_type": "309100"},
        )
        await hass.async_block_till_done()
        assert entity.state is not None
        assert entity.state_attributes["msg_type"] == "309100"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_other_camera_does_not_ring(hass: HomeAssistant) -> None:
    """A push for another channel must not fire this doorbell."""
    device = _device(channel_id="0")
    coordinator = _coordinator([device])
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    entity = ImouDoorbellEvent(coordinator, entry, PARAM_DOORBELL, device)
    async with _listening_doorbell(hass, entity):
        hass.bus.async_fire(
            EVENT_IMOU_ALARM,
            {"device_id": "SN1", "channel_id": "1", "msg_type": "callBellEvent"},
        )
        await hass.async_block_till_done()
        assert entity.state is None


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_unanswered_and_legacy_doorbell_do_not_ring(
    hass: HomeAssistant,
) -> None:
    """Unanswered calls and the PaaS Doorbell opening type stay off this entity."""
    device = _device(channel_id="0")
    coordinator = _coordinator([device])
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    entity = ImouDoorbellEvent(coordinator, entry, PARAM_DOORBELL, device)
    async with _listening_doorbell(hass, entity):
        for msg_type in ("callNoAnswered", "Doorbell", "message"):
            hass.bus.async_fire(
                EVENT_IMOU_ALARM,
                {"device_id": "SN1", "channel_id": "0", "msg_type": msg_type},
            )
        await hass.async_block_till_done()
        assert entity.state is None


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_call_event_rings(hass: HomeAssistant) -> None:
    """The live webhook path rings the matching camera doorbell event."""
    setup_imou_runtime(hass, selected_devices=["SN1"])
    device = _device(channel_id="0")
    coordinator = _coordinator([device])
    entry = next(iter(hass.config_entries.async_entries(DOMAIN)))
    entity = ImouDoorbellEvent(coordinator, entry, PARAM_DOORBELL, device)
    async with _listening_doorbell(hass, entity):
        await async_handle_imou_webhook(
            hass,
            "webhook-id",
            MockRequest({"msgType": "callEvent", "deviceId": "SN1", "channelId": "0"}),
        )
        await hass.async_block_till_done(wait_background_tasks=True)
        assert entity.state is not None
        assert entity.state_attributes["msg_type"] == "callEvent"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_removed_listener_ignores_later_calls(hass: HomeAssistant) -> None:
    """Unload must cancel the bus listener so a later call cannot write state."""
    device = _device(channel_id="0")
    coordinator = _coordinator([device])
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    entity = ImouDoorbellEvent(coordinator, entry, PARAM_DOORBELL, device)
    async with _listening_doorbell(hass, entity):
        pass
    hass.bus.async_fire(
        EVENT_IMOU_ALARM,
        {"device_id": "SN1", "channel_id": "0", "msg_type": "callBellEvent"},
    )
    await hass.async_block_till_done()
    assert entity.state is None
