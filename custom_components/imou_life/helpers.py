"""Shared helpers for Imou Life."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import translation
from homeassistant.helpers.selector import SelectOptionDict
from pyimouapi.device import ImouDeviceSummary

from .const import (
    DEFAULT_EVENT_PUSH_TYPES,
    DOMAIN,
    EVENT_PUSH_TYPE_IOT,
    PARAM_ENABLE_EVENT_PUSH,
    PARAM_EVENT_PUSH_TYPES,
    PARAM_SELECTED_DEVICES,
    callback_flags_to_event_push_types,
    imou_life_device_keys_from_ids,
)


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


def iot_property_push_active(entry: ConfigEntry) -> bool:
    """Return True when IoT devices should take state from iotProperty pushes."""
    if not entry.options.get(PARAM_ENABLE_EVENT_PUSH):
        return False
    raw = entry.options.get(PARAM_EVENT_PUSH_TYPES, DEFAULT_EVENT_PUSH_TYPES)
    types = callback_flags_to_event_push_types(list(raw) if raw else [])
    return EVENT_PUSH_TYPE_IOT in types


def resolve_ha_device_key(
    hass: HomeAssistant,
    device_id: str | None,
    channel_id: object | None = None,
    product_id: str | None = None,
) -> str | None:
    """Return the first registry key that matches a registered HA device."""
    registry = dr.async_get(hass)
    for key in imou_life_device_keys_from_ids(device_id, channel_id, product_id):
        if registry.async_get_device(identifiers={(DOMAIN, key)}) is not None:
            return key
    return None


def resolve_ha_device_entry(
    hass: HomeAssistant,
    device_id: str | None,
    channel_id: object | None = None,
    product_id: str | None = None,
) -> dr.DeviceEntry | None:
    """Return the HA device registry row for Imou ids, if registered."""
    key = resolve_ha_device_key(hass, device_id, channel_id, product_id)
    if key is None:
        return None
    return dr.async_get(hass).async_get_device(identifiers={(DOMAIN, key)})


def resolve_ha_device_name(
    hass: HomeAssistant,
    device_id: str | None,
    channel_id: object | None = None,
    product_id: str | None = None,
) -> str | None:
    """Return HA device display name for Imou ids, or None if not registered."""
    device = resolve_ha_device_entry(hass, device_id, channel_id, product_id)
    if device is None:
        return None
    return device.name_by_user or device.name


def resolve_ui_language(language: str | None) -> str:
    """Map HA language tags onto translation filenames."""
    if not isinstance(language, str) or not language.strip():
        return "en"
    if language.lower().startswith("zh"):
        return "zh-Hans"
    return language


def fill_template(template: str, fallback: str, **values: str) -> str:
    """Format a translation template; never raise for bad placeholders."""
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError):
        try:
            return fallback.format(**values)
        except (KeyError, IndexError, ValueError):
            return fallback


def alarm_type_option_key(msg_type: str) -> str:
    """Map a protocol id onto a hassfest-safe selector option key."""
    return msg_type.lower().replace(".", "-")


def selector_option_label(
    hass: HomeAssistant,
    language: str | None,
    selector: str,
    key: str,
    fallback: str,
) -> str:
    """Return a selector option label, or fallback if the cache has no key."""
    resolved = resolve_ui_language(language)
    translations = translation.async_get_cached_translations(
        hass, resolved, "selector", DOMAIN
    )
    translation_key = f"component.{DOMAIN}.selector.{selector}.options.{key}"
    return translations.get(translation_key, fallback)


def format_device_label(hass: HomeAssistant, summary: ImouDeviceSummary) -> str:
    """Build a human-readable device label for config/options selectors."""
    translations = translation.async_get_cached_translations(
        hass, resolve_ui_language(hass.config.language), "selector", DOMAIN
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
) -> list[SelectOptionDict]:
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
    return [SelectOptionDict(value=item, label=item) for item in sorted(values)]


async def async_build_device_map(hass: HomeAssistant, api_client) -> dict[str, str]:
    """Fetch device summaries and return {device_id: label}."""
    from pyimouapi.device import ImouDeviceManager

    manager = ImouDeviceManager(api_client)
    summaries = await manager.async_get_device_summaries()
    return {s.device_id: format_device_label(hass, s) for s in summaries}
