"""Camera white light as a Home Assistant light, not a switch.

Reolink and UniFi Protect expose the floodlight the same way. The lamp is
on or off; this camera has no brightness to set.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from custom_components.imou_life.const import (
    DOMAIN,
    PARAM_BULB,
    PARAM_STATUS,
    PARAM_WHITE_LIGHT,
    imou_life_device_key,
)
from custom_components.imou_life.light import (
    ImouBulbLight,
    ImouWhiteLight,
    _iter_bulbs,
    _iter_white_lights,
)
from homeassistant.components.light import ATTR_EFFECT, ColorMode, LightEntityFeature
from homeassistant.core import HomeAssistant
from pyimouapi.bulb_light import (
    MODE_SCENE,
    REF_MODE,
    REF_SCENE,
    REF_SWITCH,
    empty_bulb_state,
)
from pyimouapi.const import PARAM_STATE
from pyimouapi.ha_device import DeviceStatus, ImouHaDevice
from pytest_homeassistant_custom_component.common import MockConfigEntry

from . import USER_INPUT


def _device(*, has_light: bool = True, on: bool = False) -> MagicMock:
    device = MagicMock(spec=ImouHaDevice)
    device.device_id = "cam1"
    device.channel_id = "0"
    device.product_id = None
    device.device_name = "Cam"
    device.channel_name = "Cam"
    device.manufacturer = "Imou"
    device.model = "IPC"
    device.swversion = "1.0"
    device.sensors = {PARAM_STATUS: {PARAM_STATE: DeviceStatus.ONLINE.value}}
    device.switches = {}
    if has_light:
        device.switches[PARAM_WHITE_LIGHT] = {PARAM_STATE: on}
    return device


def _coordinator(device: MagicMock) -> MagicMock:
    coordinator = MagicMock()
    key = imou_life_device_key(device)
    coordinator.devices = [device]
    coordinator.devices_by_key = {key: device}
    coordinator.last_update_success = True
    coordinator.device_manager.async_switch_operation = AsyncMock()
    return coordinator


def _light(hass: HomeAssistant, device: MagicMock) -> ImouWhiteLight:
    coordinator = _coordinator(device)
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    return ImouWhiteLight(coordinator, entry, PARAM_WHITE_LIGHT, device)


@pytest.mark.usefixtures("enable_custom_integrations")
def test_white_light_is_an_on_off_light(hass: HomeAssistant) -> None:
    """The lamp has no brightness; Home Assistant must not show a slider."""
    light = _light(hass, _device())
    assert light.color_mode is ColorMode.ONOFF
    assert light.supported_color_modes == {ColorMode.ONOFF}


def test_iter_skips_cameras_without_white_light() -> None:
    """A camera without the lamp must not get a light entity."""
    coordinator = MagicMock()
    coordinator.devices = [_device(has_light=False), _device()]
    assert [key for key, _device in _iter_white_lights(coordinator)] == [
        PARAM_WHITE_LIGHT
    ]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_turn_on_writes_the_same_switch_the_cloud_uses(
    hass: HomeAssistant,
) -> None:
    """The cloud still speaks a switch; only the Home Assistant type changed."""
    device = _device(on=False)
    light = _light(hass, device)
    light.async_write_ha_state = MagicMock()

    assert light.is_on is False
    await light.async_turn_on()

    light.coordinator.device_manager.async_switch_operation.assert_awaited_once_with(
        device, PARAM_WHITE_LIGHT, True
    )
    light.async_write_ha_state.assert_called_once()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_turn_off_clears_the_lamp(hass: HomeAssistant) -> None:
    """Turning the light off must reach the same cloud write as the old switch."""
    device = _device(on=True)
    light = _light(hass, device)
    light.async_write_ha_state = MagicMock()

    assert light.is_on is True
    await light.async_turn_off()

    light.coordinator.device_manager.async_switch_operation.assert_awaited_once_with(
        device, PARAM_WHITE_LIGHT, False
    )


def _bulb_device() -> MagicMock:
    device = MagicMock(spec=ImouHaDevice)
    device.device_id = "bulb1"
    device.channel_id = None
    device.product_id = "3C1DUNTJ"
    device.device_name = "Bulb"
    device.channel_name = None
    device.manufacturer = "Imou"
    device.model = "L1"
    device.swversion = "1.0"
    device.sensors = {PARAM_STATUS: {PARAM_STATE: DeviceStatus.ONLINE.value}}
    device.switches = {}
    device.bulb_light = empty_bulb_state()
    return device


def _bulb(hass: HomeAssistant, device: MagicMock) -> ImouBulbLight:
    coordinator = _coordinator(device)
    coordinator.device_manager.async_set_bulb_light = AsyncMock()
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    return ImouBulbLight(coordinator, entry, PARAM_BULB, device)


@pytest.mark.usefixtures("enable_custom_integrations")
def test_bulb_supports_color_temperature_color_and_scenes(
    hass: HomeAssistant,
) -> None:
    """One light covers white, color, and the scene list."""
    light = _bulb(hass, _bulb_device())
    assert light.supported_color_modes == {ColorMode.COLOR_TEMP, ColorMode.HS}
    assert light.supported_features & LightEntityFeature.EFFECT
    assert "Movie" in (light.effect_list or [])
    assert light.is_on is False
    assert light.color_mode is None


def test_iter_bulbs_skips_cameras() -> None:
    """A camera floodlight is not a color bulb."""
    camera = _device()
    camera.bulb_light = None
    coordinator = MagicMock()
    coordinator.devices = [camera, _bulb_device()]
    assert [key for key, _device in _iter_bulbs(coordinator)] == [PARAM_BULB]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_bulb_scene_is_one_property_write(hass: HomeAssistant) -> None:
    """Picking a scene turns the bulb on in scene mode with that scene."""
    device = _bulb_device()
    light = _bulb(hass, device)
    light.async_write_ha_state = MagicMock()

    await light.async_turn_on(**{ATTR_EFFECT: "Movie"})

    light.coordinator.device_manager.async_set_bulb_light.assert_awaited_once_with(
        device,
        {REF_SWITCH: 1, REF_MODE: MODE_SCENE, REF_SCENE: 2},
    )
