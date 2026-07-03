"""Diagnostics support for Imou Life."""

from __future__ import annotations

from importlib.metadata import version
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    PARAM_API_URL,
    PARAM_APP_ID,
    PARAM_ENABLE_EVENT_PUSH,
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

    coordinator = entry.runtime_data.coordinator if entry.runtime_data else None
    last_update_success = (
        coordinator.last_update_success if coordinator is not None else None
    )

    return {
        "app_id": f"{app_id[:4]}…" if len(app_id) > 4 else app_id,
        "api_url": entry.data.get(PARAM_API_URL),
        "selected_devices_count": len(selected),
        "event_push_enabled": bool(entry.options.get(PARAM_ENABLE_EVENT_PUSH)),
        "webhook_id": f"{webhook_id[:8]}…" if len(webhook_id) > 8 else webhook_id,
        "webhook_url_configured": bool(webhook_url),
        "last_update_success": last_update_success,
        "pyimouapi_version": version("pyimouapi"),
    }
