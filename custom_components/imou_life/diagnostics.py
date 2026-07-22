"""Diagnostics support for Imou Life."""

from __future__ import annotations

from importlib.metadata import version
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    BASE_PUSH_ALWAYS,
    PARAM_API_URL,
    PARAM_APP_ID,
    PARAM_ENABLE_EVENT_PUSH,
    PARAM_EVENT_PUSH_TYPES,
    PARAM_WEBHOOK_ID,
    PARAM_WEBHOOK_URL,
)
from .helpers import get_selected_device_ids


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    app_id = entry.data.get(PARAM_APP_ID, "")
    webhook_id = entry.data.get(PARAM_WEBHOOK_ID, "")
    webhook_url = entry.options.get(PARAM_WEBHOOK_URL, "")
    selected = get_selected_device_ids(entry)
    selected_count = None if selected is None else len(selected)

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
        "base_push": BASE_PUSH_ALWAYS,
        "selected_devices_count": selected_count,
        "recent_msg_type_counts": (
            dict(runtime.push_msg_type_counts) if runtime is not None else {}
        ),
        "last_msg_type": (runtime.push_last_msg_type if runtime is not None else None),
        "last_received_at": last_received,
    }

    return {
        "app_id": f"{app_id[:4]}…" if len(app_id) > 4 else app_id,
        "api_url": entry.data.get(PARAM_API_URL),
        "selected_devices_count": selected_count,
        "event_push_enabled": bool(entry.options.get(PARAM_ENABLE_EVENT_PUSH)),
        "webhook_id": f"{webhook_id[:8]}…" if len(webhook_id) > 8 else webhook_id,
        "webhook_url_configured": bool(webhook_url),
        "last_update_success": last_update_success,
        "pyimouapi_version": version("pyimouapi"),
        "event_push": event_push,
    }
