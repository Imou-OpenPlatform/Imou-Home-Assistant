"""Camera white light as a Home Assistant light, not a switch.

Reolink and UniFi Protect expose the floodlight the same way. The lamp is
on or off; this camera has no brightness to set.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from custom_components.imou_life.const import (
    DOMAIN,
    PARAM_BULB,
    PARAM_STATUS,
    PARAM_WHITE_LIGHT,
    WHITE_LIGHT_OFF_DELAY,
    imou_life_device_key,
)
from custom_components.imou_life.light import (
    ImouBulbLight,
    ImouWhiteLight,
    _iter_bulbs,
    _iter_white_lights,
)
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from homeassistant.util.color import color_temperature_to_hs
from pyimouapi.bulb_light import (
    BULB_PROP_BRIGHTNESS,
    BULB_PROP_COLOR_TEMP,
    BULB_PROP_HUE,
    BULB_PROP_MODE,
    BULB_PROP_SATURATION,
    BULB_PROP_SCENE,
    BULB_PROP_SWITCH,
    BULB_PROP_VALUE,
    MODE_COLOR,
    MODE_SCENE,
    MODE_WHITE,
    empty_bulb_state,
    scene_effect_names,
)
from pyimouapi.capability.catalog import CapabilitySet
from pyimouapi.const import PARAM_STATE
from pyimouapi.ha_device import DeviceStatus, ImouHaDevice
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

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
    device.buttons = {}
    if has_light:
        device.switches[PARAM_WHITE_LIGHT] = {PARAM_STATE: on}
    return device


def _coordinator(device: MagicMock) -> MagicMock:
    coordinator = MagicMock()
    key = imou_life_device_key(device)

    async def _apply_switch(_device: MagicMock, switch_type: str, enable: bool) -> None:
        _device.switches[switch_type][PARAM_STATE] = enable

    coordinator.devices = [device]
    coordinator.devices_by_key = {key: device}
    coordinator.last_update_success = True
    coordinator.device_manager.async_switch_operation = AsyncMock(
        side_effect=_apply_switch
    )
    coordinator.device_manager.async_press_button = AsyncMock()
    return coordinator


def _light(hass: HomeAssistant, device: MagicMock) -> ImouWhiteLight:
    coordinator = _coordinator(device)
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    light = ImouWhiteLight(coordinator, entry, PARAM_WHITE_LIGHT, device)
    light.hass = hass
    return light


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
    assert light.is_on is True
    light._cancel_off_timer()


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
    assert light.is_on is False


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_white_light_turns_off_after_thirty_seconds(hass: HomeAssistant) -> None:
    """The lamp switches itself off after the same kind of hold the siren uses."""
    device = _device(on=False)
    light = _light(hass, device)
    light.async_write_ha_state = MagicMock()

    await light.async_turn_on()
    assert light.is_on is True

    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=WHITE_LIGHT_OFF_DELAY)
    )
    await hass.async_block_till_done()

    assert light.is_on is False
    light.coordinator.device_manager.async_switch_operation.assert_awaited_with(
        device, PARAM_WHITE_LIGHT, False
    )


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_white_light_turn_off_cancels_the_hold(hass: HomeAssistant) -> None:
    """Turning the lamp off by hand does not write off again when the hold ends."""
    device = _device(on=False)
    light = _light(hass, device)
    light.async_write_ha_state = MagicMock()

    await light.async_turn_on()
    await light.async_turn_off()
    assert light.is_on is False

    async_fire_time_changed(
        hass, dt_util.utcnow() + timedelta(seconds=WHITE_LIGHT_OFF_DELAY)
    )
    await hass.async_block_till_done()

    assert light.coordinator.device_manager.async_switch_operation.await_count == 2


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_countdown_light_uses_start_and_stop(hass: HomeAssistant) -> None:
    """Countdown lamps use the start and stop services, not the steady switch."""
    device = _device(on=False)
    device.buttons = {"white_light_start": {}, "white_light_stop": {}}
    light = _light(hass, device)
    light.async_write_ha_state = MagicMock()

    await light.async_turn_on()
    assert light.is_on is True
    light.coordinator.device_manager.async_press_button.assert_awaited_once_with(
        device, "white_light_start", 0
    )
    light.coordinator.device_manager.async_switch_operation.assert_not_awaited()

    await light.async_turn_off()
    assert light.is_on is False
    light.coordinator.device_manager.async_press_button.assert_awaited_with(
        device, "white_light_stop", 0
    )
    light.coordinator.device_manager.async_switch_operation.assert_not_awaited()


def _bulb_device(*, supported_refs: set[str] | None = None) -> MagicMock:
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
    device.capability_ids = (
        frozenset(supported_refs) if supported_refs is not None else None
    )
    device.capability_set = (
        CapabilitySet(
            properties=dict.fromkeys(supported_refs),  # type: ignore[dict-item]
            services=frozenset(),
            events=frozenset(),
        )
        if supported_refs is not None
        else None
    )
    device.bulb_v2 = supported_refs is not None
    return device


def _set_bulb_capabilities(
    device: MagicMock,
    supported_refs: set[str],
) -> None:
    """Replace the public capability snapshot used by an existing entity."""
    device.capability_ids = frozenset(supported_refs)
    device.capability_set = CapabilitySet(
        properties=dict.fromkeys(supported_refs),  # type: ignore[dict-item]
        services=frozenset(),
        events=frozenset(),
    )


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
    """A legacy bulb keeps its existing white, color, and scene behavior."""
    light = _bulb(hass, _bulb_device())
    assert light.supported_color_modes == {ColorMode.COLOR_TEMP, ColorMode.HS}
    assert light.supported_features & LightEntityFeature.EFFECT
    assert "Movie" in (light.effect_list or [])
    assert light.is_on is False
    assert light.color_mode is None


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize(
    ("supported_refs", "supported_color_modes", "supported_features"),
    [
        ({BULB_PROP_SWITCH}, {ColorMode.ONOFF}, LightEntityFeature(0)),
        (
            {BULB_PROP_SWITCH, BULB_PROP_BRIGHTNESS},
            {ColorMode.BRIGHTNESS},
            LightEntityFeature(0),
        ),
        (
            {
                BULB_PROP_SWITCH,
                BULB_PROP_MODE,
                BULB_PROP_BRIGHTNESS,
                BULB_PROP_COLOR_TEMP,
            },
            {ColorMode.COLOR_TEMP},
            LightEntityFeature(0),
        ),
        (
            {BULB_PROP_SWITCH, BULB_PROP_MODE, BULB_PROP_COLOR_TEMP},
            {ColorMode.ONOFF},
            LightEntityFeature(0),
        ),
        (
            {
                BULB_PROP_SWITCH,
                BULB_PROP_MODE,
                BULB_PROP_HUE,
                BULB_PROP_SATURATION,
                BULB_PROP_VALUE,
            },
            {ColorMode.HS},
            LightEntityFeature(0),
        ),
        (
            {BULB_PROP_SWITCH, BULB_PROP_MODE, BULB_PROP_SCENE},
            {ColorMode.ONOFF},
            LightEntityFeature.EFFECT,
        ),
        (
            {
                BULB_PROP_SWITCH,
                BULB_PROP_MODE,
                BULB_PROP_BRIGHTNESS,
                BULB_PROP_COLOR_TEMP,
                BULB_PROP_HUE,
                BULB_PROP_SATURATION,
                BULB_PROP_VALUE,
                BULB_PROP_SCENE,
            },
            {ColorMode.COLOR_TEMP, ColorMode.HS},
            LightEntityFeature.EFFECT,
        ),
    ],
)
def test_v2_bulb_attributes_follow_capability_snapshot(
    hass: HomeAssistant,
    supported_refs: set[str],
    supported_color_modes: set[ColorMode],
    supported_features: LightEntityFeature,
) -> None:
    """V2 controls only advertise the properties present at entity creation."""
    device = _bulb_device(supported_refs=supported_refs)
    device.bulb_light["on"] = True
    light = _bulb(hass, device)

    assert light.supported_color_modes == supported_color_modes
    assert light.supported_features == supported_features
    assert light.effect_list == (
        scene_effect_names() if BULB_PROP_SCENE in supported_refs else None
    )
    if {BULB_PROP_BRIGHTNESS, BULB_PROP_COLOR_TEMP} <= supported_refs:
        assert light.min_color_temp_kelvin == 2700
        assert light.max_color_temp_kelvin == 6500
    else:
        assert "min_color_temp_kelvin" not in light.capability_attributes
        assert "max_color_temp_kelvin" not in light.capability_attributes
    assert light.color_mode in supported_color_modes


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_v2_bulb_live_entity_tracks_capability_rediscovery(
    hass: HomeAssistant,
) -> None:
    """One live entity immediately follows an expanded and reduced snapshot."""
    device = _bulb_device(supported_refs={BULB_PROP_SWITCH})
    device.bulb_light["on"] = True
    light = _bulb(hass, device)
    light.async_write_ha_state = MagicMock()

    assert light.supported_color_modes == {ColorMode.ONOFF}
    assert light.supported_features == LightEntityFeature(0)
    assert light.effect_list is None

    all_refs = {
        BULB_PROP_SWITCH,
        BULB_PROP_MODE,
        BULB_PROP_BRIGHTNESS,
        BULB_PROP_COLOR_TEMP,
        BULB_PROP_HUE,
        BULB_PROP_SATURATION,
        BULB_PROP_VALUE,
        BULB_PROP_SCENE,
    }
    _set_bulb_capabilities(device, all_refs)

    assert light.supported_color_modes == {ColorMode.COLOR_TEMP, ColorMode.HS}
    assert light.color_mode is ColorMode.COLOR_TEMP
    assert light.supported_features == LightEntityFeature.EFFECT
    assert light.effect_list == scene_effect_names()
    assert light.capability_attributes["min_color_temp_kelvin"] == 2700
    assert light.capability_attributes["max_color_temp_kelvin"] == 6500

    await light.async_turn_on(**{ATTR_BRIGHTNESS: 128, ATTR_COLOR_TEMP_KELVIN: 4000})
    light.coordinator.device_manager.async_set_bulb_light.assert_awaited_once_with(
        device,
        {
            BULB_PROP_SWITCH: 1,
            BULB_PROP_MODE: MODE_WHITE,
            BULB_PROP_BRIGHTNESS: 50,
            BULB_PROP_COLOR_TEMP: 4000,
        },
    )

    light.coordinator.device_manager.async_set_bulb_light.reset_mock()
    _set_bulb_capabilities(device, {BULB_PROP_SWITCH})

    assert light.supported_color_modes == {ColorMode.ONOFF}
    assert light.color_mode is ColorMode.ONOFF
    assert light.supported_features == LightEntityFeature(0)
    assert light.effect_list is None
    assert "min_color_temp_kelvin" not in light.capability_attributes
    assert "max_color_temp_kelvin" not in light.capability_attributes
    assert ATTR_BRIGHTNESS not in light.state_attributes
    assert ATTR_COLOR_TEMP_KELVIN not in light.state_attributes
    assert ATTR_EFFECT not in light.state_attributes

    await light.async_turn_on(
        **{
            ATTR_BRIGHTNESS: 128,
            ATTR_COLOR_TEMP_KELVIN: 4000,
            ATTR_HS_COLOR: (120, 60),
            ATTR_EFFECT: "Movie",
        }
    )
    light.coordinator.device_manager.async_set_bulb_light.assert_awaited_once_with(
        device, {BULB_PROP_SWITCH: 1}
    )
    assert [candidate for _, candidate in _iter_bulbs(light.coordinator)] == [device]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_v2_bulb_color_temperature_requires_white_level(
    hass: HomeAssistant,
) -> None:
    """CT without white brightness remains ONOFF and rejects CT service fields."""
    device = _bulb_device(
        supported_refs={BULB_PROP_SWITCH, BULB_PROP_MODE, BULB_PROP_COLOR_TEMP}
    )
    device.bulb_light["on"] = True
    light = _bulb(hass, device)
    light.async_write_ha_state = MagicMock()

    assert light.supported_color_modes == {ColorMode.ONOFF}
    assert light.color_mode is ColorMode.ONOFF
    assert ATTR_BRIGHTNESS not in light.state_attributes
    assert ATTR_COLOR_TEMP_KELVIN not in light.state_attributes

    await light.async_turn_on(**{ATTR_BRIGHTNESS: 128, ATTR_COLOR_TEMP_KELVIN: 4000})
    light.coordinator.device_manager.async_set_bulb_light.assert_awaited_once_with(
        device, {BULB_PROP_SWITCH: 1}
    )


@pytest.mark.usefixtures("enable_custom_integrations")
def test_v2_bulb_does_not_report_an_unsupported_scene(
    hass: HomeAssistant,
) -> None:
    """A mode value alone must not activate an effect or an invalid ONOFF mode."""
    device = _bulb_device(
        supported_refs={
            BULB_PROP_SWITCH,
            BULB_PROP_MODE,
            BULB_PROP_BRIGHTNESS,
            BULB_PROP_COLOR_TEMP,
        }
    )
    device.bulb_light["on"] = True
    device.bulb_light["mode"] = MODE_SCENE
    device.bulb_light["scene"] = 2
    light = _bulb(hass, device)

    assert light.effect is None
    assert light.color_mode is ColorMode.COLOR_TEMP


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize(
    ("supported_refs", "expected_payload"),
    [
        ({BULB_PROP_SWITCH}, {BULB_PROP_SWITCH: 1}),
        (
            {BULB_PROP_SWITCH, BULB_PROP_BRIGHTNESS},
            {BULB_PROP_SWITCH: 1, BULB_PROP_BRIGHTNESS: 50},
        ),
        (
            {
                BULB_PROP_SWITCH,
                BULB_PROP_MODE,
                BULB_PROP_BRIGHTNESS,
                BULB_PROP_COLOR_TEMP,
            },
            {
                BULB_PROP_SWITCH: 1,
                BULB_PROP_MODE: MODE_WHITE,
                BULB_PROP_BRIGHTNESS: 50,
                BULB_PROP_COLOR_TEMP: 4000,
            },
        ),
        (
            {
                BULB_PROP_SWITCH,
                BULB_PROP_MODE,
                BULB_PROP_HUE,
                BULB_PROP_SATURATION,
                BULB_PROP_VALUE,
            },
            {
                BULB_PROP_SWITCH: 1,
                BULB_PROP_MODE: MODE_COLOR,
                BULB_PROP_HUE: 120,
                BULB_PROP_SATURATION: 60,
                BULB_PROP_VALUE: 50,
            },
        ),
        (
            {BULB_PROP_SWITCH, BULB_PROP_MODE, BULB_PROP_SCENE},
            {BULB_PROP_SWITCH: 1, BULB_PROP_MODE: MODE_SCENE, BULB_PROP_SCENE: 2},
        ),
    ],
)
async def test_v2_bulb_service_writes_only_supported_properties(
    hass: HomeAssistant,
    supported_refs: set[str],
    expected_payload: dict[str, int],
) -> None:
    """Optional service fields never call properties absent from capability.list."""
    device = _bulb_device(supported_refs=supported_refs)
    light = _bulb(hass, device)
    light.async_write_ha_state = MagicMock()

    await light.async_turn_on(
        **{
            ATTR_BRIGHTNESS: 128,
            ATTR_COLOR_TEMP_KELVIN: 4000,
            ATTR_HS_COLOR: (120, 60),
            ATTR_EFFECT: "Movie",
        }
    )

    light.coordinator.device_manager.async_set_bulb_light.assert_awaited_once_with(
        device, expected_payload
    )


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
        {BULB_PROP_SWITCH: 1, BULB_PROP_MODE: MODE_SCENE, BULB_PROP_SCENE: 2},
    )


@pytest.mark.usefixtures("enable_custom_integrations")
def test_an_active_scene_is_not_reported_as_white(hass: HomeAssistant) -> None:
    """A scene is on/off plus an effect, so the card does not show white."""
    device = _bulb_device()
    device.bulb_light["on"] = True
    device.bulb_light["mode"] = MODE_SCENE
    device.bulb_light["scene"] = 2
    device.bulb_light["brightness"] = 80
    device.bulb_light["color_temp"] = 3000
    light = _bulb(hass, device)

    assert light.color_mode is ColorMode.ONOFF
    assert light.brightness is None
    assert light.color_temp_kelvin is None
    assert light.hs_color is None
    assert light.effect == "Movie"


@pytest.mark.usefixtures("enable_custom_integrations")
def test_an_off_bulb_hides_brightness_and_color(hass: HomeAssistant) -> None:
    """Off means no color mode and no leftover brightness or scene."""
    device = _bulb_device()
    light = _bulb(hass, device)

    assert light.brightness is None
    assert light.hs_color is None
    assert light.effect is None
    assert light.color_temp_kelvin is None


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_brightness_during_a_scene_selects_white(hass: HomeAssistant) -> None:
    """A brightness-only service call leaves the scene and writes white brightness."""
    device = _bulb_device()
    device.bulb_light["mode"] = MODE_SCENE
    light = _bulb(hass, device)
    light.async_write_ha_state = MagicMock()

    await light.async_turn_on(**{ATTR_BRIGHTNESS: 128})

    payload = light.coordinator.device_manager.async_set_bulb_light.await_args.args[1]
    assert payload[BULB_PROP_MODE] == MODE_WHITE
    assert BULB_PROP_BRIGHTNESS in payload
    assert BULB_PROP_SCENE not in payload


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.usefixtures("enable_custom_integrations")
async def test_color_temperature_does_not_change_brightness(
    hass: HomeAssistant,
) -> None:
    """A color-temperature change stays in white mode and leaves brightness."""
    device = _bulb_device()
    device.bulb_light["on"] = True
    device.bulb_light["mode"] = MODE_WHITE
    device.bulb_light["brightness"] = 21
    device.bulb_light["value"] = 3
    light = _bulb(hass, device)
    light.async_write_ha_state = MagicMock()

    await light.async_turn_on(**{ATTR_COLOR_TEMP_KELVIN: 4000})

    payload = light.coordinator.device_manager.async_set_bulb_light.await_args.args[1]
    assert payload == {
        BULB_PROP_SWITCH: 1,
        BULB_PROP_MODE: MODE_WHITE,
        BULB_PROP_COLOR_TEMP: 4000,
    }


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_white_preview_opens_the_saved_color(hass: HomeAssistant) -> None:
    """The color wheel's first color is the color temperature, not the saved color."""
    device = _bulb_device()
    device.bulb_light["on"] = True
    device.bulb_light["mode"] = MODE_WHITE
    device.bulb_light["color_temp"] = 4000
    device.bulb_light["hue"] = 200
    device.bulb_light["saturation"] = 80
    device.bulb_light["value"] = 40
    light = _bulb(hass, device)
    light.async_write_ha_state = MagicMock()

    await light.async_turn_on(**{ATTR_HS_COLOR: color_temperature_to_hs(4000)})

    payload = light.coordinator.device_manager.async_set_bulb_light.await_args.args[1]
    assert payload == {BULB_PROP_SWITCH: 1, BULB_PROP_MODE: MODE_COLOR}
    assert "132100" not in payload
    assert "132300" not in payload


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_unknown_scene_is_not_sent(hass: HomeAssistant) -> None:
    """An unknown scene raises a translated error and does not call the cloud."""
    device = _bulb_device()
    light = _bulb(hass, device)

    with pytest.raises(HomeAssistantError) as err:
        await light.async_turn_on(**{ATTR_EFFECT: "Music"})

    assert err.value.translation_key == "unknown_bulb_effect"
    light.coordinator.device_manager.async_set_bulb_light.assert_not_called()
