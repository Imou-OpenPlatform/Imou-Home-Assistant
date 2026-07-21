"""Diagnostics support for Imou Life."""

from __future__ import annotations

from importlib.metadata import version
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    DEFAULT_BASE_PUSH,
    PARAM_API_URL,
    PARAM_APP_ID,
    PARAM_BASE_PUSH,
    PARAM_ENABLE_EVENT_PUSH,
    PARAM_EVENT_PUSH_TYPES,
    PARAM_SELECTED_DEVICES,
    PARAM_WEBHOOK_ID,
    PARAM_WEBHOOK_URL,
)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    app_id = entry.data.get(PARAM_APP_ID, "")
    webhook_id = entry.data.get(PARAM_WEBHOOK_ID, "")
    webhook_url = entry.options.get(PARAM_WEBHOOK_URL, "")
    selected = entry.options.get(PARAM_SELECTED_DEVICES) or entry.data.get(
        PARAM_SELECTED_DEVICES, []
    )

    runtime = entry.runtime_data
    coordinator = runtime.coordinator if runtime is not None else None
    last_update_success = (
        coordinator.last_update_success if coordinator is not None else None
    )

    last_received = (
        runtime.push_last_received_at.isoformat()
        if runtime is not None and runtime.push_last_received_at is not None
        else None
    )

    event_push = {
        "enabled": bool(entry.options.get(PARAM_ENABLE_EVENT_PUSH)),
        "webhook_url_configured": bool(webhook_url),
        "event_push_types": list(entry.options.get(PARAM_EVENT_PUSH_TYPES, [])),
        "base_push": entry.options.get(PARAM_BASE_PUSH, DEFAULT_BASE_PUSH),
        "selected_devices_count": len(selected),
        "recent_msg_type_counts": (
            dict(runtime.push_msg_type_counts) if runtime is not None else {}
        ),
        "last_msg_type": (
            runtime.push_last_msg_type if runtime is not None else None
        ),
        "last_received_at": last_received,
    }

    return {
        "app_id": f"{app_id[:4]}…" if len(app_id) > 4 else app_id,
        "api_url": entry.data.get(PARAM_API_URL),
        "selected_devices_count": len(selected),
        "event_push_enabled": bool(entry.options.get(PARAM_ENABLE_EVENT_PUSH)),
        "webhook_id": f"{webhook_id[:8]}…" if len(webhook_id) > 8 else webhook_id,
        "webhook_url_configured": bool(webhook_url),
        "last_update_success": last_update_success,
        "pyimouapi_version": version("pyimouapi"),
        "event_push": event_push,
    }
