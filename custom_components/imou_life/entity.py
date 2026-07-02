"""Base entity for Imou Life."""

from __future__ import annotations

from typing import Any

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
        self._coordinator = coordinator
        self._config_entry = config_entry
        self._entity_type = entity_type
        self._device = device

    @property
    def device_info(self) -> DeviceInfo:
        """Return the device info."""
        device_key = imou_life_device_key(self._device)
        return DeviceInfo(
            identifiers={(DOMAIN, device_key)},
            name=self._device.channel_name or self._device.device_name,
            manufacturer=self._device.manufacturer,
            model=self._device.model,
            sw_version=self._device.swversion,
            serial_number=self._device.device_id,
        )

    @property
    def unique_id(self) -> str:
        """Return a unique ID for this entity."""
        device_key = imou_life_device_key(self._device)
        return f"{device_key}${self._entity_type}"

    @property
    def translation_key(self) -> str | None:
        """Return the translation key."""
        return self._entity_type

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        if self._entity_type == PARAM_STATUS:
            return True
        return (
            self._device.sensors[PARAM_STATUS][PARAM_STATE]
            != DeviceStatus.OFFLINE.value
        )

    @staticmethod
    def is_non_negative_number(value: Any) -> bool:
        """Return True if value parses as a non-negative number."""
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False
        return number >= 0
