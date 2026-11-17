"""Imou lights: a camera floodlight, or one color bulb."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, override

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_EFFECT,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.util.color import color_temperature_to_hs
from pyimouapi.bulb_light import (
    BULB_V2_PROPERTY_BY_REF,
    MAX_KELVIN,
    MIN_KELVIN,
    MODE_COLOR,
    MODE_SCENE,
    REF_BRIGHTNESS,
    REF_COLOR_TEMP,
    REF_HUE,
    REF_MODE,
    REF_SATURATION,
    REF_SCENE,
    REF_SWITCH,
    REF_VALUE,
    percent_to_brightness,
    properties_for_turn_off,
    properties_for_turn_on,
    scene_effect_name,
    scene_effect_names,
)
from pyimouapi.const import PARAM_STATE
from pyimouapi.exceptions import ImouException
from pyimouapi.ha_device import ImouHaDevice

from .const import DOMAIN, PARAM_BULB, PARAM_WHITE_LIGHT, WHITE_LIGHT_OFF_DELAY
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .entity import ImouEntity, async_add_imou_entities

PARALLEL_UPDATES = 1

_COUNTDOWN_START = "white_light_start"
_COUNTDOWN_STOP = "white_light_stop"


def _iter_white_lights(
    coordinator: ImouDataUpdateCoordinator,
) -> list[tuple[str, ImouHaDevice]]:
    """Return the white-light lamp on cameras that have one."""
    return [
        (PARAM_WHITE_LIGHT, device)
        for device in coordinator.devices
        if _device_has_white_light(device)
    ]


def _device_has_white_light(device: ImouHaDevice) -> bool:
    """Countdown start/stop, or the steady lamp switch."""
    buttons = device.buttons or {}
    return (
        PARAM_WHITE_LIGHT in device.switches
        or _COUNTDOWN_START in buttons
        or _COUNTDOWN_STOP in buttons
    )


def _iter_bulbs(
    coordinator: ImouDataUpdateCoordinator,
) -> list[tuple[str, ImouHaDevice]]:
    """Return color bulbs. The relay is this light, not a switch."""
    return [
        (PARAM_BULB, device)
        for device in coordinator.devices
        if device.bulb_light is not None
    ]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Imou light entities."""
    async_add_imou_entities(
        entry, async_add_entities, ImouWhiteLight, _iter_white_lights
    )
    async_add_imou_entities(entry, async_add_entities, ImouBulbLight, _iter_bulbs)


class ImouWhiteLight(ImouEntity, LightEntity):
    """Camera floodlight. On for 30 seconds, or until it is turned off."""

    _attr_color_mode = ColorMode.ONOFF

    def __init__(
        self,
        coordinator: ImouDataUpdateCoordinator,
        config_entry: ImouConfigEntry,
        entity_type: str,
        device: ImouHaDevice,
    ) -> None:
        """Initialize the white-light entity."""
        super().__init__(coordinator, config_entry, entity_type, device)
        self._attr_supported_color_modes = {ColorMode.ONOFF}
        self._held_on = False
        self._unsub_off: Callable[[], None] | None = None

    def _uses_countdown(self) -> bool:
        """True when this camera has the countdown start/stop services."""
        buttons = self.device.buttons or {}
        return _COUNTDOWN_START in buttons or _COUNTDOWN_STOP in buttons

    @property
    @override
    def assumed_state(self) -> bool:
        """The countdown lamp does not report on/off through the steady switch."""
        return self._uses_countdown()

    async def async_added_to_hass(self) -> None:
        """Cancel a pending auto-off when the entity is removed."""
        await super().async_added_to_hass()
        self.async_on_remove(self._cancel_off_timer)

    @callback
    def _cancel_off_timer(self) -> None:
        """Drop a pending auto-off, if any."""
        if self._unsub_off is not None:
            self._unsub_off()
            self._unsub_off = None

    def _schedule_off(self) -> None:
        """Turn the lamp off after the hold, restarting if it was already on."""
        self._cancel_off_timer()
        self._unsub_off = async_call_later(
            self.hass, WHITE_LIGHT_OFF_DELAY, self._async_auto_off
        )

    async def _async_auto_off(self, _now: datetime) -> None:
        """Turn the lamp off when the hold ends."""
        self._unsub_off = None
        await self.async_turn_off()

    @property
    @override
    def is_on(self) -> bool | None:
        """Return True when the lamp is on."""
        if self._uses_countdown():
            return self._held_on
        switch = self.device.switches.get(PARAM_WHITE_LIGHT)
        if not isinstance(switch, dict):
            return None
        return switch.get(PARAM_STATE)

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start the countdown lamp, or the steady lamp, for 30 seconds."""
        if self._uses_countdown():
            await self._press(_COUNTDOWN_START)
            self._held_on = True
            self._schedule_off()
            self.async_write_ha_state()
            return
        await self._async_set(True)
        self._schedule_off()

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop the countdown lamp, or clear the steady lamp."""
        self._cancel_off_timer()
        if self._uses_countdown():
            await self._press(_COUNTDOWN_STOP)
            self._held_on = False
            self.async_write_ha_state()
            return
        await self._async_set(False)

    async def _press(self, button_type: str) -> None:
        """Invoke the countdown start or stop service."""
        if button_type not in (self.device.buttons or {}):
            return
        try:
            await self.coordinator.device_manager.async_press_button(
                self.device,
                button_type,
                0,
            )
        except ImouException as err:
            self._raise_imou_ha_error(err, "button_press_failed")

    async def _async_set(self, enable: bool) -> None:
        """Write the white-light switch through the vendor library."""
        try:
            await self.coordinator.device_manager.async_switch_operation(
                self.device,
                PARAM_WHITE_LIGHT,
                enable,
            )
        except ImouException as err:
            self._raise_imou_ha_error(err, "switch_operation_failed")
        self.async_write_ha_state()


class ImouBulbLight(ImouEntity, LightEntity):
    """Color bulb: white, color, and scenes on one light."""

    def __init__(
        self,
        coordinator: ImouDataUpdateCoordinator,
        config_entry: ImouConfigEntry,
        entity_type: str,
        device: ImouHaDevice,
    ) -> None:
        """Initialize the bulb entity."""
        super().__init__(coordinator, config_entry, entity_type, device)

    @property
    def _supported_property_refs(self) -> set[str]:
        """Return property refs from the device's current capability snapshot."""
        if self.device.bulb_v2 and self.device.capability_set is not None:
            property_ids = set(self.device.capability_set.properties)
            return {
                ref
                for ref, property_id in BULB_V2_PROPERTY_BY_REF.items()
                if property_id in property_ids
            }
        return set(BULB_V2_PROPERTY_BY_REF)

    @property
    def _supported_color_modes(self) -> set[ColorMode]:
        """Return modes valid for the device's current property subset."""
        supported_property_refs = self._supported_property_refs
        color_modes: set[ColorMode] = set()
        if {REF_BRIGHTNESS, REF_COLOR_TEMP} <= supported_property_refs:
            color_modes.add(ColorMode.COLOR_TEMP)
        if {
            REF_HUE,
            REF_SATURATION,
            REF_VALUE,
        } <= supported_property_refs:
            color_modes.add(ColorMode.HS)
        if not color_modes:
            color_modes.add(
                ColorMode.BRIGHTNESS
                if REF_BRIGHTNESS in supported_property_refs
                else ColorMode.ONOFF
            )
        return color_modes

    @property
    @override
    def supported_color_modes(self) -> set[ColorMode]:
        """Return color modes from the current capability snapshot."""
        return self._supported_color_modes

    @property
    @override
    def supported_features(self) -> LightEntityFeature:
        """Return scene support from the current capability snapshot."""
        if REF_SCENE in self._supported_property_refs:
            return LightEntityFeature.EFFECT
        return LightEntityFeature(0)

    @property
    @override
    def effect_list(self) -> list[str] | None:
        """Return scenes only while the scene property is supported."""
        if REF_SCENE in self._supported_property_refs:
            return scene_effect_names()
        return None

    @property
    @override
    def min_color_temp_kelvin(self) -> int:
        """Return the bulb's minimum supported color temperature."""
        return MIN_KELVIN

    @property
    @override
    def max_color_temp_kelvin(self) -> int:
        """Return the bulb's maximum supported color temperature."""
        return MAX_KELVIN

    @property
    def _state(self) -> dict[str, Any]:
        state = self.device.bulb_light
        if state is None:
            raise HomeAssistantError("device has no color bulb")
        return state

    @property
    @override
    def is_on(self) -> bool | None:
        """Return True when the bulb relay is on."""
        return bool(self._state["on"])

    def _active_effect(self) -> str | None:
        """Scene name when this bulb is on in a known scene."""
        if (
            REF_SCENE not in self._supported_property_refs
            or not self.is_on
            or self._state["mode"] != MODE_SCENE
        ):
            return None
        return scene_effect_name(int(self._state["scene"]))

    @property
    @override
    def brightness(self) -> int | None:
        """White brightness, or color brightness while in color mode."""
        if not self.is_on or self._active_effect() is not None:
            return None
        if self.color_mode == ColorMode.HS:
            level = self._state["value"]
        elif REF_BRIGHTNESS in self._supported_property_refs:
            level = self._state["brightness"]
        else:
            return None
        return percent_to_brightness(int(level))

    @property
    @override
    def color_mode(self) -> ColorMode | None:
        """White is color temperature. Color is hue and saturation.

        A known scene is an effect. Home Assistant allows on/off for the color
        mode while that effect is active, so the card does not show white.
        """
        if not self.is_on:
            return None
        if self._active_effect() is not None:
            return ColorMode.ONOFF
        if (
            self._state["mode"] == MODE_COLOR
            and ColorMode.HS in self._supported_color_modes
        ):
            return ColorMode.HS
        if ColorMode.COLOR_TEMP in self._supported_color_modes:
            return ColorMode.COLOR_TEMP
        if ColorMode.HS in self._supported_color_modes:
            return ColorMode.HS
        return next(iter(self._supported_color_modes))

    @property
    @override
    def color_temp_kelvin(self) -> int | None:
        """White color temperature in kelvin."""
        if self.color_mode != ColorMode.COLOR_TEMP:
            return None
        return int(self._state["color_temp"])

    @property
    @override
    def hs_color(self) -> tuple[float, float] | None:
        """Hue and saturation while the bulb is in color mode."""
        if self.color_mode != ColorMode.HS:
            return None
        return (float(self._state["hue"]), float(self._state["saturation"]))

    @property
    @override
    def effect(self) -> str | None:
        """The active scene, when the bulb is in scene mode."""
        return self._active_effect()

    def _hs_is_white_preview(self, hs: tuple[float, float]) -> bool:
        """True when the color wheel is still showing the white color temperature.

        In white mode Home Assistant draws the wheel from the color temperature.
        The first touch repeats that color. Applying it would replace the saved
        color hue and saturation.
        """
        if int(self._state["mode"]) == MODE_COLOR:
            return False
        derived = color_temperature_to_hs(int(self._state["color_temp"]))
        hue_delta = abs(hs[0] - derived[0]) % 360
        hue_delta = min(hue_delta, 360 - hue_delta)
        return hue_delta <= 1.5 and abs(hs[1] - derived[1]) <= 1.5

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the bulb on and apply color, temperature, brightness, or a scene."""
        raw_hs = (
            kwargs.get(ATTR_HS_COLOR)
            if ColorMode.HS in self._supported_color_modes
            else None
        )
        hs = (float(raw_hs[0]), float(raw_hs[1])) if raw_hs is not None else None
        color_temp_kelvin = (
            kwargs.get(ATTR_COLOR_TEMP_KELVIN)
            if ColorMode.COLOR_TEMP in self._supported_color_modes
            else None
        )
        effect = (
            kwargs.get(ATTR_EFFECT)
            if REF_SCENE in self._supported_property_refs
            else None
        )
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        if hs is None and REF_BRIGHTNESS not in self._supported_property_refs:
            brightness = None
        preview = (
            hs is not None
            and color_temp_kelvin is None
            and effect is None
            and self._hs_is_white_preview(hs)
        )
        try:
            if preview:
                payload = {REF_SWITCH: 1, REF_MODE: MODE_COLOR}
            else:
                payload = properties_for_turn_on(
                    self._state,
                    brightness=brightness,
                    color_temp_kelvin=color_temp_kelvin,
                    hs_color=hs,
                    effect=effect,
                )
        except ValueError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="unknown_bulb_effect",
            ) from err
        payload = {
            ref: value
            for ref, value in payload.items()
            if ref in self._supported_property_refs
        }
        await self._async_write(payload)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the bulb off. Color is left as it was."""
        await self._async_write(properties_for_turn_off())

    async def _async_write(self, payload: dict[str, int]) -> None:
        """Send one property map and refresh the light card."""
        try:
            await self.coordinator.device_manager.async_set_bulb_light(
                self.device, payload
            )
        except ImouException as err:
            self._raise_imou_ha_error(err, "switch_operation_failed")
        self.async_write_ha_state()
