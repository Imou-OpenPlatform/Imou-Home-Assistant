"""Support for Imou devices."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntry
from pyimouapi.device import ImouDeviceManager
from pyimouapi.ha_device import ImouHaDeviceManager
from pyimouapi.openapi import ImouOpenApiClient

from .const import (
    DOMAIN,
    PARAM_API_URL,
    PARAM_APP_ID,
    PARAM_APP_SECRET,
    PARAM_UPDATE_INTERVAL,
    PLATFORMS,
)
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def async_setup_entry(hass: HomeAssistant, entry: ImouConfigEntry) -> bool:
    """Set up Imou Life from a config entry."""
    _LOGGER.debug("Setting up %s", DOMAIN)
    imou_client = ImouOpenApiClient(
        entry.data[PARAM_APP_ID],
        entry.data[PARAM_APP_SECRET],
        entry.data[PARAM_API_URL],
    )
    device_manager = ImouDeviceManager(imou_client)
    imou_device_manager = ImouHaDeviceManager(device_manager)
    coordinator = ImouDataUpdateCoordinator(
        hass,
        imou_device_manager,
        entry.options.get(PARAM_UPDATE_INTERVAL, 60),
        entry,
    )
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ImouConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading entry %s", entry.entry_id)
    results = await asyncio.gather(
        *[
            hass.config_entries.async_forward_entry_unload(entry, platform)
            for platform in PLATFORMS
        ]
    )
    if not all(results):
        return False

    _remove_devices_for_config_entry(hass, entry.entry_id)
    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    _LOGGER.debug("Reloading entry %s", entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)


def _remove_devices_for_config_entry(hass: HomeAssistant, config_entry_id: str) -> None:
    """Remove device registry entries tied to this config entry."""
    device_registry = dr.async_get(hass)
    for device_entry in device_registry.devices.get_devices_for_config_entry_id(
        config_entry_id
    ):
        _LOGGER.debug("Removing device %s", device_entry.name)
        device_registry.async_remove_device(device_entry.id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Handle removal of a single device from a config entry."""
    _LOGGER.debug("Removing device %s", device_entry.name)
    dr.async_get(hass).async_remove_device(device_entry.id)
    return True
