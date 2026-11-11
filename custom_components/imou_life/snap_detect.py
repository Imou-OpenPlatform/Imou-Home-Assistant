"""deviceSnapDetect service: capture, detect, and notify."""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pyimouapi.exceptions import ImouException
from pyimouapi.snap_detect import DeviceSnapDetectResult, parse_device_snap_detect_data

from .const import DOMAIN, SERVICE_DEVICE_SNAP_DETECT
from .coordinator import ImouConfigEntry
from .helpers import (
    async_deliver_message_to_targets,
    default_notify_service_targets,
    parse_notify_services,
    resolve_ha_device_name,
)
from .runtime_data import get_runtime_data

_LOGGER = logging.getLogger(__name__)

_TARGET_LABELS = {
    "human": "person",
    "vehicle": "vehicle",
    "animal": "pet",
    "box": "object",
}


def _build_notification(
    device_name: str, result: DeviceSnapDetectResult
) -> tuple[str, str]:
    title = device_name
    if not result.detect_result and not result.targets:
        return title, "No target detected."
    parts: list[str] = []
    for target in result.targets:
        label = _TARGET_LABELS.get(target.target_type, target.target_type)
        if target.confidence not in (None, ""):
            parts.append(f"{label} ({target.confidence})")
        else:
            parts.append(label)
    message = "Detected: " + ", ".join(parts) if parts else "Target detected."
    if result.url:
        message = f"{message}\n{result.url}"
    return title, message


def _resolve_call(
    hass: HomeAssistant, call: ServiceCall
) -> tuple[ImouConfigEntry, str, str | None, list[str]]:
    entity_id: str | None = None
    target = getattr(call, "target", None)
    if isinstance(target, dict) and target.get("entity_id"):
        ids = target["entity_id"]
        entity_id = ids[0] if isinstance(ids, list) else str(ids)
    else:
        raw_entity = call.data.get("entity_id")
        if isinstance(raw_entity, list) and raw_entity:
            entity_id = str(raw_entity[0])
        elif raw_entity not in (None, ""):
            entity_id = str(raw_entity)
    if not entity_id:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="snap_detect_entity_required",
        )
    registry = er.async_get(hass)
    entity_entry = registry.async_get(entity_id)
    if entity_entry is None or entity_entry.config_entry_id is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="snap_detect_entity_not_found",
        )
    if not entity_id.startswith(f"{DOMAIN}."):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="snap_detect_entity_not_found",
        )
    entry = hass.config_entries.async_get_entry(entity_entry.config_entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="snap_detect_entity_not_found",
        )
    config_entry: ImouConfigEntry = entry
    runtime = get_runtime_data(config_entry)
    if runtime is None:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="snap_detect_not_ready",
        )
    unique_id = entity_entry.unique_id or ""
    device_key = unique_id.rsplit("$", 1)[0] if "$" in unique_id else unique_id
    device = runtime.coordinator.devices_by_key.get(device_key)
    if device is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="snap_detect_device_not_loaded",
        )
    channel_id = device.channel_id
    if channel_id is None and "$" in unique_id:
        suffix = unique_id.rsplit("$", 1)[-1]
        if suffix.isdigit():
            channel_id = suffix
    notify_raw = call.data.get("notify_targets")
    if notify_raw is None:
        notify = list(runtime.notify_services)
        if not notify:
            notify = default_notify_service_targets(hass)
    else:
        notify = parse_notify_services(notify_raw)
    return config_entry, device.device_id, channel_id, notify


async def async_handle_device_snap_detect(
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Call deviceSnapDetect and push results to configured notify targets."""
    entry, device_id, channel_id, notify_targets = _resolve_call(hass, call)
    runtime = get_runtime_data(entry)
    assert runtime is not None
    device_name = (
        resolve_ha_device_name(hass, device_id, channel_id, product_id=None)
        or device_id
    )
    try:
        raw = (
            await runtime.coordinator.device_manager.delegate.async_device_snap_detect(
                device_id, channel_id
            )
        )
    except ImouException as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="snap_detect_failed",
            translation_placeholders={"error": err.message or str(err)},
        ) from err
    result = parse_device_snap_detect_data(raw)
    title, message = _build_notification(device_name, result)
    image_url = result.url if result.url and result.url.startswith("https://") else None
    await async_deliver_message_to_targets(
        hass, title, message, notify_targets, image_url=image_url
    )
    _LOGGER.debug(
        "deviceSnapDetect %s ch%s targets=%s notify=%s",
        device_id,
        channel_id,
        len(result.targets),
        len(notify_targets),
    )


def async_register_snap_detect_service(hass: HomeAssistant) -> None:
    """Register the domain service once."""
    if hass.services.has_service(DOMAIN, SERVICE_DEVICE_SNAP_DETECT):
        return

    async def _handler(call: ServiceCall) -> None:
        await async_handle_device_snap_detect(hass, call)

    hass.services.async_register(DOMAIN, SERVICE_DEVICE_SNAP_DETECT, _handler)
