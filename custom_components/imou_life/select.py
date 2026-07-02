"""Imou select entities."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pyimouapi.exceptions import ImouException
from pyimouapi.ha_device import ImouHaDevice

from .const import (
    PARAM_CURRENT_OPTION,
    PARAM_ENTITY_ID,
    PARAM_OPTION,
    PARAM_OPTIONS,
    SERVICE_SELECT,
    imou_life_device_key,
)
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .entity import ImouEntity


def _iter_selects(
    coordinator: ImouDataUpdateCoordinator,
) -> list[tuple[str, ImouHaDevice]]:
    """Return (select_type, device) pairs for supported selects."""
    return [
        (select_type, device)
        for device in coordinator.devices
        for select_type in device.selects
    ]


async def async_setup_entry(
    hass: HomeAssistant, entry: ImouConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Imou select entities."""
    coordinator = entry.runtime_data.coordinator

    def _async_add_selects(new_devices: list[ImouHaDevice]) -> None:
        device_keys = {imou_life_device_key(device) for device in new_devices}
        async_add_entities(
            ImouSelect(coordinator, entry, select_type, device)
            for select_type, device in _iter_selects(coordinator)
            if imou_life_device_key(device) in device_keys
        )

    coordinator.new_device_callbacks.append(_async_add_selects)

    @callback
    def _remove_new_device_callback() -> None:
        if _async_add_selects in coordinator.new_device_callbacks:
            coordinator.new_device_callbacks.remove(_async_add_selects)

    entry.async_on_unload(_remove_new_device_callback)
    _async_add_selects(coordinator.devices)

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_SELECT,
        {
            vol.Required(PARAM_ENTITY_ID): cv.entity_id,
            vol.Required(PARAM_OPTION): cv.string,
        },
        "async_select_option",
    )


class ImouSelect(ImouEntity, SelectEntity):
    """Representation of an Imou select."""

    @property
    def options(self) -> list[str]:
        """Return available options."""
        return self.device.selects[self._entity_type][PARAM_OPTIONS]

    @property
    def current_option(self) -> str | None:
        """Return the selected option."""
        return self.device.selects[self._entity_type][PARAM_CURRENT_OPTION]

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        try:
            await self.coordinator.device_manager.async_select_option(
                self.device, self._entity_type, option
            )
            self.device.selects[self._entity_type][PARAM_CURRENT_OPTION] = option
            self.async_write_ha_state()
        except ImouException as err:
            raise HomeAssistantError(err.message) from err
