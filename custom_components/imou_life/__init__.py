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
    PARAM_BASE_PUSH,
    PARAM_ENABLE_EVENT_PUSH,
    PARAM_EVENT_PUSH_TYPES,
    PARAM_NOTIFY_SERVICES,
    PARAM_SELECTED_DEVICES,
    PARAM_UPDATE_INTERVAL,
    PARAM_WEBHOOK_ID,
    PARAM_WEBHOOK_URL,
    PLATFORMS,
)
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .webhook import async_register_imou_webhook, async_unregister_imou_webhook

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

    # --- Event push / webhook setup (optional, never blocks normal startup) ---
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["notify_services"] = []
    hass.data[DOMAIN]["push_enabled"] = bool(entry.options.get(PARAM_ENABLE_EVENT_PUSH))
    hass.data[DOMAIN]["selected_devices"] = entry.options.get(
        PARAM_SELECTED_DEVICES
    ) or entry.data.get(PARAM_SELECTED_DEVICES, [])
    try:
        webhook_id = entry.data.get(PARAM_WEBHOOK_ID, "")
        if not webhook_id:
            # No webhook_id in entry.data: generate one for upgrade users
            webhook_id = uuid.uuid4().hex
            entry.data[PARAM_WEBHOOK_ID] = webhook_id
            await hass.config_entries.async_update_entry(entry, data=entry.data)
        generated_url = async_register_imou_webhook(hass, webhook_id)
        if entry.options.get(PARAM_ENABLE_EVENT_PUSH):
            await _async_set_message_callback(
                entry, imou_client, "on", generated_url
            )

        # Store notify services list for the webhook handler to use
        raw_services = entry.options.get(PARAM_NOTIFY_SERVICES, "")
        if raw_services:
            hass.data[DOMAIN]["notify_services"] = [
                s.strip() for s in raw_services.split(",") if s.strip()
            ]
    except Exception:
        _LOGGER.exception(
            "Failed to set up event push (non-fatal, integration continues normally)"
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

    # --- Disable callback + unregister webhook ---
    webhook_id = entry.data.get(PARAM_WEBHOOK_ID, "")
    if entry.options.get(PARAM_ENABLE_EVENT_PUSH) and webhook_id:
        imou_client = ImouOpenApiClient(
            entry.data[PARAM_APP_ID],
            entry.data[PARAM_APP_SECRET],
            entry.data[PARAM_API_URL],
        )
        try:
            await _async_set_message_callback(entry, imou_client, "off")
        except Exception:
            _LOGGER.exception("Failed to disable Imou message callback during unload")
        finally:
            await imou_client.async_close()
    if webhook_id:
        async_unregister_imou_webhook(hass, webhook_id)

    hass.data.pop(DOMAIN, None)
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


async def _async_set_message_callback(
    entry: ImouConfigEntry,
    imou_client: ImouOpenApiClient,
    status: str,
    generated_webhook_url: str | None = None,
) -> None:
    """Register or unregister Imou Open Platform message callback."""
    callback_url = entry.options.get(PARAM_WEBHOOK_URL) or generated_webhook_url
    callback_flags = entry.options.get(PARAM_EVENT_PUSH_TYPES, [])
    params: dict[str, str] = {
        "status": status,
        "basePush": entry.options.get(PARAM_BASE_PUSH, "2"),
    }
    if status == "on":
        if not callback_url:
            _LOGGER.error(
                "Cannot enable Imou event push: no webhook URL available. "
                "Please set webhook_url in integration options or configure HA "
                "external URL."
            )
            return
        params["callbackUrl"] = callback_url
        params["callbackFlag"] = (
            ",".join(callback_flags) if callback_flags else "alarm,deviceStatus"
        )
    try:
        await imou_client.async_request_api("/openapi/setMessageCallback", params)
        _LOGGER.info(
            "Imou message callback set to %s (url=%s)",
            status,
            callback_url or "N/A",
        )
    except Exception:
        _LOGGER.exception("Failed to set Imou message callback")


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Handle removal of a single device from a config entry."""
    _LOGGER.debug("Removing device %s", device_entry.name)
    dr.async_get(hass).async_remove_device(device_entry.id)
    return True
