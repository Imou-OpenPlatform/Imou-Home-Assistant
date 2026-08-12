"""Imou text entities."""

from __future__ import annotations

from homeassistant.components.text import TextEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pyimouapi.const import PARAM_REF, PARAM_STATE
from pyimouapi.exceptions import ImouException
from pyimouapi.ha_device import ImouHaDevice

from .const import (
    DOMAIN,
    PARAM_COUNT_DOWN_SWITCH,
    PARAM_OVERCHARGE_SWITCH,
)
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .entity import ImouEntity, async_add_imou_entities

PARALLEL_UPDATES = 0


def _iter_texts(
    coordinator: ImouDataUpdateCoordinator,
) -> list[tuple[str, ImouHaDevice]]:
    """Return (text_type, device) pairs for supported text entities."""
    return [
        (text_type, device)
        for device in coordinator.devices
        for text_type in device.texts
    ]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Imou text entities."""
    async_add_imou_entities(entry, async_add_entities, ImouText, _iter_texts)


class ImouText(ImouEntity, TextEntity):
    """Representation of an Imou text entity."""

    @property
    def native_value(self) -> str | None:
        """Return the current text value."""
        return self.device.texts[self._entity_type][PARAM_STATE]

    async def async_set_value(self, value: str) -> None:
        """Write a new value to the device."""
        try:
            await self.coordinator.device_manager.async_set_text_value(
                self.device,
                self._entity_type,
                value,
            )
        except ImouException as e:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="set_text_value_failed",
                translation_placeholders={"error": e.message},
            ) from e
        self.async_write_ha_state()

    @property
    def pattern(self) -> str | None:
        """Optional regex validation pattern."""
        if self._entity_type == PARAM_OVERCHARGE_SWITCH:
            if self.device.texts[self._entity_type][PARAM_REF] == "128900":
                return (
                    "^(?:[5-9]|[1-9][0-9]{1,2}|[1-2][0-9]{3}|"
                    "3[0-5][0-9]{2}|36[0-7][0-9]|3680)$"
                )
            return (
                "^(?:[5-9]|[1-9][0-9]{1,2}|[1-9][0-9]{3}|"
                "1[0-9]{3}|2[0-4][0-9]{2}|2500)$"
            )
        if self._entity_type == PARAM_COUNT_DOWN_SWITCH:
            return "^(?:0|[1-9]|[1-9][0-9]{1,2}|1[0-3][0-9]{2}|14[0-3][0-9]|1440)$"
        return None
