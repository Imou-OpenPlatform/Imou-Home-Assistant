"""Webhook support for Imou Life event push messages."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, tzinfo
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiohttp import web
from homeassistant.components import persistent_notification, webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr
from pyimouapi.push import (
    event_ref_lookup_key,
    is_alarm_msg_type,
    is_iot_non_event,
    normalize_push_payload,
)

from .const import (
    DOMAIN,
    EVENT_IMOU_ALARM,
    EVENT_IMOU_EVENT,
    PARAM_ENABLE_EVENT_PUSH,
    PARAM_NOTIFY_ON_ALARM,
    PARAM_WEBHOOK_ID,
)
from .helpers import (
    resolve_ha_device_entry,
    resolve_ha_device_key,
    resolve_ha_device_name,
)
from .local_record import async_maybe_record_from_alarm
from .pic_thumbnail import async_maybe_decrypt_thumbnail, public_media_url
from .runtime_data import ImouRuntimeData, get_runtime_data

_LOGGER = logging.getLogger(__name__)

_WEBHOOK_STRINGS_DIR = Path(__file__).parent / "webhook_strings"


def _redacted_push_for_log(event_data: dict[str, Any]) -> dict[str, Any]:
    """Copy push fields for debug logs without the push token or raw body."""
    safe = dict(event_data)
    if safe.get("token"):
        safe["token"] = "***"
    safe.pop("raw", None)
    return safe


def _notify_on_alarm_enabled(hass: HomeAssistant, event_data: dict[str, Any]) -> bool:
    """Return True unless this device's notify-on-alarm switch is off.

    Missing entity, unresolvable device key, or a non-off state (unknown /
    unavailable during coordinator failure) defaults to on so existing
    installs keep sending until the user turns a switch off.
    """
    registry = er.async_get(hass)
    device_key = resolve_ha_device_key(
        hass,
        event_data.get("device_id"),
        event_data.get("channel_id"),
        event_data.get("product_id"),
    )
    if device_key is None:
        return True
    entity_id = registry.async_get_entity_id(
        "switch", DOMAIN, f"{device_key}${PARAM_NOTIFY_ON_ALARM}"
    )
    if not entity_id:
        return True
    state = hass.states.get(entity_id)
    if state is None:
        return True
    return state.state != STATE_OFF


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


def _alarm_type_label(alarm_types: dict[str, str], msg_type: str | None) -> str | None:
    """Map msg_type / IoT identifier to a localized label."""
    if not msg_type:
        return None
    if msg_type in alarm_types:
        return alarm_types[msg_type]
    if msg_type.startswith("e_") and msg_type[2:] in alarm_types:
        return alarm_types[msg_type[2:]]
    return None


def _zoneinfo(tz_name: str | None) -> tzinfo:
    """Return a tzinfo for HA's configured zone, defaulting to UTC."""
    if not tz_name:
        return UTC
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return UTC


def _format_wall_datetime(dt: datetime, tz: tzinfo) -> str:
    """Format a datetime as YYYY-MM-DD HH:MM:SS in tz.

    Naive values are treated as already in tz. Aware values are converted.
    """
    dt = dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


_COMPACT_TZ_SUFFIX_RE = re.compile(
    r"^(?P<time>\d{6}(?:\.\d+)?)(?P<tz>Z|[+-]\d{2}:?\d{2})$",
    re.IGNORECASE,
)


def _parse_compact_offset(offset: str) -> str:
    """Normalize a compact numeric offset for datetime.fromisoformat."""
    if offset.upper() == "Z":
        return "+00:00"
    if ":" in offset:
        return offset
    if len(offset) == 5:
        return f"{offset[:3]}:{offset[3:]}"
    if len(offset) == 3:
        return f"{offset}:00"
    return offset


def _format_notification_time(raw_time: Any, tz: tzinfo) -> str:
    """Normalize push timestamps for notification bodies."""
    if raw_time is None or raw_time == "":
        return ""
    if isinstance(raw_time, (int, float)):
        value = float(raw_time)
        if value > 1_000_000_000_000:
            value /= 1000.0
        if value > 1_000_000_000:
            try:
                return datetime.fromtimestamp(value, tz).strftime("%Y-%m-%d %H:%M:%S")
            except (OSError, OverflowError, ValueError):
                return str(raw_time)
        return str(raw_time)

    text = str(raw_time).strip()
    if not text:
        return ""
    if text.isdigit():
        return _format_notification_time(int(text), tz)

    if (
        len(text) == 8
        and text[2] == ":"
        and text[5] == ":"
        and text.replace(":", "").isdigit()
    ):
        return text

    # Compact IoT localTime: 20260817T143005 or 20260817T143005.000Z
    if "T" in text and "-" not in text.split("T", 1)[0]:
        date_part, time_part = text.split("T", 1)
        tz_suffix = ""
        time_core = time_part
        match = _COMPACT_TZ_SUFFIX_RE.match(time_part)
        if match:
            time_core = match.group("time")
            tz_suffix = match.group("tz")
        else:
            time_core = time_part.split(".", 1)[0]
        if (
            len(date_part) == 8
            and len(time_core) >= 6
            and date_part.isdigit()
            and time_core[:6].isdigit()
        ):
            if tz_suffix:
                iso_date = f"{date_part[0:4]}-{date_part[4:6]}-{date_part[6:8]}"
                iso_time = f"{time_core[0:2]}:{time_core[2:4]}:{time_core[4:6]}"
                candidate = f"{iso_date}T{iso_time}{_parse_compact_offset(tz_suffix)}"
                try:
                    parsed = datetime.fromisoformat(candidate)
                except ValueError:
                    return text
                return _format_wall_datetime(parsed, tz)
            try:
                naive = datetime(
                    int(date_part[0:4]),
                    int(date_part[4:6]),
                    int(date_part[6:8]),
                    int(time_core[0:2]),
                    int(time_core[2:4]),
                    int(time_core[4:6]),
                )
            except ValueError:
                return text
            return _format_wall_datetime(naive, tz)

    if "T" in text:
        candidate = text.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            after = text.split("T", 1)[1]
            digits = "".join(ch for ch in after if ch.isdigit())
            if len(digits) >= 6:
                return f"{digits[0:2]}:{digits[2:4]}:{digits[4:6]}"
            return text
        return _format_wall_datetime(parsed, tz)

    return text


async def _async_apply_event_identifier(
    runtime: ImouRuntimeData, event_data: dict[str, Any]
) -> None:
    """Rewrite outbound msg_type to identifier when resolvable."""
    product_id = event_data.get("product_id")
    raw = event_data.get("raw")
    if not product_id or not isinstance(raw, dict):
        return
    lookup_key = event_ref_lookup_key(event_data.get("msg_type"), raw)
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
        event_data.get("device_name") or event_data.get("device_id") or unknown_device
    )
    alarm_type = _alarm_type_label(alarm_types, msg_type) or (
        msg_type if msg_type else unknown_alarm
    )
    time_str = _format_notification_time(
        event_data.get("time"), _zoneinfo(hass.config.time_zone)
    )

    # Title is the alarm type only; body carries device (and time) without
    # repeating the same type line.
    title = notif.get("title", "Imou Life · {alarm_type}").format(alarm_type=alarm_type)
    message = notif.get("device", "Device: {device_name}").format(
        device_name=device_name
    )
    ha_device = resolve_ha_device_entry(
        hass,
        event_data.get("device_id"),
        event_data.get("channel_id"),
        event_data.get("product_id"),
    )
    if ha_device is not None and ha_device.area_id:
        area = ar.async_get(hass).async_get_area(ha_device.area_id)
        if area is not None and area.name:
            area_line = notif.get("area", "Location: {area_name}").format(
                area_name=area.name
            )
            message += f"\n{area_line}"
    if ha_device is not None and ha_device.labels:
        label_reg = lr.async_get(hass)
        label_names = sorted(
            name
            for label_id in ha_device.labels
            if (label := label_reg.async_get_label(label_id)) is not None
            and (name := label.name)
        )
        if label_names:
            labels_line = notif.get("labels", "Labels: {labels}").format(
                labels=" / ".join(label_names)
            )
            message += f"\n{labels_line}"
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


async def _async_create_web_notification(
    hass: HomeAssistant,
    event_data: dict[str, Any],
    thumbnail_url: str,
) -> None:
    """Show the decrypted still in the Home Assistant web notification drawer.

    Only called when the user picked a drawer target, so this stays opt-in
    like every other notification channel. One notification id per device
    keeps the drawer at the latest alarm per camera instead of growing without
    bound. The relative ``/local/`` path is right here: the browser is already
    on Home Assistant's own origin, and it does not need an external URL.
    """
    title, message = await _async_build_notification_message(hass, event_data)
    device_key = (
        resolve_ha_device_key(
            hass,
            event_data.get("device_id"),
            event_data.get("channel_id"),
            event_data.get("product_id"),
        )
        or event_data.get("device_id")
        or "unknown"
    )
    persistent_notification.async_create(
        hass,
        f"![]({thumbnail_url})\n\n{message}",
        title=title,
        notification_id=f"{DOMAIN}_alarm_{device_key}",
    )


def _is_companion_notify(domain: str, service: str) -> bool:
    """Return True for Companion App notify.mobile_app_* services."""
    return domain == "notify" and service.startswith("mobile_app_")


def _is_web_drawer_notify(domain: str, service: str) -> bool:
    """Return True for services that post into the web notification drawer."""
    if domain == "notify" and service == "persistent_notification":
        return True
    return domain == "persistent_notification" and service == "create"


def _split_notify_target(target: str) -> tuple[str, str] | None:
    """Split a configured target into (domain, service); bare names are notify."""
    target = target.strip()
    if not target:
        return None
    if "." in target:
        domain, service = target.split(".", 1)
        return domain, service
    return "notify", target


def _has_web_drawer_target(notify_services: list[str]) -> bool:
    """Return True when a configured target writes to the web drawer."""
    for target in notify_services:
        parts = _split_notify_target(target)
        if parts is not None and _is_web_drawer_notify(*parts):
            return True
    return False


async def _async_send_notifications(
    hass: HomeAssistant,
    event_data: dict[str, Any],
    notify_services: list[str],
    thumbnail_url: str | None = None,
    skip_web_drawer: bool = False,
) -> None:
    """Send alarm notifications to every configured target.

    A target is a ``domain.service`` pair; a bare name means ``notify.<name>``.
    Companion targets also get a link to the device page and the alarm picture,
    because only they understand those keys. Every other service receives the
    title and message alone.

    ``skip_web_drawer`` drops targets that would post a second, image-less copy
    into the drawer the caller has already filled with the decrypted still.
    """
    title, message = await _async_build_notification_message(hass, event_data)
    ha_device = resolve_ha_device_entry(
        hass,
        event_data.get("device_id"),
        event_data.get("channel_id"),
        event_data.get("product_id"),
    )
    for svc in notify_services:
        parts = _split_notify_target(svc)
        if parts is None:
            continue
        svc_domain, svc_name = parts
        if skip_web_drawer and _is_web_drawer_notify(svc_domain, svc_name):
            _LOGGER.debug(
                "Skipping %s.%s: the alarm image already covers the web drawer",
                svc_domain,
                svc_name,
            )
            continue
        service_data: dict[str, Any] = {"message": message, "title": title}
        if _is_companion_notify(svc_domain, svc_name):
            notify_data: dict[str, Any] = {}
            if ha_device is not None:
                path = f"/config/devices/device/{ha_device.id}"
                notify_data["url"] = path
                notify_data["clickAction"] = path
            if thumbnail_url:
                notify_data["image"] = thumbnail_url
                # iOS reads "attachment" and ignores "image"; Android does the
                # opposite. content-type is a file extension, not a MIME type.
                notify_data["attachment"] = {
                    "url": thumbnail_url,
                    "content-type": "jpg",
                }
                _LOGGER.debug(
                    "Attaching alarm picture %s to %s.%s",
                    thumbnail_url,
                    svc_domain,
                    svc_name,
                )
            if notify_data:
                service_data["data"] = notify_data
        try:
            await hass.services.async_call(
                svc_domain,
                svc_name,
                service_data,
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
        thumbnail_local = None
        is_alarm = is_alarm_msg_type(event_data.get("msg_type"))
        if is_alarm:
            thumbnail_local = await async_maybe_decrypt_thumbnail(
                hass, entry, runtime, event_data
            )
            if thumbnail_local:
                event_data["thumbnail_path"] = thumbnail_local
            _LOGGER.debug(
                "Alarm thumbnail for %s: %s",
                event_data.get("device_id"),
                thumbnail_local or "none",
            )
        hass.bus.async_fire(EVENT_IMOU_EVENT, event_data)
        if is_alarm:
            hass.bus.async_fire(EVENT_IMOU_ALARM, event_data)
            if _notify_on_alarm_enabled(hass, event_data):
                web_notified = False
                if thumbnail_local and _has_web_drawer_target(runtime.notify_services):
                    await _async_create_web_notification(
                        hass, event_data, thumbnail_local
                    )
                    web_notified = True
                if runtime.notify_services:
                    public_thumb = (
                        public_media_url(hass, thumbnail_local)
                        if thumbnail_local
                        else None
                    )
                    await _async_send_notifications(
                        hass,
                        event_data,
                        runtime.notify_services,
                        public_thumb,
                        skip_web_drawer=web_notified,
                    )
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

    event_data = normalize_push_payload(payload)
    device_id = event_data.get("device_id")
    _LOGGER.debug("Received Imou push: %s", _redacted_push_for_log(event_data))

    # Check: is push enabled? If user disabled it, silently ignore.
    # Prefer entry.options over runtime: options are saved before reload, and
    # the cloud may already POST while setup is still finishing.
    entry_and_runtime = _get_entry_and_runtime(hass, webhook_id)
    if entry_and_runtime is None:
        return web.Response(status=200, text="ok")
    entry, runtime = entry_and_runtime
    if not entry.options.get(PARAM_ENABLE_EVENT_PUSH):
        _LOGGER.debug("Push is disabled, ignoring event")
        return web.Response(status=200, text="ok")

    # IoT devices (product id present) only accept the iotEvent envelope.
    if is_iot_non_event(event_data.get("product_id"), event_data.get("msg_type")):
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

    ha_device_name = resolve_ha_device_name(
        hass,
        event_data.get("device_id"),
        channel_id=event_data.get("channel_id"),
        product_id=event_data.get("product_id"),
    )
    if not ha_device_name:
        _LOGGER.debug(
            "Ignoring push with no Home Assistant device for %s (msg_type=%s)",
            device_id,
            event_data.get("msg_type"),
        )
        return web.Response(status=200, text="ok")

    event_data["device_name"] = ha_device_name

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
