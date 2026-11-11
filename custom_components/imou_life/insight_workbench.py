"""AI Insight workbench services."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pyimouapi.exceptions import ImouException
from pyimouapi.ha_device import ImouHaDevice
from pyimouapi.insight.search import (
    format_search_results_markdown,
    parse_search_ai_alarms,
)
from pyimouapi.insight.statistics import (
    format_briefing_markdown,
    parse_alarm_message_statistics,
)

from .const import (
    DEFAULT_INSIGHT_LOOKBACK_DAYS,
    DOMAIN,
    PARAM_INSIGHT_WORKBENCH_DEVICE,
    SERVICE_REFRESH_AI_INSIGHT_BRIEFING,
    SERVICE_SEARCH_AI_ALARMS,
)
from .coordinator import ImouConfigEntry
from .runtime_data import get_runtime_data

_LOGGER = logging.getLogger(__name__)


def _device_for_key(coordinator, key: str) -> ImouHaDevice | None:
    return coordinator.devices_by_key.get(key)


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

    entity_id: str | None = None
    target = getattr(call, "target", None)
    if isinstance(target, dict) and target.get("entity_id"):
        ids = target["entity_id"]
        entity_id = ids[0] if isinstance(ids, list) else str(ids)

    key: str | None = None
    if entity_id:
        row = er.async_get(hass).async_get(entity_id)
        if (
            row is not None
            and row.unique_id
            and row.unique_id.endswith(f"${PARAM_INSIGHT_WORKBENCH_DEVICE}")
        ):
            key = runtime.workbench_device_key
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


def _time_window(days: int) -> tuple[str, str, str, str]:
    days = max(1, min(days, 31))
    end = datetime.now(UTC).replace(tzinfo=None)
    begin = end - timedelta(days=days)
    search_begin = begin.strftime("%Y-%m-%d %H:%M:%S")
    search_end = end.strftime("%Y-%m-%d %H:%M:%S")
    stat_start = begin.strftime("%Y%m%d")
    stat_end = end.strftime("%Y%m%d")
    return search_begin, search_end, stat_start, stat_end


async def async_handle_search_ai_alarms(hass: HomeAssistant, call: ServiceCall) -> None:
    keyword = str(call.data.get("keyword") or "").strip()
    if not keyword:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="insight_search_keyword_required",
        )
    days = int(call.data.get("days") or DEFAULT_INSIGHT_LOOKBACK_DAYS)
    entry = _resolve_config_entry(hass, call)
    device = _resolve_workbench_device(hass, entry, call)
    channel = device.channel_id
    if channel is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="insight_workbench_no_device",
        )
    begin, end, _, _ = _time_window(days)
    runtime = get_runtime_data(entry)
    assert runtime is not None
    try:
        delegate = runtime.coordinator.device_manager.delegate
        raw = await delegate.async_search_ai_alarm_message(
            device.device_id,
            str(channel),
            search_content=keyword,
            begin_time=begin,
            end_time=end,
            product_id=device.product_id,
        )
    except ImouException as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="insight_search_failed",
            translation_placeholders={"error": err.message or str(err)},
        ) from err
    _, hits = parse_search_ai_alarms(raw)
    markdown = format_search_results_markdown(hits, keyword=keyword)
    search_sensor = runtime.workbench_search_sensor
    if search_sensor is not None:
        search_sensor.set_markdown(markdown)
    _LOGGER.debug(
        "AI search %s ch%s keyword=%r hits=%s",
        device.device_id,
        channel,
        keyword,
        len(hits),
    )


async def async_handle_refresh_briefing(hass: HomeAssistant, call: ServiceCall) -> None:
    days = int(call.data.get("days") or DEFAULT_INSIGHT_LOOKBACK_DAYS)
    entry = _resolve_config_entry(hass, call)
    device = _resolve_workbench_device(hass, entry, call)
    channel = device.channel_id
    if channel is None:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="insight_workbench_no_device",
        )
    _, _, start_day, end_day = _time_window(days)
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
    markdown = format_briefing_markdown(briefing)
    briefing_sensor = runtime.workbench_briefing_sensor
    if briefing_sensor is not None:
        briefing_sensor.set_markdown(markdown)


def _resolve_config_entry(hass: HomeAssistant, call: ServiceCall) -> ImouConfigEntry:
    if len(hass.config_entries.async_entries(DOMAIN)) == 1:
        return hass.config_entries.async_entries(DOMAIN)[0]
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
    hass.services.async_register(
        DOMAIN, SERVICE_REFRESH_AI_INSIGHT_BRIEFING, _briefing
    )
