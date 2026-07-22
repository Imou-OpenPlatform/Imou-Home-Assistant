"""Shared helpers for Imou Life."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import translation
from pyimouapi.device import ImouDeviceSummary

from .const import DOMAIN, PARAM_SELECTED_DEVICES


def get_selected_device_ids(entry: ConfigEntry) -> list[str] | None:
    """Return selected device ids, or None when selection means all devices.

    Empty list means poll/accept none. Missing key means no filter (all).
    Options take precedence over data; an explicit empty list is preserved.
    """
    if PARAM_SELECTED_DEVICES in entry.options:
        return list(entry.options[PARAM_SELECTED_DEVICES])
    if PARAM_SELECTED_DEVICES in entry.data:
        return list(entry.data[PARAM_SELECTED_DEVICES])
    return None


def format_device_label(hass: HomeAssistant, summary: ImouDeviceSummary) -> str:
    """Build a human-readable device label for config/options selectors."""
    translations = translation.async_get_cached_translations(
        hass, hass.config.language, "selector", DOMAIN
    )
    name = summary.name
    label = (
        f"{name} ({summary.model})"
        if summary.model and summary.model != "unknown"
        else name
    )
    status_key = f"component.{DOMAIN}.selector.device_status.options.{summary.status}"
    status_text = translations.get(status_key)
    if status_text:
        label += f" [{status_text}]"
    return label


async def async_build_device_map(hass: HomeAssistant, api_client) -> dict[str, str]:
    """Fetch device summaries and return {device_id: label}."""
    from pyimouapi.device import ImouDeviceManager

    manager = ImouDeviceManager(api_client)
    summaries = await manager.async_get_device_summaries()
    return {s.device_id: format_device_label(hass, s) for s in summaries}
