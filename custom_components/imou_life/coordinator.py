"""Provides the Imou DataUpdateCoordinator."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import TypeAlias

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pyimouapi.ha_device import ImouHaDevice, ImouHaDeviceManager

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class ImouDataUpdateCoordinator(DataUpdateCoordinator[bool]):
    """Coordinates polling Imou device status from the cloud."""

    def __init__(
        self,
        hass: HomeAssistant,
        device_manager: ImouHaDeviceManager,
        update_interval: int,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize ImouDataUpdateCoordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=update_interval),
            always_update=True,
        )
        self._device_manager = device_manager
        self._devices: list[ImouHaDevice] = []

    @property
    def devices(self) -> list[ImouHaDevice]:
        """Devices discovered for this config entry."""
        return self._devices

    @property
    def device_manager(self) -> ImouHaDeviceManager:
        """Underlying pyimouapi device manager."""
        return self._device_manager

    async def _async_setup(self) -> None:
        """Set up the coordinator.

        This is the place to set up your coordinator,
        or to load data, that only needs to be loaded once.

        This method will be called automatically during
        coordinator.async_config_entry_first_refresh.
        """
        devices_list = await self._device_manager.async_get_devices()
        self._devices.extend(devices_list)

    def _should_skip_device_update(self, device: ImouHaDevice) -> bool:
        """Skip cloud status poll when every HA entity for this device is disabled."""
        if self.config_entry is None:
            return False
        entry_id = self.config_entry.entry_id
        device_registry = dr.async_get(self.hass)
        entity_registry = er.async_get(self.hass)
        unique = f"{device.device_id}_{device.channel_id or device.product_id}"
        device_entry = device_registry.async_get_device({(DOMAIN, unique)})
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

    async def async_update_all_device(self) -> bool:
        """Update all device."""
        to_update = [d for d in self._devices if not self._should_skip_device_update(d)]
        if len(to_update) < len(self._devices):
            _LOGGER.debug(
                "Skipping cloud poll for %s device(s) with all entities disabled",
                len(self._devices) - len(to_update),
            )
        await asyncio.gather(
            *[
                self._device_manager.async_update_device_status(device)
                for device in to_update
            ],
            return_exceptions=True,
        )
        return True

    async def _async_update_data(self) -> bool:
        """Fetch latest device status from Imou cloud."""
        _LOGGER.debug("Polling Imou device status")
        async with asyncio.timeout(300):
            try:
                return await self.async_update_all_device()
            except Exception as err:
                _LOGGER.exception("Unexpected error updating Imou devices")
                raise UpdateFailed from err


ImouConfigEntry: TypeAlias = ConfigEntry[ImouDataUpdateCoordinator]
