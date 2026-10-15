"""Imou lights: a camera floodlight, or one color bulb."""

from __future__ import annotations

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
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pyimouapi.bulb_light import (
    MAX_KELVIN,
    MIN_KELVIN,
    MODE_COLOR,
    MODE_SCENE,
    percent_to_brightness,
    properties_for_turn_off,
    properties_for_turn_on,
    scene_effect_name,
    scene_effect_names,
)
from pyimouapi.const import PARAM_STATE
from pyimouapi.exceptions import ImouException
from pyimouapi.ha_device import ImouHaDevice

from .const import PARAM_BULB, PARAM_WHITE_LIGHT
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .entity import ImouEntity, async_add_imou_entities

PARALLEL_UPDATES = 0


def _iter_white_lights(
    coordinator: ImouDataUpdateCoordinator,
) -> list[tuple[str, ImouHaDevice]]:
    """Return the white-light lamp on cameras that have one."""
    return [
        (PARAM_WHITE_LIGHT, device)
        for device in coordinator.devices
        if PARAM_WHITE_LIGHT in device.switches
    ]


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
    """Camera floodlight: on or off, the same write as the old switch."""

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

    @property
    @override
    def is_on(self) -> bool | None:
        """Return True when the lamp is on."""
        return self.device.switches[PARAM_WHITE_LIGHT][PARAM_STATE]

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the lamp on."""
        await self._async_set(True)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the lamp off."""
        await self._async_set(False)

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

    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_min_color_temp_kelvin = MIN_KELVIN
    _attr_max_color_temp_kelvin = MAX_KELVIN

    def __init__(
        self,
        coordinator: ImouDataUpdateCoordinator,
        config_entry: ImouConfigEntry,
        entity_type: str,
        device: ImouHaDevice,
    ) -> None:
        """Initialize color modes and the scene list."""
        super().__init__(coordinator, config_entry, entity_type, device)
        self._attr_supported_color_modes = {ColorMode.COLOR_TEMP, ColorMode.HS}
        self._attr_effect_list = scene_effect_names()

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

    @property
    @override
    def brightness(self) -> int | None:
        """White brightness, or color brightness while in color mode."""
        if not self.is_on:
            return None
        level = (
            self._state["value"]
            if self._state["mode"] == MODE_COLOR
            else self._state["brightness"]
        )
        return percent_to_brightness(int(level))

    @property
    @override
    def color_mode(self) -> ColorMode | None:
        """White is color temperature. Color is hue and saturation."""
        if not self.is_on:
            return None
        if self._state["mode"] == MODE_COLOR:
            return ColorMode.HS
        return ColorMode.COLOR_TEMP

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
        if not self.is_on or self._state["mode"] != MODE_SCENE:
            return None
        return scene_effect_name(int(self._state["scene"]))

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the bulb on and apply color, temperature, brightness, or a scene."""
        hs = kwargs.get(ATTR_HS_COLOR)
        try:
            payload = properties_for_turn_on(
                self._state,
                brightness=kwargs.get(ATTR_BRIGHTNESS),
                color_temp_kelvin=kwargs.get(ATTR_COLOR_TEMP_KELVIN),
                hs_color=(float(hs[0]), float(hs[1])) if hs is not None else None,
                effect=kwargs.get(ATTR_EFFECT),
            )
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err
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
