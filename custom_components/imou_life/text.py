"""Imou text entities."""

from __future__ import annotations

from homeassistant.components.text import TextEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pyimouapi.const import PARAM_REF, PARAM_STATE
from pyimouapi.exceptions import ImouException
from pyimouapi.ha_device import ImouHaDevice

from .const import (
    PARAM_COUNT_DOWN_SWITCH,
    PARAM_OVERCHARGE_SWITCH,
    imou_life_device_key,
)
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .entity import ImouEntity


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
    hass: HomeAssistant, entry: ImouConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Imou text entities."""
    coordinator = entry.runtime_data.coordinator

    def _async_add_texts(new_devices: list[ImouHaDevice]) -> None:
        device_keys = {imou_life_device_key(device) for device in new_devices}
        async_add_entities(
            ImouText(coordinator, entry, text_type, device)
            for text_type, device in _iter_texts(coordinator)
            if imou_life_device_key(device) in device_keys
        )

    coordinator.new_device_callbacks.append(_async_add_texts)

    @callback
    def _remove_new_device_callback() -> None:
        if _async_add_texts in coordinator.new_device_callbacks:
            coordinator.new_device_callbacks.remove(_async_add_texts)

    entry.async_on_unload(_remove_new_device_callback)
    _async_add_texts(coordinator.devices)


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
            self.device.texts[self._entity_type][PARAM_STATE] = value
        except ImouException as e:
            raise HomeAssistantError(e.message) from e

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
