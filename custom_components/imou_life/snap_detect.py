"""deviceSnapDetect service: capture, detect, and notify."""

from __future__ import annotations

import logging

from aiohttp import ClientError, ClientTimeout
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import translation
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pyimouapi.exceptions import ImouException
from pyimouapi.snap_detect import DeviceSnapDetectResult, parse_device_snap_detect_data

from .const import DOMAIN, SERVICE_DEVICE_SNAP_DETECT
from .coordinator import ImouConfigEntry
from .helpers import (
    async_deliver_message_to_targets,
    async_display_language,
    default_notify_service_targets,
    fill_template,
    parse_notify_services,
    resolve_ha_device_name,
    selector_option_label,
)
from .runtime_data import get_runtime_data
from .snap_boxes import annotate_snap_jpeg, annotated_snap_url, write_annotated_snap

_LOGGER = logging.getLogger(__name__)

_TARGET_LABELS = {
    "human": ("detected_person", "Person"),
    "vehicle": ("detected_vehicle", "Vehicle"),
    "animal": ("detected_pet", "Pet"),
    "box": ("detected_object", "Object"),
}


async def _async_build_notification(
    hass: HomeAssistant,
    device_name: str,
    result: DeviceSnapDetectResult,
    picture_url: str | None,
) -> tuple[str, str]:
    language = await async_display_language(hass)
    await translation.async_get_translations(
        hass, language, "selector", integrations={DOMAIN}
    )

    def copy(key: str, fallback: str, **values: str) -> str:
        template = selector_option_label(
            hass, language, "webhook_notification", key, fallback
        )
        return fill_template(template, fallback, **values)

    title = device_name
    if not result.detect_result and not result.targets:
        message = copy("no_target", "No target detected.")
    else:
        parts: list[str] = []
        for target in result.targets:
            mapped = _TARGET_LABELS.get(target.target_type)
            label = (
                copy(mapped[0], mapped[1]) if mapped is not None else target.target_type
            )
            if target.confidence not in (None, ""):
                parts.append(f"{label} ({target.confidence})")
            else:
                parts.append(label)
        message = (
            copy("detected", "Detected: {targets}", targets=", ".join(parts))
            if parts
            else copy("target_detected", "Target detected.")
        )
    if picture_url:
        message = f"{message}\n\n![]({picture_url})"
    return title, message


async def _async_download_snap(hass: HomeAssistant, url: str) -> bytes | None:
    """Download the snapshot that deviceSnapDetect returned."""
    session = async_get_clientsession(hass)
    try:
        async with session.get(url, timeout=ClientTimeout(total=20)) as response:
            if response.status != 200:
                _LOGGER.debug("Snap picture HTTP %s", response.status)
                return None
            data = await response.read()
    except (TimeoutError, ClientError):
        _LOGGER.debug("Snap picture download failed", exc_info=True)
        return None
    if not data or len(data) > 8_000_000:
        return None
    return data


async def _async_annotated_picture_url(
    hass: HomeAssistant,
    device_id: str,
    channel_id: str | None,
    source_url: str,
    result: DeviceSnapDetectResult,
) -> str | None:
    """Download the snapshot, draw boxes, and return a URL for that picture."""
    jpeg = await _async_download_snap(hass, source_url)
    if not jpeg:
        return None

    def _draw_and_save() -> str | None:
        annotated = annotate_snap_jpeg(jpeg, result.targets)
        if not annotated:
            return None
        local_path = write_annotated_snap(hass, device_id, channel_id, annotated)
        if local_path is None:
            return None
        return annotated_snap_url(hass, local_path)

    return await hass.async_add_executor_job(_draw_and_save)


def _first_id(raw: object) -> str | None:
    if isinstance(raw, list):
        return str(raw[0]) if raw else None
    if raw not in (None, ""):
        return str(raw)
    return None


def _call_id(call: ServiceCall, key: str) -> str | None:
    target = getattr(call, "target", None)
    if isinstance(target, dict) and target.get(key):
        found = _first_id(target.get(key))
        if found:
            return found
    return _first_id(call.data.get(key))


def _notify_targets(
    hass: HomeAssistant, call: ServiceCall, runtime_services: list[str]
) -> list[str]:
    notify_raw = call.data.get("notify_targets")
    if notify_raw is None:
        notify = list(runtime_services)
        if not notify:
            notify = default_notify_service_targets(hass)
        return notify
    return parse_notify_services(notify_raw)


def _resolve_device_target(
    hass: HomeAssistant, call: ServiceCall, device_id: str
) -> tuple[ImouConfigEntry, str, str | None, list[str]]:
    """Resolve a Home Assistant device to one Imou channel."""
    device_entry = dr.async_get(hass).async_get(device_id)
    registry_key = None
    if device_entry is not None:
        for domain, identifier in device_entry.identifiers:
            if domain == DOMAIN:
                registry_key = identifier
                break
    if device_entry is None or registry_key is None or not device_entry.config_entries:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="snap_detect_entity_not_found",
        )
    entry = hass.config_entries.async_get_entry(next(iter(device_entry.config_entries)))
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
    device = runtime.coordinator.devices_by_key.get(registry_key)
    if device is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="snap_detect_device_not_loaded",
        )
    return (
        config_entry,
        device.device_id,
        device.channel_id,
        _notify_targets(hass, call, list(runtime.notify_services)),
    )


def _resolve_call(
    hass: HomeAssistant, call: ServiceCall
) -> tuple[ImouConfigEntry, str, str | None, list[str]]:
    device_id = _call_id(call, "device_id")
    if device_id:
        return _resolve_device_target(hass, call, device_id)
    entity_id = _call_id(call, "entity_id")
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
    return (
        config_entry,
        device.device_id,
        channel_id,
        _notify_targets(hass, call, list(runtime.notify_services)),
    )


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
    image_url = result.url if result.url and result.url.startswith("https://") else None
    if image_url and any(target.region for target in result.targets):
        annotated = await _async_annotated_picture_url(
            hass, device_id, channel_id, image_url, result
        )
        if annotated:
            image_url = annotated
    title, message = await _async_build_notification(
        hass, device_name, result, image_url
    )
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
