"""Diagnostics support for Imou Life."""

from __future__ import annotations

from collections.abc import Mapping
from importlib.metadata import version
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry
from pyimouapi.const import PARAM_CURRENT_OPTION, PARAM_OPTIONS, PARAM_STATE
from pyimouapi.ha_device import ImouHaDevice

from .const import (
    BASE_PUSH_ALWAYS,
    DOMAIN,
    PARAM_API_URL,
    PARAM_APP_ID,
    PARAM_ATTACH_DECRYPTED_THUMBNAIL,
    PARAM_DEVICE_PASSWORDS,
    PARAM_ENABLE_EVENT_PUSH,
    PARAM_EVENT_PUSH_TYPES,
    PARAM_STATUS,
    PARAM_WEBHOOK_ID,
    PARAM_WEBHOOK_URL,
    imou_life_device_key,
)
from .helpers import get_selected_device_ids
from .pic_thumbnail import native_libs_present
from .runtime_data import get_runtime_data

# Resolved at import time: reading package metadata touches the filesystem and
# must not run inside the event loop.
_PYIMOUAPI_VERSION = version("pyimouapi")


def _redact_id(value: str, keep: int) -> str:
    """Return a partially redacted identifier for diagnostics."""
    return f"{value[:keep]}…" if len(value) > keep else value


def _device_password_serials(options: Mapping[str, Any]) -> list[str]:
    """Return device serials with stored passwords (values never included)."""
    passwords = options.get(PARAM_DEVICE_PASSWORDS)
    if not isinstance(passwords, dict):
        return []
    return sorted(key for key in passwords if key)


def _entity_state_summary(entities: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Summarise entity bags without dumping raw API payloads."""
    summary: dict[str, Any] = {}
    for key, data in entities.items():
        if PARAM_STATE in data:
            summary[key] = data[PARAM_STATE]
        elif PARAM_CURRENT_OPTION in data:
            summary[key] = {
                "current_option": data.get(PARAM_CURRENT_OPTION),
                "options": data.get(PARAM_OPTIONS),
            }
        else:
            summary[key] = sorted(data.keys())
    return summary


def _device_diagnostics_payload(
    device: ImouHaDevice,
    *,
    selected: bool | None,
    present: bool,
) -> dict[str, Any]:
    """Build the diagnostics dict for one Imou device."""
    status = None
    if PARAM_STATUS in device.sensors:
        status = device.sensors[PARAM_STATUS].get(PARAM_STATE)
    return {
        "device_id": device.device_id,
        "device_name": device.device_name,
        "channel_id": device.channel_id,
        "channel_name": device.channel_name,
        "product_id": device.product_id,
        "parent_device_id": device.parent_device_id,
        "parent_product_id": device.parent_product_id,
        "manufacturer": device.manufacturer,
        "model": device.model,
        "sw_version": device.swversion,
        "is_ipc": device.is_ipc,
        "device_key": imou_life_device_key(device),
        "status": status,
        "selected": selected,
        "present_in_coordinator": present,
        "entities": {
            "switches": _entity_state_summary(device.switches),
            "sensors": _entity_state_summary(device.sensors),
            "binary_sensors": _entity_state_summary(device.binary_sensors),
            "selects": _entity_state_summary(device.selects),
            "buttons": sorted(device.buttons.keys()),
            "texts": _entity_state_summary(device.texts),
            "alarm_control_panel": device.alarm_control_panel,
        },
    }


def _device_key_from_entry(device_entry: DeviceEntry) -> str | None:
    """Return the Imou device_key stored in the HA device registry."""
    for domain, ident in device_entry.identifiers:
        if domain == DOMAIN:
            return ident
    return None


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    app_id = entry.data.get(PARAM_APP_ID, "")
    webhook_id = entry.data.get(PARAM_WEBHOOK_ID, "")
    webhook_url = entry.options.get(PARAM_WEBHOOK_URL, "")
    selected = get_selected_device_ids(entry)
    selected_count = None if selected is None else len(selected)

    runtime = get_runtime_data(entry)
    coordinator = runtime.coordinator if runtime is not None else None
    last_update_success = (
        coordinator.last_update_success if coordinator is not None else None
    )
    devices_by_key = coordinator.devices_by_key if coordinator is not None else {}

    last_received = (
        runtime.push_last_received_at.isoformat()
        if runtime is not None and runtime.push_last_received_at is not None
        else None
    )

    event_push = {
        "enabled": bool(entry.options.get(PARAM_ENABLE_EVENT_PUSH)),
        "webhook_url_configured": bool(webhook_url),
        "event_push_types": list(entry.options.get(PARAM_EVENT_PUSH_TYPES, [])),
        "base_push": BASE_PUSH_ALWAYS,
        "selected_devices_count": selected_count,
        "recent_msg_type_counts": (
            dict(runtime.push_msg_type_counts) if runtime is not None else {}
        ),
        "last_msg_type": (runtime.push_last_msg_type if runtime is not None else None),
        "last_received_at": last_received,
    }

    return {
        "app_id": _redact_id(app_id, 4),
        "api_url": entry.data.get(PARAM_API_URL),
        "selected_devices_count": selected_count,
        "coordinator_device_count": len(devices_by_key),
        "event_push_enabled": bool(entry.options.get(PARAM_ENABLE_EVENT_PUSH)),
        "webhook_id": _redact_id(webhook_id, 8),
        "webhook_url_configured": bool(webhook_url),
        "last_update_success": last_update_success,
        "pyimouapi_version": _PYIMOUAPI_VERSION,
        "attach_decrypted_thumbnail": bool(
            entry.options.get(PARAM_ATTACH_DECRYPTED_THUMBNAIL)
        ),
        "native_libs_present": native_libs_present(hass),
        "device_password_serials": _device_password_serials(entry.options),
        "event_push": event_push,
    }


async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry, device_entry: DeviceEntry
) -> dict[str, Any]:
    """Return diagnostics for a single Home Assistant device."""
    device_key = _device_key_from_entry(device_entry)
    runtime = get_runtime_data(entry)
    coordinator = runtime.coordinator if runtime is not None else None
    devices_by_key = coordinator.devices_by_key if coordinator is not None else {}
    device = devices_by_key.get(device_key) if device_key else None

    selected_ids = get_selected_device_ids(entry)
    if device is not None:
        selected = None if selected_ids is None else device.device_id in selected_ids
        return _device_diagnostics_payload(device, selected=selected, present=True)

    # Ghost device: still on the registry but no longer in the coordinator.
    serial = device_entry.serial_number
    selected = None
    if selected_ids is not None and serial is not None:
        selected = serial in selected_ids
    return {
        "device_key": device_key,
        "device_id": serial,
        "name": device_entry.name_by_user or device_entry.name,
        "model": device_entry.model,
        "manufacturer": device_entry.manufacturer,
        "sw_version": device_entry.sw_version,
        "present_in_coordinator": False,
        "selected": selected,
    }
