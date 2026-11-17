"""AI Insight workbench services."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pyimouapi.exceptions import ImouException
from pyimouapi.ha_device import ImouHaDevice
from pyimouapi.insight.search import InsightAlarmHit, parse_search_ai_alarms
from pyimouapi.insight.statistics import parse_alarm_message_statistics

from .const import (
    DEFAULT_INSIGHT_LOOKBACK_DAYS,
    DOMAIN,
    PARAM_INSIGHT_WORKBENCH_DEVICE,
    SERVICE_REFRESH_AI_INSIGHT_BRIEFING,
    SERVICE_SEARCH_AI_ALARMS,
)
from .coordinator import ImouConfigEntry
from .insight_account import camera_select_options
from .insight_briefing_view import briefing_card
from .insight_dashboard import keyword_entity_id
from .pic_thumbnail import async_maybe_decrypt_thumbnail
from .runtime_data import ImouRuntimeData, get_runtime_data

_LOGGER = logging.getLogger(__name__)
_SEARCH_PAGE_SIZE = 10


def _device_for_key(coordinator, key: str) -> ImouHaDevice | None:
    return coordinator.devices_by_key.get(key)


def _targeted_entity_id(call: ServiceCall) -> str | None:
    """Return the first entity id on this service call."""
    raw = call.data.get("entity_id")
    if isinstance(raw, str) and raw:
        return raw
    if isinstance(raw, list):
        for item in raw:
            if item:
                return str(item)
    return None


def _device_key_from_entity_id(hass: HomeAssistant, entity_id: str) -> str | None:
    registry = er.async_get(hass)
    row = registry.async_get(entity_id)
    if row is None or not row.unique_id:
        return None
    if "$" not in row.unique_id:
        return None
    return row.unique_id.rsplit("$", 1)[0]


def _resolve_workbench_device(
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    call: ServiceCall,
) -> ImouHaDevice:
    runtime = get_runtime_data(entry)
    if runtime is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="insight_workbench_not_ready",
        )

    entity_id = _targeted_entity_id(call)

    key: str | None = None
    if entity_id:
        row = er.async_get(hass).async_get(entity_id)
        if (
            row is not None
            and row.unique_id
            and row.unique_id.endswith(f"${PARAM_INSIGHT_WORKBENCH_DEVICE}")
        ):
            key = runtime.workbench_device_key
            if not key:
                state = hass.states.get(entity_id)
                label = state.state if state is not None else ""
                if label not in ("unknown", "unavailable", ""):
                    _, mapping = camera_select_options(runtime.coordinator.devices)
                    key = mapping.get(label)
        else:
            key = _device_key_from_entity_id(hass, entity_id)

        if key:
            device = _device_for_key(runtime.coordinator, key)
            if device is not None and device.channel_id is not None:
                return device

    key = runtime.workbench_device_key
    if not key:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="insight_workbench_no_device",
        )
    device = _device_for_key(runtime.coordinator, key)
    if device is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="insight_workbench_no_device",
        )
    return device


def _search_keyword(hass: HomeAssistant, call: ServiceCall) -> str:
    """Keyword from the call, or the text box on the search page."""
    keyword = str(call.data.get("keyword") or "").strip()
    if keyword:
        return keyword
    state = hass.states.get(keyword_entity_id(hass))
    if state is None:
        return ""
    value = state.state.strip()
    if value in ("unknown", "unavailable", ""):
        return ""
    return value


def _time_window(days: int) -> tuple[str, str, str, str]:
    days = max(1, min(days, 31))
    end = datetime.now(UTC).replace(tzinfo=None)
    begin = end - timedelta(days=days)
    search_begin = begin.strftime("%Y-%m-%d %H:%M:%S")
    search_end = end.strftime("%Y-%m-%d %H:%M:%S")
    stat_start = begin.strftime("%Y%m%d")
    stat_end = end.strftime("%Y%m%d")
    return search_begin, search_end, stat_start, stat_end


@dataclass
class _SearchSession:
    """One keyword search, including the cursor for each page already seen."""

    device_id: str
    channel: str
    product_id: str | None
    keyword: str
    begin: str
    end: str
    page: int
    history: list[str | None]
    next_index: str | None
    alarm_ids: tuple[str, ...]


def _usable_next_index(value: str | None) -> str | None:
    if not value or value in {"0", "-1"}:
        return None
    return value


def _page_direction(call: ServiceCall) -> str:
    direction = str(call.data.get("page") or "").strip().lower()
    if direction in {"next", "previous"}:
        return direction
    return ""


def _page_target(
    session: _SearchSession | None, direction: str
) -> tuple[str | None, int]:
    """Return the cursor and page number for a next or previous step."""
    if session is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="insight_search_page_unavailable",
        )
    if direction == "previous":
        if session.page <= 1:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="insight_search_first_page",
            )
        return session.history[session.page - 2], session.page - 1
    if not session.next_index:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="insight_search_last_page",
        )
    return session.next_index, session.page + 1


async def async_handle_search_ai_alarms(hass: HomeAssistant, call: ServiceCall) -> None:
    days = int(call.data.get("days") or DEFAULT_INSIGHT_LOOKBACK_DAYS)
    entry = _resolve_config_entry(hass, call)
    device = _resolve_workbench_device(hass, entry, call)
    channel = device.channel_id
    if channel is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="insight_workbench_no_device",
        )
    runtime = get_runtime_data(entry)
    assert runtime is not None
    direction = _page_direction(call)
    session = runtime.search_session
    if direction:
        if (
            not isinstance(session, _SearchSession)
            or session.device_id != device.device_id
            or session.channel != str(channel)
        ):
            session = None
        cursor, page = _page_target(session, direction)
        assert session is not None
        keyword = session.keyword
        begin, end = session.begin, session.end
    else:
        keyword = _search_keyword(hass, call)
        if not keyword:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="insight_search_keyword_required",
            )
        begin, end, _, _ = _time_window(days)
        cursor, page = None, 1
        session = None
    try:
        delegate = runtime.coordinator.device_manager.delegate
        raw = await delegate.async_search_ai_alarm_message(
            device.device_id,
            str(channel),
            search_content=keyword,
            begin_time=begin,
            end_time=end,
            page_size=_SEARCH_PAGE_SIZE,
            next_index_id=cursor,
            product_id=device.product_id,
        )
    except ImouException as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="insight_search_failed",
            translation_placeholders={"error": err.message or str(err)},
        ) from err
    next_index, hits = parse_search_ai_alarms(raw)
    alarm_ids = tuple(hit.alarm_id or "" for hit in hits)
    if direction == "next" and session is not None and (
        not hits or alarm_ids == session.alarm_ids
    ):
        session.next_index = None
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="insight_search_last_page",
        )
    shown = [(hit, None) for hit in hits[:_SEARCH_PAGE_SIZE]]
    summary, markdown = search_card(keyword, shown, page=page)
    search_sensor = runtime.workbench_search_sensor
    if search_sensor is not None:
        search_sensor.set_markdown(summary, markdown)
    history = list(session.history) if session is not None else []
    while len(history) < page:
        history.append(None)
    history[page - 1] = cursor
    if direction == "previous" and page < len(history):
        stored_next = history[page]
    else:
        stored_next = None
        history = history[:page]
    runtime.search_session = _SearchSession(
        device_id=device.device_id,
        channel=str(channel),
        product_id=device.product_id,
        keyword=keyword,
        begin=begin,
        end=end,
        page=page,
        history=history,
        next_index=stored_next or _usable_next_index(next_index),
        alarm_ids=alarm_ids,
    )
    runtime.search_revision += 1
    revision = runtime.search_revision
    if any(hit.thumb_url or hit.picture_urls for hit, _image in shown):
        hass.async_create_task(
            _attach_search_pictures(
                hass, entry, runtime, device, keyword, shown, revision, page
            )
        )
    _LOGGER.debug(
        "AI search %s ch%s keyword=%r page=%s hits=%s",
        device.device_id,
        channel,
        keyword,
        page,
        len(hits),
    )


def _briefing_stat_days(call: ServiceCall) -> tuple[str, str]:
    """Resolve YYYYMMDD start/end for a single calendar day."""
    end_raw = str(call.data.get("end_date") or "").strip()
    if end_raw:
        try:
            end_dt = datetime.strptime(end_raw[:10], "%Y-%m-%d")
        except ValueError as err:
            msg = f"Invalid end_date: {end_raw!r}"
            raise ServiceValidationError(msg) from err
        day = end_dt.strftime("%Y%m%d")
        return day, day
    today = datetime.now(UTC).replace(tzinfo=None).strftime("%Y%m%d")
    return today, today


async def async_handle_refresh_briefing(hass: HomeAssistant, call: ServiceCall) -> None:
    entry = _resolve_config_entry(hass, call)
    device = _resolve_workbench_device(hass, entry, call)
    channel = device.channel_id
    if channel is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="insight_workbench_no_device",
        )
    start_day, end_day = _briefing_stat_days(call)
    runtime = get_runtime_data(entry)
    assert runtime is not None
    try:
        delegate = runtime.coordinator.device_manager.delegate
        raw = await delegate.async_get_alarm_message_statistics(
            device.device_id,
            str(channel),
            start_day=start_day,
            end_day=end_day,
            product_id=device.product_id,
            ai_statistics_analysis_enable=True,
        )
    except ImouException as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="insight_briefing_failed",
            translation_placeholders={"error": err.message or str(err)},
        ) from err
    briefing = parse_alarm_message_statistics(raw)
    chart_dir = Path(hass.config.path("www", "imou"))
    summary, markdown = await hass.async_add_executor_job(
        briefing_card, chart_dir, entry.entry_id, briefing
    )
    briefing_sensor = runtime.workbench_briefing_sensor
    if briefing_sensor is not None:
        briefing_sensor.set_briefing(summary, markdown)


_MESSAGE_ICONS = (
    (("human", "person", "people", "header", "aiper"), "🚶"),
    (("vehicle", "car", "veh"), "🚗"),
    (("pet", "animal", "dog", "cat"), "🐾"),
    (("motion", "videomotion"), "📹"),
    (("sound", "audio"), "🔊"),
    (("door", "bell", "call"), "🚪"),
    (("package", "parcel"), "📦"),
)


def _message_icon(hit: InsightAlarmHit) -> str:
    blob = " ".join([hit.alarm_type or "", *hit.ai_tags]).lower()
    for keys, icon in _MESSAGE_ICONS:
        if any(key in blob for key in keys):
            return icon
    return "🔔"


def _md(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("*", "\\*")
        .replace("_", "\\_")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("\n", " ")
    )


async def _attach_search_pictures(
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    runtime: ImouRuntimeData,
    device: ImouHaDevice,
    keyword: str,
    shown: list[tuple[InsightAlarmHit, str | None]],
    revision: int,
    page: int,
) -> None:
    """Fill search pictures after the text results are already on the card."""
    updated: list[tuple[InsightAlarmHit, str | None]] = []
    any_image = False
    for hit, _image in shown:
        if runtime.search_revision != revision:
            return
        image = await _decrypt_search_picture(hass, entry, runtime, device, hit)
        if image:
            any_image = True
        updated.append((hit, image))
    if not any_image or runtime.search_revision != revision:
        return
    summary, markdown = search_card(keyword, updated, page=page)
    search_sensor = runtime.workbench_search_sensor
    if search_sensor is not None and runtime.search_revision == revision:
        search_sensor.set_markdown(summary, markdown)


async def _decrypt_search_picture(
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    runtime: ImouRuntimeData,
    device: ImouHaDevice,
    hit: InsightAlarmHit,
) -> str | None:
    """Decrypt one search still. Returns a /local/ URL, or None."""
    raw: dict[str, Any] = {}
    if hit.thumb_url:
        raw["thumbUrl"] = hit.thumb_url
    if hit.picture_urls:
        raw["picUrlArray"] = list(hit.picture_urls)
    if not raw:
        return None
    return await async_maybe_decrypt_thumbnail(
        hass,
        entry,
        runtime,
        {
            "device_id": device.device_id,
            "channel_id": device.channel_id,
            "product_id": device.product_id,
            "alarm_id": hit.alarm_id,
            "raw": raw,
        },
        ignore_switch=True,
        retry=False,
    )


def search_card(
    keyword: str,
    rows: list[tuple[InsightAlarmHit, str | None]],
    *,
    page: int = 1,
) -> tuple[str, str]:
    """Return (entity summary, card markdown) for one search page."""
    if not rows:
        text = f"没有找到和「{keyword}」相关的告警。"
        return text, text
    blocks: list[str] = [f"第 {page} 页"]
    for hit, image in rows[:_SEARCH_PAGE_SIZE]:
        title = hit.ai_copywriting or hit.begin_time or ""
        parts = [f"{_message_icon(hit)} **{_md(title)}**"]
        if image:
            parts.append(f"![{_md(title)}]({image})")
        if hit.begin_time and hit.begin_time != title:
            parts.append(hit.begin_time)
        blocks.append("\n\n".join(parts))
    summary = rows[0][0].ai_copywriting or f"{len(rows)} 条"
    return summary[:255], "\n\n---\n\n".join(blocks)


def _resolve_config_entry(hass: HomeAssistant, call: ServiceCall) -> ImouConfigEntry:
    """Pick the account this call belongs to.

    One loaded account is used as-is. With several accounts, the targeted
    entity decides which one, because each account has its own workbench.
    """
    entries = hass.config_entries.async_entries(DOMAIN)
    entity_id = _targeted_entity_id(call)
    if entity_id:
        row = er.async_get(hass).async_get(entity_id)
        if row is not None and row.config_entry_id:
            match = next(
                (entry for entry in entries if entry.entry_id == row.config_entry_id),
                None,
            )
            if match is not None:
                return match
    if len(entries) == 1:
        return entries[0]
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="insight_workbench_entry_required",
    )


def async_register_insight_workbench_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_SEARCH_AI_ALARMS):
        return

    async def _search(call: ServiceCall) -> None:
        await async_handle_search_ai_alarms(hass, call)

    async def _briefing(call: ServiceCall) -> None:
        await async_handle_refresh_briefing(hass, call)

    hass.services.async_register(DOMAIN, SERVICE_SEARCH_AI_ALARMS, _search)
    hass.services.async_register(DOMAIN, SERVICE_REFRESH_AI_INSIGHT_BRIEFING, _briefing)
