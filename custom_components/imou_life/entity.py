"""Base entity for Imou Life."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, override

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pyimouapi.const import PARAM_STATE
from pyimouapi.ha_device import DeviceStatus, ImouHaDevice

from .const import DOMAIN, PARAM_STATUS, imou_life_device_key
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator


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
        self._last_known_device = device
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
        """Return the live device from the coordinator.

        A device dropped from the account leaves its entities behind until the
        registry catches up, and HA reads capability attributes before it reads
        `available`, so raising here would break the update for every entity
        after this one. The last known device keeps those reads answerable;
        `available` is what reports the device as gone.
        """
        device = self.coordinator.devices_by_key.get(self._device_key)
        if device is not None:
            self._last_known_device = device
        return self._last_known_device

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


@callback
def async_add_imou_entities(
    entry: ImouConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    entity_class: type[ImouEntity],
    iter_pairs: Callable[
        [ImouDataUpdateCoordinator], Iterable[tuple[Any, ImouHaDevice]]
    ],
) -> None:
    """Add entities for the known devices and for any discovered later.

    ``iter_pairs`` yields (entity type or description, device) for everything the
    platform supports; entities are then built only for the devices the
    coordinator has just reported, so a later discovery does not re-add the ones
    already present.
    """
    coordinator = entry.runtime_data.coordinator

    def _async_add(new_devices: list[ImouHaDevice]) -> None:
        device_keys = {imou_life_device_key(device) for device in new_devices}
        async_add_entities(
            entity_class(coordinator, entry, entity_key, device)
            for entity_key, device in iter_pairs(coordinator)
            if imou_life_device_key(device) in device_keys
        )

    coordinator.new_device_callbacks.append(_async_add)

    @callback
    def _remove_new_device_callback() -> None:
        if _async_add in coordinator.new_device_callbacks:
            coordinator.new_device_callbacks.remove(_async_add)

    entry.async_on_unload(_remove_new_device_callback)
    _async_add(coordinator.devices)
