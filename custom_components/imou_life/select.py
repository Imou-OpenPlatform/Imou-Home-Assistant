"""Imou select entities."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pyimouapi.exceptions import ImouException

from .const import (
    PARAM_CURRENT_OPTION,
    PARAM_ENTITY_ID,
    PARAM_OPTION,
    PARAM_OPTIONS,
    SERVICE_SELECT,
)
from .coordinator import ImouConfigEntry
from .entity import ImouEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ImouConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Imou select entities."""
    coordinator = entry.runtime_data
    entities: list[ImouSelect] = []
    for device in coordinator.devices:
        for select_type in device.selects:
            entities.append(ImouSelect(coordinator, entry, select_type, device))
    if entities:
        async_add_entities(entities)

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
        return self._device.selects[self._entity_type][PARAM_OPTIONS]

    @property
    def current_option(self) -> str | None:
        """Return the selected option."""
        return self._device.selects[self._entity_type][PARAM_CURRENT_OPTION]

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        try:
            await self._coordinator.device_manager.async_select_option(
                self._device, self._entity_type, option
            )
            self._device.selects[self._entity_type][PARAM_CURRENT_OPTION] = option
            self.async_write_ha_state()
        except ImouException as err:
            raise HomeAssistantError(err.message) from err
