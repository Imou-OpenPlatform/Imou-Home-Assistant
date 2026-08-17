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
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    DOMAIN,
    EVENT_IMOU_ALARM,
    EVENT_IMOU_EVENT,
    PARAM_NOTIFY_ON_ALARM,
    PARAM_WEBHOOK_ID,
    imou_life_device_keys_from_ids,
)
from .helpers import resolve_ha_device_name
from .local_record import async_maybe_record_from_alarm
from .runtime_data import ImouRuntimeData, get_runtime_data

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
        "low_battery_alarm",
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
        # upgrade / storage ops (PaaS msgType and IoT identifier)
        "UpgradeSuccess",
        "upgradeFail",
        "apUpgradeSuccess",
        "apUpgradeFail",
        "storageRecoverOk",
        "storageRecoverFail",
        "storageEmpty",
        "storageAbnormal",
        "e_upgradeSuccess",
        "e_upgradeFail",
        "e_storageEmpty",
        "e_storageAbnormal",
        "upgrading",
        "upgrade_success",
        "upgrade_failed",
        "upgrade_result",
        "e_matchApSucc",
        # alarm-panel scenario / disarm status
        "home",
        "leave",
        "no_defend",
    }
)


def _is_alarm_msg_type(msg_type: str | None) -> bool:
    """Return True if this push should fire imou_life_alarm / notify."""
    return msg_type is not None and msg_type not in _NON_ALARM_MSG_TYPES


def _notify_on_alarm_enabled(hass: HomeAssistant, event_data: dict[str, Any]) -> bool:
    """Return True unless this device's notify-on-alarm switch is off.

    Missing entity or unresolvable device key defaults to on so existing
    installs keep sending until the user turns a switch off.
    """
    registry = er.async_get(hass)
    for device_key in imou_life_device_keys_from_ids(
        event_data.get("device_id"),
        event_data.get("channel_id"),
        event_data.get("product_id"),
    ):
        entity_id = registry.async_get_entity_id(
            "switch", DOMAIN, f"{device_key}${PARAM_NOTIFY_ON_ALARM}"
        )
        if not entity_id:
            continue
        state = hass.states.get(entity_id)
        return state is None or state.state == STATE_ON
    return True


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


def _channel_id_from_payload(payload: dict[str, Any]) -> Any:
    """Return a scalar channel id from the push payload, if present."""
    for key in ("cid", "channelId", "msgChannelId"):
        if key not in payload:
            continue
        value = payload.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, (list, dict)):
            continue
        return value
    channels = payload.get("channels")
    if isinstance(channels, (list, dict)) or channels in (None, ""):
        return None
    return channels


def _normalize_event_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize different Imou push formats into convenient common fields."""
    device_id = (
        payload.get("did")
        or payload.get("deviceId")
        or payload.get("msgDeviceId")
        or payload.get("dname")
    )
    channel_id = _channel_id_from_payload(payload)
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


def _alarm_type_label(alarm_types: dict[str, str], msg_type: str | None) -> str | None:
    """Map msg_type / IoT identifier to a localized label."""
    if not msg_type:
        return None
    if msg_type in alarm_types:
        return alarm_types[msg_type]
    if msg_type.startswith("e_") and msg_type[2:] in alarm_types:
        return alarm_types[msg_type[2:]]
    return None


def _format_notification_time(raw_time: Any) -> str:
    """Normalize push timestamps to HH:MM:SS for notification bodies."""
    if raw_time is None or raw_time == "":
        return ""
    if isinstance(raw_time, (int, float)):
        value = float(raw_time)
        # Milliseconds since epoch.
        if value > 1_000_000_000_000:
            value /= 1000.0
        if value > 1_000_000_000:
            try:
                return datetime.fromtimestamp(value).strftime("%H:%M:%S")
            except (OSError, OverflowError, ValueError):
                return str(raw_time)
        return str(raw_time)

    text = str(raw_time).strip()
    if not text:
        return ""
    if text.isdigit():
        return _format_notification_time(int(text))

    # Compact IoT localTime: 20260817T143005 or 20260817T143005.000
    if "T" in text and "-" not in text.split("T", 1)[0]:
        date_part, time_part = text.split("T", 1)
        time_part = time_part.split(".", 1)[0].replace("Z", "")
        if len(date_part) == 8 and len(time_part) >= 6 and time_part[:6].isdigit():
            return f"{time_part[0:2]}:{time_part[2:4]}:{time_part[4:6]}"

    if "T" in text:
        candidate = text.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(candidate).strftime("%H:%M:%S")
        except ValueError:
            after = text.split("T", 1)[1]
            digits = "".join(ch for ch in after if ch.isdigit())
            if len(digits) >= 6:
                return f"{digits[0:2]}:{digits[2:4]}:{digits[4:6]}"

    return text


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
    # Prefer the Home Assistant registry name; never use the cloud dname/cname.
    device_name = (
        event_data.get("device_name")
        or event_data.get("device_id")
        or unknown_device
    )
    alarm_type = _alarm_type_label(alarm_types, msg_type) or (
        msg_type if msg_type else unknown_alarm
    )
    time_str = _format_notification_time(event_data.get("time"))

    # Title is the alarm type only; body carries device (and time) without
    # repeating the same type line.
    title = notif.get("title", "Imou Life · {alarm_type}").format(alarm_type=alarm_type)
    message = notif.get("device", "Device: {device_name}").format(
        device_name=device_name
    )
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


def _get_entry_and_runtime(
    hass: HomeAssistant, webhook_id: str
) -> tuple[ConfigEntry, ImouRuntimeData] | None:
    """Return the loaded config entry and runtime data owning webhook_id."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.data.get(PARAM_WEBHOOK_ID) != webhook_id:
            continue
        if (runtime := get_runtime_data(entry)) is not None:
            return entry, runtime
    _LOGGER.debug("No loaded Imou config entry for webhook_id %s", webhook_id)
    return None


async def _async_dispatch_imou_push(
    hass: HomeAssistant,
    entry: ConfigEntry,
    runtime: ImouRuntimeData,
    event_data: dict[str, Any],
) -> None:
    """Resolve identifiers, classify, fire events, and notify after webhook ACK."""
    try:
        await _async_apply_event_identifier(runtime, event_data)
        runtime.record_push_msg(event_data.get("msg_type"))
        hass.bus.async_fire(EVENT_IMOU_EVENT, event_data)
        if _is_alarm_msg_type(event_data.get("msg_type")):
            hass.bus.async_fire(EVENT_IMOU_ALARM, event_data)
            notify_services = runtime.notify_services
            if notify_services and _notify_on_alarm_enabled(hass, event_data):
                await _async_send_notifications(hass, event_data, notify_services)
            await async_maybe_record_from_alarm(hass, entry, event_data)
    except Exception:
        _LOGGER.exception("Failed while processing accepted Imou webhook push")


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

    _LOGGER.debug("Received Imou push raw payload: %s", payload)

    event_data = _normalize_event_payload(payload)
    device_id = event_data.get("device_id")
    _LOGGER.debug("Received Imou push event: %s", event_data)

    # Check: is push enabled? If user disabled it, silently ignore.
    entry_and_runtime = _get_entry_and_runtime(hass, webhook_id)
    if entry_and_runtime is None:
        return web.Response(status=200, text="ok")
    entry, runtime = entry_and_runtime
    if not runtime.push_enabled:
        _LOGGER.debug("Push is disabled, ignoring event")
        return web.Response(status=200, text="ok")

    # IoT devices (product id present) only accept the iotEvent envelope.
    if event_data.get("product_id") and event_data.get("msg_type") != "iotEvent":
        _LOGGER.debug(
            "Ignoring non-iotEvent push for IoT device %s (msg_type=%s)",
            device_id,
            event_data.get("msg_type"),
        )
        return web.Response(status=200, text="ok")

    # Filter: None = all devices; [] = none; otherwise allow-list
    selected_devices = runtime.selected_devices
    if selected_devices is not None and (
        not device_id or device_id not in selected_devices
    ):
        _LOGGER.debug(
            "Ignoring push from unselected device %s (selected: %s)",
            device_id,
            selected_devices,
        )
        return web.Response(status=200, text="ok")

    event_data["device_name"] = resolve_ha_device_name(
        hass,
        event_data.get("device_id"),
        channel_id=event_data.get("channel_id"),
        product_id=event_data.get("product_id"),
    )

    # ACK first so Imou does not stop pushing while we resolve/notify. The task is
    # tied to the entry so it cannot outlive the runtime data it holds.
    entry.async_create_background_task(
        hass,
        _async_dispatch_imou_push(hass, entry, runtime, event_data),
        name=f"{DOMAIN}_webhook_dispatch_{webhook_id}",
    )
    return web.Response(status=200, text="ok")


def async_register_imou_webhook(hass: HomeAssistant, webhook_id: str) -> str:
    """Register HA webhook and return the external URL."""
    # A setup attempt that failed after registering leaves the handler behind,
    # and re-registering the same id raises. Drop any stale handler first.
    webhook.async_unregister(hass, webhook_id)
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
