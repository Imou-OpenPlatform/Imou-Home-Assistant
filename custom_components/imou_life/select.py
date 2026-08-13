"""Imou select entities."""

from __future__ import annotations

from typing import override

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pyimouapi.const import PARAM_CURRENT_OPTION, PARAM_OPTIONS
from pyimouapi.exceptions import ImouException
from pyimouapi.ha_device import ImouHaDevice

from .const import (
    DOMAIN,
    PARAM_COLLECTION_POINT,
    PARAM_DEVICE_VOLUME,
    PARAM_MODE,
    PARAM_NIGHT_VISION_MODE,
)
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .entity import ImouEntity, async_add_imou_entities

PARALLEL_UPDATES = 0

# The collection point recalls a PTZ preset, which is an action on the camera;
# the rest set how the device behaves and belong under configuration.
SELECT_TYPES: tuple[SelectEntityDescription, ...] = (
    SelectEntityDescription(
        key=PARAM_DEVICE_VOLUME,
        translation_key=PARAM_DEVICE_VOLUME,
        entity_category=EntityCategory.CONFIG,
    ),
    SelectEntityDescription(
        key=PARAM_MODE,
        translation_key=PARAM_MODE,
        entity_category=EntityCategory.CONFIG,
    ),
    SelectEntityDescription(
        key=PARAM_NIGHT_VISION_MODE,
        translation_key=PARAM_NIGHT_VISION_MODE,
        entity_category=EntityCategory.CONFIG,
    ),
    SelectEntityDescription(
        key=PARAM_COLLECTION_POINT,
        translation_key=PARAM_COLLECTION_POINT,
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
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Imou select entities."""
    async_add_imou_entities(entry, async_add_entities, ImouSelect, _iter_selects)


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
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="select_option_failed",
                translation_placeholders={"error": err.message},
            ) from err
        self.async_write_ha_state()
