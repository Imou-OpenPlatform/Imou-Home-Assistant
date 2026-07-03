"""Support for Imou devices."""

from __future__ import annotations

import logging
import uuid

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntry
from pyimouapi.device import ImouDeviceManager
from pyimouapi.ha_device import ImouHaDeviceManager
from pyimouapi.openapi import ImouOpenApiClient

from .const import (
    PARAM_API_URL,
    PARAM_APP_ID,
    PARAM_APP_SECRET,
    PARAM_ENABLE_EVENT_PUSH,
    PARAM_WEBHOOK_ID,
    PLATFORMS,
)
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .event_push import async_setup_event_push, async_teardown_event_push
from .runtime_data import ImouRuntimeData

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config entries to the current schema."""
    if entry.version >= 2:
        return True

    data = dict(entry.data)
    if not data.get(PARAM_WEBHOOK_ID):
        data[PARAM_WEBHOOK_ID] = uuid.uuid4().hex
        _LOGGER.debug("Added missing webhook_id to config entry %s", entry.entry_id)

    hass.config_entries.async_update_entry(entry, data=data, version=2)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ImouConfigEntry) -> bool:
    """Set up Imou Life from a config entry."""
    _LOGGER.debug("Setting up imou_life")
    imou_client = ImouOpenApiClient(
        entry.data[PARAM_APP_ID],
        entry.data[PARAM_APP_SECRET],
        entry.data[PARAM_API_URL],
    )
    device_manager = ImouDeviceManager(imou_client)
    imou_device_manager = ImouHaDeviceManager(device_manager)
    coordinator = ImouDataUpdateCoordinator(hass, imou_device_manager, entry)
    runtime = ImouRuntimeData(coordinator=coordinator)
    entry.runtime_data = runtime

    try:
        await async_setup_event_push(hass, entry, imou_client, runtime)
    except Exception:
        _LOGGER.exception(
            "Failed to set up event push (non-fatal, integration continues normally)"
        )

    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    @callback
    def _async_keep_polling() -> None:
        pass

    entry.async_on_unload(coordinator.async_add_listener(_async_keep_polling))
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ImouConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading entry %s", entry.entry_id)
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    webhook_id = entry.data.get(PARAM_WEBHOOK_ID, "")
    if entry.options.get(PARAM_ENABLE_EVENT_PUSH) and webhook_id:
        imou_client = ImouOpenApiClient(
            entry.data[PARAM_APP_ID],
            entry.data[PARAM_APP_SECRET],
            entry.data[PARAM_API_URL],
        )
        try:
            await async_teardown_event_push(hass, entry, imou_client)
        except Exception:
            _LOGGER.exception("Failed to disable Imou message callback during unload")
        finally:
            await imou_client.async_close()
    elif webhook_id:
        await async_teardown_event_push(hass, entry)

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    _LOGGER.debug("Reloading entry %s", entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Handle removal of a single device from a config entry."""
    _LOGGER.debug("Removing device %s", device_entry.name)
    dr.async_get(hass).async_remove_device(device_entry.id)
    return True
