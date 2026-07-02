"""Provides the Imou DataUpdateCoordinator."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pyimouapi.exceptions import ImouException
from pyimouapi.ha_device import ImouHaDevice, ImouHaDeviceManager

from .const import (
    DOMAIN,
    PARAM_SELECTED_DEVICES,
    PARAM_UPDATE_INTERVAL,
    UPDATE_TIMEOUT,
    imou_life_device_key,
)
from .runtime_data import ImouRuntimeData

_LOGGER = logging.getLogger(__name__)


class ImouDataUpdateCoordinator(DataUpdateCoordinator[None]):
    """Coordinates polling Imou device status from the cloud."""

    def __init__(
        self,
        hass: HomeAssistant,
        device_manager: ImouHaDeviceManager,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize ImouDataUpdateCoordinator."""
        update_interval = config_entry.options.get(PARAM_UPDATE_INTERVAL, 60)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=update_interval),
            always_update=True,
        )
        self._device_manager = device_manager
        self.devices_by_key: dict[str, ImouHaDevice] = {}
        self._devices_initialized = False
        self.new_device_callbacks: list[Callable[[list[ImouHaDevice]], None]] = []

    @property
    def devices(self) -> list[ImouHaDevice]:
        """Devices discovered for this config entry."""
        return list(self.devices_by_key.values())

    @property
    def device_manager(self) -> ImouHaDeviceManager:
        """Underlying pyimouapi device manager."""
        return self._device_manager

    def get_device(self, device_key: str) -> ImouHaDevice | None:
        """Return the current device for device_key, if still on the account."""
        return self.devices_by_key.get(device_key)

    def _filter_devices(self, devices_list: list[ImouHaDevice]) -> list[ImouHaDevice]:
        """Apply selected-devices filter from config entry options or data."""
        if PARAM_SELECTED_DEVICES in self.config_entry.options:
            selected_ids = self.config_entry.options[PARAM_SELECTED_DEVICES]
        elif PARAM_SELECTED_DEVICES in self.config_entry.data:
            selected_ids = self.config_entry.data[PARAM_SELECTED_DEVICES]
        else:
            selected_ids = None

        if selected_ids is None:
            return devices_list
        if selected_ids:
            selected_set = set(selected_ids)
            filtered = [d for d in devices_list if d.device_id in selected_set]
            _LOGGER.info(
                "Device filter active: %d/%d devices selected for polling",
                len(filtered),
                len(devices_list),
            )
            return filtered
        return []

    async def _async_update_data(self) -> None:
        """Fetch latest device status from Imou cloud."""
        _LOGGER.debug("Polling Imou device status")
        try:
            async with asyncio.timeout(UPDATE_TIMEOUT):
                fresh_devices = await self._device_manager.async_get_devices()
        except TimeoutError as err:
            raise UpdateFailed(f"Timeout while fetching data: {err}") from err
        except ImouException as err:
            raise UpdateFailed(f"Error fetching Imou devices: {err}") from err

        filtered_list = self._filter_devices(fresh_devices)
        fresh_by_key = {imou_life_device_key(d): d for d in filtered_list}
        self._async_add_remove_devices(fresh_by_key)

        devices_to_update = [
            device
            for device in self.devices_by_key.values()
            if not self._should_skip_device_update(device)
        ]
        if len(devices_to_update) < len(self.devices_by_key):
            _LOGGER.debug(
                "Skipping cloud poll for %s device(s) with all entities disabled",
                len(self.devices_by_key) - len(devices_to_update),
            )
        if not devices_to_update:
            return

        try:
            async with asyncio.timeout(UPDATE_TIMEOUT):
                results = await asyncio.gather(
                    *(
                        self._device_manager.async_update_device_status(device)
                        for device in devices_to_update
                    ),
                    return_exceptions=True,
                )
        except TimeoutError as err:
            raise UpdateFailed(f"Timeout while fetching data: {err}") from err

        failures: list[Exception] = []
        for device, result in zip(devices_to_update, results, strict=True):
            if isinstance(result, BaseException) and not isinstance(result, Exception):
                raise result
            if not isinstance(result, Exception):
                continue
            device_key = imou_life_device_key(device)
            _LOGGER.warning(
                "Error updating status for Imou device %s: %s",
                device_key,
                result,
            )
            failures.append(result)
        if failures and len(failures) == len(devices_to_update):
            raise UpdateFailed(
                f"Error updating Imou devices: {failures[0]}"
            ) from failures[0]

    def _async_add_remove_devices(self, fresh_by_key: dict[str, ImouHaDevice]) -> None:
        """Add new devices and remove devices no longer in the account."""
        if not self._devices_initialized:
            self.devices_by_key = fresh_by_key
            self._devices_initialized = True
            return

        current_keys = set(fresh_by_key)
        known_keys = set(self.devices_by_key)

        if current_keys == known_keys:
            return

        if removed_keys := known_keys - current_keys:
            _LOGGER.debug("Removed Imou device(s): %s", ", ".join(removed_keys))
            device_registry = dr.async_get(self.hass)
            for device_key in removed_keys:
                del self.devices_by_key[device_key]
                if device := device_registry.async_get_device(
                    identifiers={(DOMAIN, device_key)}
                ):
                    device_registry.async_update_device(
                        device_id=device.id,
                        remove_config_entry_id=self.config_entry.entry_id,
                    )

        if new_keys := current_keys - known_keys:
            _LOGGER.debug("New Imou device(s) found: %s", ", ".join(new_keys))
            new_devices = []
            for device_key in new_keys:
                self.devices_by_key[device_key] = fresh_by_key[device_key]
                new_devices.append(fresh_by_key[device_key])
            for callback in self.new_device_callbacks:
                callback(new_devices)

    def _should_skip_device_update(self, device: ImouHaDevice) -> bool:
        """Skip cloud status poll when every HA entity for this device is disabled."""
        if self.config_entry is None:
            return False
        entry_id = self.config_entry.entry_id
        device_registry = dr.async_get(self.hass)
        entity_registry = er.async_get(self.hass)
        device_key = imou_life_device_key(device)
        device_entry = device_registry.async_get_device({(DOMAIN, device_key)})
        if device_entry is None:
            return False
        entries = [
            e
            for e in er.async_entries_for_device(
                entity_registry,
                device_entry.id,
                include_disabled_entities=True,
            )
            if e.config_entry_id == entry_id
        ]
        if not entries:
            return False
        return all(e.disabled_by is not None for e in entries)


type ImouConfigEntry = ConfigEntry[ImouRuntimeData]
