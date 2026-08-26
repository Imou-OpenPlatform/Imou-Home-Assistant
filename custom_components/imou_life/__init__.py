"""Support for Imou devices."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntry
from pyimouapi.device import ImouDeviceManager
from pyimouapi.ha_device import ImouHaDeviceManager
from pyimouapi.openapi import ImouOpenApiClient

from .const import (
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    PARAM_API_URL,
    PARAM_APP_ID,
    PARAM_APP_SECRET,
    PARAM_ATTACH_DECRYPTED_THUMBNAIL,
    PARAM_ENABLE_EVENT_PUSH,
    PARAM_ENABLE_POLLING,
    PARAM_EVENT_PUSH_TYPES,
    PARAM_NOTIFY_SERVICES,
    PARAM_SELECTED_DEVICES,
    PARAM_UPDATE_INTERVAL,
    PARAM_WEBHOOK_ID,
    PARAM_WEBHOOK_URL,
    PLATFORMS,
)
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .event_push import async_setup_event_push, async_teardown_event_push
from .helpers import get_selected_device_ids, parse_notify_services
from .runtime_data import ImouRuntimeData, get_runtime_data

_LOGGER: logging.Logger = logging.getLogger(__package__)

_UNSET = object()

_REPLACED_BUTTON_SUFFIXES = ("$siren_start", "$siren_stop")


def async_remove_replaced_legacy_entities(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Drop leftover select.mode and siren button rows from 1.3.x upgrades."""
    registry = er.async_get(hass)
    for entity_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        unique_id = entity_entry.unique_id
        drop_select = entity_entry.domain == "select" and unique_id.endswith("$mode")
        drop_button = entity_entry.domain == "button" and unique_id.endswith(
            _REPLACED_BUTTON_SUFFIXES
        )
        if drop_select or drop_button:
            registry.async_remove(entity_entry.entity_id)


def options_reload_signature(options: Mapping[str, Any]) -> tuple[object, ...]:
    """Return the subset of options that require unload and setup to apply.

    Everything else (decrypt switch and passwords, notify targets, camera
    defaults, local-record path) is read from ``entry.options`` on use, or
    refreshed on the live runtime, so changing it must not cost a full
    ``setMessageCallback`` off/on cycle and a rediscovery.
    """
    selected = options.get(PARAM_SELECTED_DEVICES, _UNSET)
    if selected is _UNSET:
        selected_sig: object = None
    elif isinstance(selected, list):
        # Order must not force a reload when the same set was re-saved.
        selected_sig = tuple(sorted(str(item) for item in selected))
    else:
        selected_sig = selected
    event_types = options.get(PARAM_EVENT_PUSH_TYPES) or []
    return (
        bool(options.get(PARAM_ENABLE_POLLING, True)),
        int(options.get(PARAM_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)),
        selected_sig,
        bool(options.get(PARAM_ENABLE_EVENT_PUSH)),
        str(options.get(PARAM_WEBHOOK_URL) or ""),
        tuple(event_types),
    )


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
    # Registered before anything can fail so a retried setup never leaks a session.
    entry.async_on_unload(imou_client.async_close)
    device_manager = ImouDeviceManager(imou_client)
    imou_device_manager = ImouHaDeviceManager(device_manager)
    coordinator = ImouDataUpdateCoordinator(hass, imou_device_manager, entry)
    runtime = ImouRuntimeData(coordinator=coordinator, client=imou_client)
    runtime.reload_signature = options_reload_signature(entry.options)
    entry.runtime_data = runtime

    try:
        await async_setup_event_push(hass, entry, imou_client, runtime)
    except Exception:
        _LOGGER.exception(
            "Failed to set up event push (non-fatal, integration continues normally)"
        )

    await coordinator.async_config_entry_first_refresh()
    async_remove_replaced_legacy_entities(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    @callback
    def _async_keep_polling() -> None:
        pass

    entry.async_on_unload(coordinator.async_add_listener(_async_keep_polling))
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ImouConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading entry %s", entry.entry_id)
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    webhook_id = entry.data.get(PARAM_WEBHOOK_ID, "")
    if not webhook_id:
        return True

    # Turning event push off in the options saves them before the reload, so
    # entry.options already says "off" by the time we get here. What has to be
    # switched off in the cloud is what setup actually turned on, which is the
    # only thing the runtime records.
    runtime = get_runtime_data(entry)
    if runtime is None or not runtime.push_enabled:
        await async_teardown_event_push(hass, entry)
        return True

    # Reuse the setup client so its accessToken is still valid; a fresh client
    # would have to fetch a token before it could disable the callback.
    client = runtime.client
    spare_client = None
    if client is None:
        spare_client = client = ImouOpenApiClient(
            entry.data[PARAM_APP_ID],
            entry.data[PARAM_APP_SECRET],
            entry.data[PARAM_API_URL],
        )
    try:
        await async_teardown_event_push(hass, entry, client)
    except Exception:
        _LOGGER.exception("Failed to disable Imou message callback during unload")
    finally:
        if spare_client is not None:
            await spare_client.async_close()

    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply option changes, reloading only when setup-time settings change."""
    runtime = get_runtime_data(entry)
    new_signature = options_reload_signature(entry.options)
    if runtime is not None and runtime.reload_signature == new_signature:
        runtime.notify_services = parse_notify_services(
            entry.options.get(PARAM_NOTIFY_SERVICES)
        )
        runtime.selected_devices = get_selected_device_ids(entry)
        # Soft path never rebuilds the decoder. Clearing the sticky failure
        # flag lets a later alarm retry after the user drops the .so files in.
        if entry.options.get(PARAM_ATTACH_DECRYPTED_THUMBNAIL):
            runtime.pic_decoder_failed = False
            runtime.pic_decoder = None
        _LOGGER.debug(
            "Applied soft option changes for entry %s without reload", entry.entry_id
        )
        return

    _LOGGER.debug("Reloading entry %s after option changes", entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    _LOGGER.debug("Reloading entry %s", entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)


def _device_id_from_entry(device_entry: DeviceEntry) -> str | None:
    """Extract Imou device_id from a HA device registry entry."""
    if device_entry.serial_number:
        return device_entry.serial_number
    for domain, ident in device_entry.identifiers:
        if domain == DOMAIN:
            return ident.split("_", 1)[0]
    return None


def _sibling_channel_names(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    device_entry: DeviceEntry,
    device_id: str,
) -> list[str]:
    """Return the other Home Assistant devices sharing one Imou device id.

    An NVR, and a camera with more than one lens, is reported by the account
    as one device carrying several channels, and each channel becomes its own
    device here.
    """
    device_registry = dr.async_get(hass)
    return sorted(
        entry.name_by_user or entry.name or entry.id
        for entry in dr.async_entries_for_config_entry(
            device_registry, config_entry.entry_id
        )
        if entry.id != device_entry.id and _device_id_from_entry(entry) == device_id
    )


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Persist exclusion so the next poll does not re-add the device."""
    device_name = device_entry.name_by_user or device_entry.name or device_entry.id
    device_id = _device_id_from_entry(device_entry)
    if not device_id:
        raise HomeAssistantError(
            f"Cannot remove device {device_name}: missing Imou device id",
            translation_domain=DOMAIN,
            translation_key="remove_device_missing_id",
            translation_placeholders={"name": device_name},
        )

    # Exclusion is recorded per account device, which is the granularity the
    # cloud and the push messages use. One channel of a multi-channel device
    # cannot be expressed in it, and removing the whole device id here would
    # take its sibling channels out of Home Assistant along with whatever the
    # user had named or automated on them.
    if siblings := _sibling_channel_names(hass, config_entry, device_entry, device_id):
        siblings_text = ", ".join(siblings)
        raise HomeAssistantError(
            f"Cannot remove {device_name}: it is one channel of Imou device "
            f"{device_id}, which also covers {siblings_text}. Deselect the whole "
            "device in the integration options to stop polling it, or disable "
            "this channel's entities to hide it",
            translation_domain=DOMAIN,
            translation_key="remove_device_has_siblings",
            translation_placeholders={
                "name": device_name,
                "device_id": device_id,
                "siblings": siblings_text,
            },
        )

    selected = get_selected_device_ids(config_entry)
    if selected is None:
        runtime = get_runtime_data(config_entry)
        if runtime is None:
            raise HomeAssistantError(
                f"Cannot remove device {device_name}: integration is not loaded",
                translation_domain=DOMAIN,
                translation_key="remove_device_no_runtime",
                translation_placeholders={"name": device_name},
            )
        all_ids = {d.device_id for d in runtime.coordinator.devices_by_key.values()}
        # Already absent from the live map (deleted in the Imou app, or never
        # discovered this session): nothing to exclude — allow the registry drop.
        if device_id not in all_ids:
            return True
        selected = [i for i in sorted(all_ids) if i != device_id]
    else:
        selected = [i for i in selected if i != device_id]

    hass.config_entries.async_update_entry(
        config_entry,
        options={**config_entry.options, PARAM_SELECTED_DEVICES: selected},
    )
    runtime = get_runtime_data(config_entry)
    if runtime is not None:
        runtime.selected_devices = selected

    _LOGGER.debug("Removed device %s from selected_devices", device_id)
    return True
