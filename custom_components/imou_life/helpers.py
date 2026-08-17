"""Shared helpers for Imou Life."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import translation
from pyimouapi.device import ImouDeviceSummary

from .const import DOMAIN, PARAM_SELECTED_DEVICES, imou_life_device_keys_from_ids


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


def resolve_ha_device_name(
    hass: HomeAssistant,
    device_id: str | None,
    channel_id: object | None = None,
    product_id: str | None = None,
) -> str | None:
    """Return HA device display name for Imou ids, or None if not registered."""
    registry = dr.async_get(hass)
    for key in imou_life_device_keys_from_ids(device_id, channel_id, product_id):
        device = registry.async_get_device(identifiers={(DOMAIN, key)})
        if device is not None:
            return device.name_by_user or device.name
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


def parse_notify_services(raw: Any) -> list[str]:
    """Normalize stored notify options to a list of domain.service ids."""
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    if isinstance(raw, str) and raw.strip():
        return [part.strip() for part in raw.split(",") if part.strip()]
    return []


_NOTIFY_SERVICES_NOT_TARGETS = frozenset({"send_message"})


def notify_service_selector_options(
    hass: HomeAssistant, stored: list[str]
) -> list[dict[str, str]]:
    """Return notify targets plus any saved non-notify actions.

    ``notify.send_message`` is a generic action, not a destination, so it is
    omitted unless the user already saved it.
    """
    notify = hass.services.async_services().get("notify", {})
    values: list[str] = []
    for name in notify:
        if name in _NOTIFY_SERVICES_NOT_TARGETS:
            continue
        values.append(f"notify.{name}")
    for extra in stored:
        if extra not in values:
            values.append(extra)
    return [{"value": item, "label": item} for item in sorted(values)]


async def async_build_device_map(hass: HomeAssistant, api_client) -> dict[str, str]:
    """Fetch device summaries and return {device_id: label}."""
    from pyimouapi.device import ImouDeviceManager

    manager = ImouDeviceManager(api_client)
    summaries = await manager.async_get_device_summaries()
    return {s.device_id: format_device_label(hass, s) for s in summaries}
