"""Imou camera white light."""

from __future__ import annotations

from typing import Any, override

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pyimouapi.const import PARAM_STATE
from pyimouapi.exceptions import ImouException
from pyimouapi.ha_device import ImouHaDevice

from .const import PARAM_WHITE_LIGHT
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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Imou light entities."""
    async_add_imou_entities(
        entry, async_add_entities, ImouWhiteLight, _iter_white_lights
    )


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
