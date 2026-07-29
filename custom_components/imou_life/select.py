"""Imou select entities."""

from __future__ import annotations

from typing import override

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pyimouapi.const import PARAM_CURRENT_OPTION, PARAM_OPTIONS
from pyimouapi.exceptions import ImouException
from pyimouapi.ha_device import ImouHaDevice

from .const import (
    PARAM_DEVICE_VOLUME,
    PARAM_MODE,
    PARAM_NIGHT_VISION_MODE,
    imou_life_device_key,
)
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .entity import ImouEntity

PARALLEL_UPDATES = 0

SELECT_TYPES: tuple[SelectEntityDescription, ...] = (
    SelectEntityDescription(
        key=PARAM_DEVICE_VOLUME,
        translation_key=PARAM_DEVICE_VOLUME,
    ),
    SelectEntityDescription(
        key=PARAM_MODE,
        translation_key=PARAM_MODE,
    ),
    SelectEntityDescription(
        key=PARAM_NIGHT_VISION_MODE,
        translation_key=PARAM_NIGHT_VISION_MODE,
    ),
)


def _iter_selects(
    coordinator: ImouDataUpdateCoordinator,
) -> list[tuple[SelectEntityDescription, ImouHaDevice]]:
    """Return (description, device) pairs for supported selects."""
    return [
        (description, device)
        for device in coordinator.devices
        for description in SELECT_TYPES
        if description.key in device.selects
    ]


async def async_setup_entry(
    hass: HomeAssistant, entry: ImouConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Imou select entities."""
    coordinator = entry.runtime_data.coordinator

    def _async_add_selects(new_devices: list[ImouHaDevice]) -> None:
        device_keys = {imou_life_device_key(device) for device in new_devices}
        async_add_entities(
            ImouSelect(coordinator, entry, description, device)
            for description, device in _iter_selects(coordinator)
            if imou_life_device_key(device) in device_keys
        )

    coordinator.new_device_callbacks.append(_async_add_selects)

    @callback
    def _remove_new_device_callback() -> None:
        if _async_add_selects in coordinator.new_device_callbacks:
            coordinator.new_device_callbacks.remove(_async_add_selects)

    entry.async_on_unload(_remove_new_device_callback)
    _async_add_selects(coordinator.devices)


class ImouSelect(ImouEntity, SelectEntity):
    """Representation of an Imou select."""

    entity_description: SelectEntityDescription

    def __init__(
        self,
        coordinator: ImouDataUpdateCoordinator,
        config_entry: ImouConfigEntry,
        description: SelectEntityDescription,
        device: ImouHaDevice,
    ) -> None:
        """Initialize Imou select."""
        super().__init__(coordinator, config_entry, description.key, device)
        self.entity_description = description

    @property
    @override
    def options(self) -> list[str]:
        """Return available options."""
        return self.device.selects[self._entity_type][PARAM_OPTIONS]

    @property
    @override
    def current_option(self) -> str | None:
        """Return the selected option."""
        return self.device.selects[self._entity_type][PARAM_CURRENT_OPTION]

    @override
    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        try:
            await self.coordinator.device_manager.async_select_option(
                self.device,
                self._entity_type,
                option,
            )
        except ImouException as err:
            raise HomeAssistantError(err.message) from err
        await self.coordinator.async_request_refresh()
