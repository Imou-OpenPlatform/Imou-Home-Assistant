"""Manual siren as a standard HA siren entity."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from custom_components.imou_life.button import _iter_buttons
from custom_components.imou_life.const import (
    DOMAIN,
    EVENT_IMOU_ALARM,
    PARAM_SIREN,
    SIREN_OFF_DELAY,
)
from custom_components.imou_life.siren import (
    ImouSiren,
    _iter_sirens,
    siren_push_state,
)
from custom_components.imou_life.webhook import async_handle_imou_webhook
from homeassistant.components.siren import SirenEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pyimouapi.const import PARAM_SIREN_START, PARAM_SIREN_STOP
from pyimouapi.ha_device import ImouHaDevice
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from . import USER_INPUT
from .conftest import setup_imou_runtime
from .test_webhook import MockRequest


def _device(
    *,
    channel_id: object | None,
    device_id: str = "SN1",
    buttons: dict | None = None,
) -> MagicMock:
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
    device.buttons = buttons or {PARAM_SIREN_START: {}, PARAM_SIREN_STOP: {}}
    return device


def _coordinator(devices: list[MagicMock]) -> MagicMock:
    coordinator = MagicMock()
    coordinator.devices = devices
    coordinator.devices_by_key = {}
    coordinator.last_update_success = True
    coordinator.device_manager.async_press_button = AsyncMock()
    return coordinator


@asynccontextmanager
async def _listening_siren(
    hass: HomeAssistant, entity: ImouSiren
) -> AsyncIterator[ImouSiren]:
    """Subscribe the entity, then drop its bus listener and auto-off timer."""
    entity.hass = hass
    entity.entity_id = "siren.front_siren"
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
        ("sirenOn", True),
        ("e_sirenOn", True),
        ("sirenOff", False),
        ("siren_alarm_cleared", False),
        ("human", None),
        ("siren_warning", None),
        (None, None),
    ],
)
def test_siren_push_state(msg_type: str | None, expected: bool | None) -> None:
    """Only explicit siren on/off pushes drive the siren entity."""
    assert siren_push_state(msg_type) is expected


def test_iter_sirens_requires_siren_buttons() -> None:
    """Siren replaces the start/stop button pair on capable devices."""
    siren_device = _device(channel_id="0")
    plain = _device(channel_id="0", device_id="SN2", buttons={"mute": {}})
    pairs = _iter_sirens(_coordinator([siren_device, plain]))
    assert [(key, device) for key, device in pairs] == [(PARAM_SIREN, siren_device)]


def test_iter_buttons_skips_siren_controls() -> None:
    """Start/stop buttons must not duplicate the siren entity."""
    device = _device(channel_id="0", buttons={PARAM_SIREN_START: {}, "mute": {}})
    pairs = _iter_buttons(_coordinator([device]))
    assert [button_type for button_type, _device in pairs] == ["mute"]


def test_siren_entity_supports_turn_on_off() -> None:
    """Dashboards and voice assistants expect standard siren services."""
    device = _device(channel_id="0")
    entity = ImouSiren(
        _coordinator([device]),
        MockConfigEntry(domain=DOMAIN, data=USER_INPUT),
        PARAM_SIREN,
        device,
    )
    assert entity.supported_features == (
        SirenEntityFeature.TURN_ON | SirenEntityFeature.TURN_OFF
    )
    assert entity.is_on is False


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_turn_on_calls_siren_start(hass: HomeAssistant) -> None:
    """siren.turn_on maps to the existing cloud siren start API."""
    device = _device(channel_id="0")
    coordinator = _coordinator([device])
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    entity = ImouSiren(coordinator, entry, PARAM_SIREN, device)
    async with _listening_siren(hass, entity):
        await entity.async_turn_on()

        coordinator.device_manager.async_press_button.assert_awaited_once_with(
            device, PARAM_SIREN_START, 0
        )
        assert entity.is_on is True


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_turn_off_calls_siren_stop(hass: HomeAssistant) -> None:
    """siren.turn_off maps to the existing cloud siren stop API."""
    device = _device(channel_id="0")
    coordinator = _coordinator([device])
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    entity = ImouSiren(coordinator, entry, PARAM_SIREN, device)
    entity.hass = hass
    entity._attr_is_on = True
    await entity.async_turn_off()

    coordinator.device_manager.async_press_button.assert_awaited_once_with(
        device, PARAM_SIREN_STOP, 0
    )
    assert entity.is_on is False


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_siren_auto_off(hass: HomeAssistant) -> None:
    """Assumed on state clears after the hold time when the cloud sends no off."""
    device = _device(channel_id="0")
    coordinator = _coordinator([device])
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    entity = ImouSiren(coordinator, entry, PARAM_SIREN, device)
    async with _listening_siren(hass, entity):
        hass.bus.async_fire(
            EVENT_IMOU_ALARM,
            {"device_id": "SN1", "channel_id": "0", "msg_type": "sirenOn"},
        )
        await hass.async_block_till_done()
        assert entity.is_on is True

        async_fire_time_changed(
            hass, dt_util.utcnow() + timedelta(seconds=SIREN_OFF_DELAY)
        )
        await hass.async_block_till_done()

        assert entity.is_on is False


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_siren_off_push_clears_immediately(hass: HomeAssistant) -> None:
    """A sirenOff push must not wait for the auto-off timer."""
    device = _device(channel_id="0")
    coordinator = _coordinator([device])
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    entity = ImouSiren(coordinator, entry, PARAM_SIREN, device)
    async with _listening_siren(hass, entity):
        hass.bus.async_fire(
            EVENT_IMOU_ALARM,
            {"device_id": "SN1", "channel_id": "0", "msg_type": "sirenOn"},
        )
        await hass.async_block_till_done()
        assert entity.is_on is True

        hass.bus.async_fire(
            EVENT_IMOU_ALARM,
            {"device_id": "SN1", "channel_id": "0", "msg_type": "sirenOff"},
        )
        await hass.async_block_till_done()

        assert entity.is_on is False


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_other_camera_does_not_flip_siren(hass: HomeAssistant) -> None:
    """A push for another channel must not change this siren."""
    device = _device(channel_id="0")
    coordinator = _coordinator([device])
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    entity = ImouSiren(coordinator, entry, PARAM_SIREN, device)
    async with _listening_siren(hass, entity):
        hass.bus.async_fire(
            EVENT_IMOU_ALARM,
            {"device_id": "SN1", "channel_id": "1", "msg_type": "sirenOn"},
        )
        await hass.async_block_till_done()

        assert entity.is_on is False


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_siren_on_turns_entity_on(hass: HomeAssistant) -> None:
    """The live webhook path turns the matching siren entity on."""
    setup_imou_runtime(hass, selected_devices=["SN1"])
    device = _device(channel_id="0")
    coordinator = _coordinator([device])
    entry = next(iter(hass.config_entries.async_entries(DOMAIN)))
    entity = ImouSiren(coordinator, entry, PARAM_SIREN, device)
    async with _listening_siren(hass, entity):
        await async_handle_imou_webhook(
            hass,
            "webhook-id",
            MockRequest({"msgType": "sirenOn", "deviceId": "SN1", "channelId": "0"}),
        )
        await hass.async_block_till_done(wait_background_tasks=True)

        assert entity.is_on is True
