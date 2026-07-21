"""Webhook support for Imou Life event push messages."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from aiohttp import web
from homeassistant.components import webhook
from homeassistant.core import HomeAssistant

from .const import DOMAIN, EVENT_IMOU_ALARM, EVENT_IMOU_EVENT, PARAM_WEBHOOK_ID
from .runtime_data import ImouRuntimeData

_LOGGER = logging.getLogger(__name__)

_WEBHOOK_STRINGS_DIR = Path(__file__).parent / "webhook_strings"

# Status / ops types that must NOT fire imou_life_alarm or notify.
# Official Imou "Device alarm" list also includes privacy-mask and lifecycle
# types; we subtract those here. See:
# https://open.imoulife.com/book/en/push/alarm.html
_NON_ALARM_MSG_TYPES = frozenset(
    {
        # deviceStatus
        "online",
        "offline",
        "close",
        "changeDevName",
        # iot property/action & stats (iotEvent is an alarm)
        "iotProperty",
        "iotAction",
        "numberstat",
        "electricity",
        # privacy mask (#66)
        "openCamera",
        "closeCamera",
        # light state (sirenOn/sirenOff are alarms)
        "whiteLightOn",
        "whiteLightOff",
        # sleep
        "sleep",
        # bind / share / auth / transfer
        "bindDevice",
        "unbindDevice",
        "deviceShare",
        "deviceShareCancel",
        "deviceAuthorize",
        "deviceAuthorizationChanged",
        "transferDeviceFrom",
        "transferDeviceTo",
        "deviceDeletedSharedCancel",
        # upgrade / storage ops
        "UpgradeSuccess",
        "upgradeFail",
        "apUpgradeSuccess",
        "apUpgradeFail",
        "storageRecoverOk",
        "storageRecoverFail",
        "storageEmpty",
        "storageAbnormal",
    }
)


def _is_alarm_msg_type(msg_type: str | None) -> bool:
    """Return True if this push should fire imou_life_alarm / notify."""
    return msg_type is not None and msg_type not in _NON_ALARM_MSG_TYPES


def _webhook_strings_filename(language: str) -> str:
    if language.startswith("zh"):
        return "zh-Hans.json"
    return "en.json"


@lru_cache(maxsize=4)
def _load_webhook_strings_file(filename: str) -> dict[str, Any]:
    """Load a webhook strings JSON file (blocking; call via executor)."""
    path = _WEBHOOK_STRINGS_DIR / filename
    return json.loads(path.read_text(encoding="utf-8"))


_WEBHOOK_STRING_FILES = ("en.json", "zh-Hans.json")


async def async_preload_webhook_strings(hass: HomeAssistant) -> None:
    """Warm webhook string cache off the event loop."""
    for filename in _WEBHOOK_STRING_FILES:
        await hass.async_add_executor_job(_load_webhook_strings_file, filename)


async def _async_get_webhook_strings(
    hass: HomeAssistant,
    language: str,
) -> tuple[dict[str, str], dict[str, str]]:
    """Load webhook notification templates and alarm type labels."""
    filename = _webhook_strings_filename(language)
    data = await hass.async_add_executor_job(_load_webhook_strings_file, filename)
    notification = data.get("notification", {})
    alarm_types = data.get("alarm_types", {})
    return notification, alarm_types


def _normalize_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize different Imou push formats into convenient common fields."""
    device_id = (
        payload.get("did")
        or payload.get("deviceId")
        or payload.get("msgDeviceId")
        or payload.get("dname")
    )
    channel_id = (
        payload.get("cid")
        if "cid" in payload
        else payload.get("channelId")
        or payload.get("msgChannelId")
        or payload.get("channels")
    )
    msg_type = payload.get("msgType")
    content = payload.get("content")
    output_data = None
    if isinstance(content, dict):
        output_data = content.get("outputData")
        if channel_id is None:
            monitor = content.get("monitor")
            if isinstance(monitor, dict) and "channel" in monitor:
                channel_id = monitor.get("channel")

    raw_time = payload.get("time") or payload.get("localTime") or payload.get("utcTime")

    event = {
        "msg_type": msg_type,
        "msg_type_name": msg_type,
        "device_id": device_id,
        "channel_id": channel_id,
        "product_id": payload.get("pid"),
        "time": raw_time,
        "name": payload.get("cname") or payload.get("dname"),
        "alarm_id": payload.get("id") or payload.get("alarmId"),
        "token": payload.get("token"),
        "desc": payload.get("desc"),
        "outputData": output_data,
        "raw": payload,
    }
    return event


def _is_digit_str(value: Any) -> bool:
    return isinstance(value, str) and value.isdigit()


def _lookup_key_for_event_resolve(
    msg_type: str | None, raw: dict[str, Any]
) -> str | None:
    """Return the product-model event ref to resolve, or None to skip."""
    if _is_digit_str(msg_type):
        return msg_type
    if msg_type == "iotEvent":
        content = raw.get("content")
        if isinstance(content, dict):
            event_ref = content.get("event")
            if event_ref is None or event_ref == "":
                return None
            key = str(event_ref)
            if key.isdigit():
                return key
    return None


async def _async_apply_event_identifier(
    runtime: ImouRuntimeData, event_data: dict[str, Any]
) -> None:
    """Rewrite outbound msg_type to identifier when resolvable."""
    product_id = event_data.get("product_id")
    raw = event_data.get("raw")
    if not product_id or not isinstance(raw, dict):
        return
    lookup_key = _lookup_key_for_event_resolve(event_data.get("msg_type"), raw)
    if not lookup_key:
        return
    try:
        delegate = runtime.coordinator.device_manager.delegate
        identifier = await delegate.async_resolve_event_identifier(
            product_id, lookup_key
        )
    except Exception:
        _LOGGER.warning(
            "Failed to resolve event identifier for product_id=%s key=%s",
            product_id,
            lookup_key,
            exc_info=True,
        )
        return
    if identifier:
        event_data["msg_type"] = identifier
        event_data["msg_type_name"] = identifier


async def _async_build_notification_message(
    hass: HomeAssistant, event_data: dict[str, Any]
) -> tuple[str, str]:
    """Build a notification title and message from an event payload."""
    notif, alarm_types = await _async_get_webhook_strings(hass, hass.config.language)

    msg_type = event_data.get("msg_type")
    unknown_device = notif.get("unknown_device", "Unknown device")
    unknown_alarm = notif.get("unknown_alarm", "Alarm")
    device_name = (
        event_data.get("name") or event_data.get("device_id") or unknown_device
    )
    alarm_type = alarm_types.get(msg_type, msg_type or unknown_alarm)

    raw_time = event_data.get("time")
    if isinstance(raw_time, (int, float)) and raw_time > 1000000000:
        try:
            time_str = datetime.fromtimestamp(raw_time).strftime("%H:%M:%S")
        except (OSError, ValueError):
            time_str = str(raw_time)
    else:
        time_str = str(raw_time) if raw_time else ""

    title = notif.get("title", "Imou alarm: {alarm_type}").format(alarm_type=alarm_type)
    message = notif.get("device", "Device: {device_name}").format(
        device_name=device_name
    )
    type_line = notif.get("type", "Type: {alarm_type}").format(alarm_type=alarm_type)
    message += f"\n{type_line}"
    if time_str:
        time_line = notif.get("time", "Time: {time_str}").format(time_str=time_str)
        message += f"\n{time_line}"

    desc = event_data.get("desc")
    if desc and isinstance(desc, dict):
        desc_type = desc.get("type")
        if desc_type:
            details_line = notif.get("details", "Details: {desc_type}").format(
                desc_type=desc_type
            )
            message += f"\n{details_line}"

    return title, message


async def _async_send_notifications(
    hass: HomeAssistant, event_data: dict[str, Any], notify_services: list[str]
) -> None:
    """Send alarm notifications.

    Supports:
      - "qiyewechat.send"           -> calls qiyewechat.send service
      - "notify.mobile_app_xxx"     -> calls legacy notify service
      - "notify.xxx"                -> tries notify.send_message entity, then legacy
      - "domain.service"            -> calls any HA service
    """
    title, message = await _async_build_notification_message(hass, event_data)
    for svc in notify_services:
        svc = svc.strip()
        if not svc:
            continue
        # Parse domain.service
        if "." in svc:
            svc_domain, svc_name = svc.split(".", 1)
        else:
            svc_domain = "notify"
            svc_name = svc
        try:
            await hass.services.async_call(
                svc_domain,
                svc_name,
                {"message": message, "title": title},
                blocking=False,
            )
            _LOGGER.debug("Sent alarm notification via %s.%s", svc_domain, svc_name)
        except Exception:
            _LOGGER.exception(
                "Failed to send alarm notification via %s.%s", svc_domain, svc_name
            )


def _get_runtime_data_for_webhook(
    hass: HomeAssistant, webhook_id: str
) -> ImouRuntimeData | None:
    """Return runtime data for the config entry that owns webhook_id."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.data.get(PARAM_WEBHOOK_ID) != webhook_id:
            continue
        runtime = entry.runtime_data
        if runtime is not None:
            return runtime
    _LOGGER.debug("No Imou config entry for webhook_id %s", webhook_id)
    return None


async def async_handle_imou_webhook(
    hass: HomeAssistant,
    webhook_id: str,
    request: web.Request,
) -> web.Response:
    """Handle alarm/event push messages from Imou Open Platform."""
    try:
        payload = await request.json()
    except Exception as err:
        _LOGGER.warning("Invalid Imou webhook payload: %s", err)
        return web.Response(status=200, text="ok")

    if not isinstance(payload, dict):
        _LOGGER.warning("Unexpected Imou webhook payload type: %s", type(payload))
        return web.Response(status=200, text="ok")

    event_data = _normalize_event_payload(payload)
    msg_type = event_data.get("msg_type")
    device_id = event_data.get("device_id")
    _LOGGER.debug("Received Imou push event: %s", event_data)

    # Check: is push enabled? If user disabled it, silently ignore.
    runtime = _get_runtime_data_for_webhook(hass, webhook_id)
    if runtime is None or not runtime.push_enabled:
        _LOGGER.debug("Push is disabled, ignoring event")
        return web.Response(status=200, text="ok")

    # Filter: only process events from selected devices (if device selection is active)
    selected_devices = runtime.selected_devices
    if selected_devices and device_id and device_id not in selected_devices:
        _LOGGER.debug(
            "Ignoring push from unselected device %s (selected: %s)",
            device_id,
            selected_devices,
        )
        return web.Response(status=200, text="ok")

    runtime.record_push_msg(msg_type)

    # Classify on original top-level msgType before outbound identifier rewrite.
    is_alarm = _is_alarm_msg_type(msg_type)
    await _async_apply_event_identifier(runtime, event_data)

    # Always fire the generic event
    hass.bus.async_fire(EVENT_IMOU_EVENT, event_data)

    # Fire alarm-specific event + send notifications for security alarms
    if is_alarm:
        hass.bus.async_fire(EVENT_IMOU_ALARM, event_data)

    # Send notifications if configured
    notify_services = runtime.notify_services
    if is_alarm and notify_services:
        await _async_send_notifications(hass, event_data, notify_services)

    # Imou explicitly requires HTTP 200, otherwise it may stop pushing messages.
    return web.Response(status=200, text="ok")


def async_register_imou_webhook(hass: HomeAssistant, webhook_id: str) -> str:
    """Register HA webhook and return the external URL."""
    webhook.async_register(
        hass,
        DOMAIN,
        "Imou Life Event Push",
        webhook_id,
        async_handle_imou_webhook,
    )
    try:
        return webhook.async_generate_url(hass, webhook_id)
    except Exception:
        _LOGGER.warning(
            "Could not generate external webhook URL. "
            "Please set webhook_url manually in integration options."
        )
        return ""


def async_unregister_imou_webhook(hass: HomeAssistant, webhook_id: str) -> None:
    """Unregister HA webhook."""
    webhook.async_unregister(hass, webhook_id)
