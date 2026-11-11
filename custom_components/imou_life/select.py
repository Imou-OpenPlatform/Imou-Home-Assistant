"""Imou select entities."""

from __future__ import annotations

from typing import override

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pyimouapi.const import PARAM_CURRENT_OPTION, PARAM_OPTIONS
from pyimouapi.exceptions import ImouException
from pyimouapi.ha_device import ImouHaDevice

from .const import (
    DOMAIN,
    PARAM_COLLECTION_POINT,
    PARAM_DEVICE_VOLUME,
    PARAM_INSIGHT_WORKBENCH_DEVICE,
    PARAM_NIGHT_VISION_MODE,
    PARAM_WIDE_DYNAMIC,
)
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .entity import ImouEntity, async_add_imou_entities
from .insight_account import ImouAccountEntity, camera_select_options
from .runtime_data import get_runtime_data

PARALLEL_UPDATES = 1

# The collection point recalls a PTZ preset, which is an action on the camera;
# the rest set how the device behaves and belong under configuration.
SELECT_TYPES: tuple[SelectEntityDescription, ...] = (
    SelectEntityDescription(
        key=PARAM_DEVICE_VOLUME,
        translation_key=PARAM_DEVICE_VOLUME,
        entity_category=EntityCategory.CONFIG,
    ),
    SelectEntityDescription(
        key=PARAM_NIGHT_VISION_MODE,
        translation_key=PARAM_NIGHT_VISION_MODE,
        entity_category=EntityCategory.CONFIG,
    ),
    SelectEntityDescription(
        key=PARAM_WIDE_DYNAMIC,
        translation_key=PARAM_WIDE_DYNAMIC,
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
    async_add_entities(
        [ImouInsightWorkbenchSelect(entry.runtime_data.coordinator, entry)]
    )


class ImouInsightWorkbenchSelect(ImouAccountEntity, SelectEntity):
    """Pick which camera channel the AI Insight workbench uses."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: ImouDataUpdateCoordinator,
        config_entry: ImouConfigEntry,
    ) -> None:
        super().__init__(coordinator, config_entry, PARAM_INSIGHT_WORKBENCH_DEVICE)
        self._label_to_key: dict[str, str] = {}
        self._refresh_options()

    def _refresh_options(self) -> None:
        labels, mapping = camera_select_options(self.coordinator.devices)
        self._label_to_key = mapping
        self._attr_options = labels
        runtime = get_runtime_data(self._config_entry)
        if runtime is not None and runtime.workbench_device_key:
            for label, key in mapping.items():
                if key == runtime.workbench_device_key:
                    self._attr_current_option = label
                    return
        if labels:
            self._attr_current_option = labels[0]
            if runtime is not None:
                runtime.workbench_device_key = mapping[labels[0]]
        else:
            self._attr_current_option = None

    async def async_select_option(self, option: str) -> None:
        key = self._label_to_key.get(option)
        if key is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="insight_workbench_unknown_device",
            )
        self._attr_current_option = option
        runtime = get_runtime_data(self._config_entry)
        if runtime is not None:
            runtime.workbench_device_key = key
        self.async_write_ha_state()

    @property
    @override
    def available(self) -> bool:
        return bool(self._attr_options) and super().available


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
            self._raise_imou_ha_error(err, "select_option_failed")
        self.async_write_ha_state()
