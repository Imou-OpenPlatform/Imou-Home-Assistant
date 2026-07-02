"""Base entity for Imou Life."""

from __future__ import annotations

from typing import Any, override

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pyimouapi.const import PARAM_STATE
from pyimouapi.ha_device import DeviceStatus, ImouHaDevice

from .const import DOMAIN, PARAM_STATUS, imou_life_device_key
from .coordinator import ImouDataUpdateCoordinator


class ImouEntity(CoordinatorEntity[ImouDataUpdateCoordinator]):
    """Base class for Imou entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ImouDataUpdateCoordinator,
        config_entry: ConfigEntry,
        entity_type: str,
        device: ImouHaDevice,
    ) -> None:
        """Initialize ImouEntity."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._entity_type = entity_type
        self._device_key = imou_life_device_key(device)
        self._attr_unique_id = f"{self._device_key}${entity_type}"
        self._attr_translation_key = entity_type
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._device_key)},
            name=device.channel_name or device.device_name,
            manufacturer=device.manufacturer,
            model=device.model,
            sw_version=device.swversion,
            serial_number=device.device_id,
        )

    @property
    def device(self) -> ImouHaDevice:
        """Return the live device from the coordinator."""
        return self.coordinator.devices_by_key[self._device_key]

    @property
    @override
    def available(self) -> bool:
        """Return True if entity is available."""
        if not super().available:
            return False
        if self._device_key not in self.coordinator.devices_by_key:
            return False
        if self._entity_type == PARAM_STATUS:
            return True
        if PARAM_STATUS not in self.device.sensors:
            return False
        return (
            self.device.sensors[PARAM_STATUS][PARAM_STATE] != DeviceStatus.OFFLINE.value
        )

    @staticmethod
    def is_non_negative_number(value: Any) -> bool:
        """Return True if value parses as a non-negative number."""
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False
        return number >= 0
