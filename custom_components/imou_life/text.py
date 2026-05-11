"""Imou text entities."""

from __future__ import annotations

from homeassistant.components.text import TextEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pyimouapi.const import PARAM_REF, PARAM_STATE
from pyimouapi.exceptions import ImouException

from .const import PARAM_COUNT_DOWN_SWITCH, PARAM_OVERCHARGE_SWITCH
from .coordinator import ImouConfigEntry
from .entity import ImouEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ImouConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Imou text entities."""
    coordinator = entry.runtime_data
    entities: list[ImouText] = []
    for device in coordinator.devices:
        for text_type in device.texts:
            entities.append(ImouText(coordinator, entry, text_type, device))
    if entities:
        async_add_entities(entities)


class ImouText(ImouEntity, TextEntity):
    """Representation of an Imou text entity."""

    @property
    def native_value(self) -> str | None:
        """Return the current text value."""
        return self._device.texts[self._entity_type][PARAM_STATE]

    async def async_set_value(self, value: str) -> None:
        """Write a new value to the device."""
        try:
            await self._coordinator.device_manager.async_set_text_value(
                self._device,
                self._entity_type,
                value,
            )
            self._device.texts[self._entity_type][PARAM_STATE] = value
        except ImouException as e:
            raise HomeAssistantError(e.message) from e

    @property
    def pattern(self) -> str | None:
        """Optional regex validation pattern."""
        if self._entity_type == PARAM_OVERCHARGE_SWITCH:
            if self._device.texts[self._entity_type][PARAM_REF] == "128900":
                return "^(?:[5-9]|[1-9][0-9]{1,2}|[1-2][0-9]{3}|3[0-5][0-9]{2}|36[0-7][0-9]|3680)$"
            return "^(?:[5-9]|[1-9][0-9]{1,2}|[1-9][0-9]{3}|1[0-9]{3}|2[0-4][0-9]{2}|2500)$"
        if self._entity_type == PARAM_COUNT_DOWN_SWITCH:
            return "^(?:0|[1-9]|[1-9][0-9]{1,2}|1[0-3][0-9]{2}|14[0-3][0-9]|1440)$"
        return None
