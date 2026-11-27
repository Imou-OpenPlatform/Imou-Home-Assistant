"""deviceSnapDetect service: capture, detect, and notify."""

from __future__ import annotations

import logging

from aiohttp import ClientError, ClientTimeout
from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import translation
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pyimouapi.const import ERROR_CODE_OPEN_API_QUOTA_EXCEEDED
from pyimouapi.exceptions import ImouException
from pyimouapi.snap_detect import DeviceSnapDetectResult, parse_device_snap_detect_data

from .const import DOMAIN, EVENT_AI_INSIGHT_TRIAL, SERVICE_DEVICE_SNAP_DETECT
from .coordinator import ImouConfigEntry
from .detection_present import confidence_line, detection_labels, seen_line
from .helpers import (
    async_deliver_message_to_targets,
    async_display_language,
    default_notify_service_targets,
    fill_template,
    resolve_ha_device_name,
    selector_option_label,
)
from .runtime_data import get_runtime_data
from .snap_boxes import annotate_snap_jpeg, annotated_snap_url, write_annotated_snap

_LOGGER = logging.getLogger(__name__)
_DRAWER_TARGETS = frozenset(
    {"notify.persistent_notification", "persistent_notification.create"}
)

_MIN_CONFIDENCE = 85

_BOX_LABEL_KEYS = {
    "human": "tag_person",
    "vehicle": "tag_vehicle",
    "animal": "tag_pet",
    "box": "tag_object",
}


def _confidence_score(raw: object) -> float | None:
    """Return a numeric confidence, or None when the value cannot be read."""
    if isinstance(raw, bool) or raw in (None, ""):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw)
        except ValueError:
            return None
    return None


def _accurate_result(result: DeviceSnapDetectResult) -> DeviceSnapDetectResult:
    """Keep targets that are at least 85% sure."""
    targets = tuple(
        target
        for target in result.targets
        if (score := _confidence_score(target.confidence)) is not None
        and score >= _MIN_CONFIDENCE
    )
    return DeviceSnapDetectResult(result.url, bool(targets), targets)


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
    labels = detection_labels(hass, language)
    if not result.targets:
        message = copy("no_target", "No target detected.")
    else:
        blocks = [
            block
            for target in result.targets
            if (block := _target_block(labels, target.target_type, target.confidence))
        ]
        message = (
            "\n\n".join(blocks)
            if blocks
            else copy("target_detected", "Target detected.")
        )
    if picture_url:
        message = f"{message}\n\n![]({picture_url})"
    return title, message


def _target_block(labels: dict[str, str], target_type: str, confidence: object) -> str:
    """Who was seen, then how sure that is, in the shared layout."""
    parts = [seen_line(labels, [target_type])]
    score = _confidence_score(confidence)
    if score is not None:
        parts.append(confidence_line(labels, score))
    return "\n\n".join(part for part in parts if part)


def _box_names(labels: dict[str, str]) -> dict[str, str]:
    return {
        target_type: labels.get(key) or key
        for target_type, key in _BOX_LABEL_KEYS.items()
    }


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
    language = await async_display_language(hass)
    names = _box_names(detection_labels(hass, language))

    def _draw_and_save() -> str | None:
        annotated = annotate_snap_jpeg(jpeg, result.targets, names)
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


def _with_drawer(targets: list[str]) -> list[str]:
    """The notification drawer is always included so a call is never silent."""
    if any(target in _DRAWER_TARGETS for target in targets):
        return targets
    return ["notify.persistent_notification", *targets]


def _notify_targets(hass: HomeAssistant, runtime_services: list[str]) -> list[str]:
    """Always notify the drawer, signed-in phones, and alarm-notification targets."""
    targets = default_notify_service_targets(hass)
    for extra in runtime_services:
        if extra not in targets:
            targets.append(extra)
    return _with_drawer(targets)


async def _async_trial_dialog(hass: HomeAssistant) -> tuple[str, str]:
    """Return the contact prompt shown when detection is not enabled."""
    language = await async_display_language(hass)
    await translation.async_get_translations(
        hass, language, "selector", integrations={DOMAIN}
    )
    title = selector_option_label(
        hass,
        language,
        "snap_detect_trial_dialog",
        "title",
        "Snapshot AI detection trial",
    )
    message = selector_option_label(
        hass,
        language,
        "snap_detect_trial_dialog",
        "message",
        "Snapshot AI detection is not enabled for this account. "
        "Contact sales for a trial:\n\n"
        "💬 WeChat: 17799860785\n"
        "📱 WhatsApp: +86 19106884129",
    )
    return title, message


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
        _notify_targets(hass, list(runtime.notify_services)),
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
        _notify_targets(hass, list(runtime.notify_services)),
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
        if err.api_code == ERROR_CODE_OPEN_API_QUOTA_EXCEEDED:
            title, message = await _async_trial_dialog(hass)
            hass.bus.async_fire(
                EVENT_AI_INSIGHT_TRIAL,
                {"title": title, "message": message},
            )
            return
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="snap_detect_failed",
            translation_placeholders={"error": err.message or str(err)},
        ) from err
    result = _accurate_result(parse_device_snap_detect_data(raw))
    source_url = (result.url or "").strip()
    image_url = source_url if source_url.startswith(("http://", "https://")) else None
    if image_url and any(target.region for target in result.targets):
        annotated = await _async_annotated_picture_url(
            hass, device_id, channel_id, image_url, result
        )
        if annotated:
            image_url = annotated
    title, message = await _async_build_notification(
        hass, device_name, result, image_url
    )
    persistent_notification.async_create(
        hass,
        message,
        title=title,
        notification_id=f"imou_life_snap_{device_id}_{channel_id or '0'}",
    )
    phone_targets = [
        target for target in notify_targets if target not in _DRAWER_TARGETS
    ]
    await async_deliver_message_to_targets(
        hass, title, message, phone_targets, image_url=image_url
    )
    _LOGGER.info(
        "Snap detect %s ch%s posted %s target(s) with picture=%s",
        device_id,
        channel_id,
        len(result.targets),
        bool(image_url),
    )


def async_register_snap_detect_service(hass: HomeAssistant) -> None:
    """Register the domain service once."""
    if hass.services.has_service(DOMAIN, SERVICE_DEVICE_SNAP_DETECT):
        return

    async def _handler(call: ServiceCall) -> None:
        await async_handle_device_snap_detect(hass, call)

    hass.services.async_register(DOMAIN, SERVICE_DEVICE_SNAP_DETECT, _handler)
