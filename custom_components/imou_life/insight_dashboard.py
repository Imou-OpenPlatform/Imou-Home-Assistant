"""Sidebar dashboard for AI Insight, installed with the integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from homeassistant.components.lovelace.const import CONF_URL_PATH, LOVELACE_DATA
from homeassistant.components.lovelace.dashboard import LovelaceStorage
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_ICON,
    CONF_ID,
    CONF_NAME,
    EVENT_STATE_CHANGED,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import translation
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    PARAM_AI_INSIGHT_TRIAL,
    PARAM_INSIGHT_BRIEFING,
    PARAM_INSIGHT_SEARCH_RESULT,
    PARAM_INSIGHT_WORKBENCH_DEVICE,
    PARAM_TRIAL_CONTACT_URL,
    SERVICE_REFRESH_AI_INSIGHT_BRIEFING,
)
from .coordinator import ImouConfigEntry
from .helpers import resolve_ui_language, selector_option_label

_LOGGER = logging.getLogger(__name__)

DASHBOARD_URL = "imou-zhijian"
DASHBOARD_ICON = "mdi:creation"
DATE_ENTITY_ID = "input_datetime.imou_insight_briefing_end"
KEYWORD_ENTITY_ID = "input_text.imou_insight_search_keyword"
_LISTENER_FLAG = "insight_dashboard_listener"
_DATE_ENTITY_KEY = "insight_dashboard_date_entity"
_KEYWORD_ENTITY_KEY = "insight_dashboard_keyword_entity"

_COPY_DEFAULTS = {
    "title": "AI Insight",
    "view_briefing": "Briefing",
    "view_search": "Search",
    "view_trial": "Trial",
    "device": "Camera",
    "date": "Date",
    "keyword": "Keyword",
    "search": "Search",
    "previous": "Previous",
    "next": "Next",
    "briefing": "Daily briefing",
    "results": "Results",
    "trial_button": "AI Insight trial",
    "empty": "No camera on AI Insight yet.",
    "trial_markdown": (
        "## ✨ AI Insight tells you what the cameras saw\n\n"
        "🌅 **A briefing for the day you pick**\n"
        "Choose a date. People, vehicles, pets, packages, and the day total line up "
        "on one chart, so the busy kind of moment stands out.\n\n"
        "🔍 **Find an alarm in one sentence**\n"
        "No need to scrub the timeline. Type what you remember. Matching "
        "alarms show up with their time and picture.\n\n"
        "🚶 🚗 🐾 **A different icon for each kind of moment**\n"
        "A person, a vehicle, and a pet each get their own mark. The title "
        "is the sentence saved with that alarm."
    ),
    "trial_intro": (
        "AI Insight adds smart search and a daily briefing. "
        "Add the official WeChat or WhatsApp below to start a trial."
    ),
    "wechat": "17799860785",
    "whatsapp": "+86 19106884129",
}


@dataclass(frozen=True, slots=True)
class InsightDashboardAccount:
    """Entities from one account that the dashboard can show."""

    title: str
    select_entity: str | None
    briefing_entity: str | None
    search_entity: str | None
    trial_entity: str | None
    trial_note: str = ""


def keyword_entity_id(hass: HomeAssistant) -> str:
    """Text helper the search page reads when a call omits ``keyword``."""
    data = hass.data.get(DOMAIN) or {}
    stored = data.get(_KEYWORD_ENTITY_KEY)
    return stored if isinstance(stored, str) and stored else KEYWORD_ENTITY_ID


def _search_button(
    name: str, icon: str, select_entity: str, data: dict[str, Any]
) -> dict[str, Any]:
    """One search-page button. The page flag is read by the service."""
    return {
        "type": "button",
        "name": name,
        "icon": icon,
        "icon_height": "24px",
        "tap_action": {
            "action": "call-service",
            "service": f"{DOMAIN}.search_ai_alarms",
            "target": {"entity_id": select_entity},
            "data": data,
        },
    }


def build_insight_dashboard(
    accounts: list[InsightDashboardAccount],
    *,
    date_entity: str,
    keyword_entity: str,
    copy: Mapping[str, str],
) -> dict[str, Any]:
    """Return the Lovelace config for the sidebar dashboard."""
    ready = [account for account in accounts if account.select_entity]
    single = len(ready) == 1
    briefing_rows: list[dict[str, Any]] = []
    search_rows: list[dict[str, Any]] = []
    search_actions: list[dict[str, Any]] = []
    briefing_cards: list[dict[str, Any]] = []
    search_cards: list[dict[str, Any]] = []
    for account in ready:
        device_name = copy["device"] if single else account.title
        briefing_rows.append({"entity": account.select_entity, "name": device_name})
        search_rows.append({"entity": account.select_entity, "name": device_name})
        button_name = copy["search"]
        if not single:
            button_name = f"{button_name} · {account.title}"
        if not single:
            previous_name = f"{copy['previous']} · {account.title}"
            next_name = f"{copy['next']} · {account.title}"
        else:
            previous_name = copy["previous"]
            next_name = copy["next"]
        search_actions.append(
            {
                "type": "horizontal-stack",
                "cards": [
                    _search_button(
                        previous_name,
                        "mdi:chevron-left",
                        account.select_entity,
                        {"page": "previous"},
                    ),
                    _search_button(
                        button_name,
                        "mdi:magnify",
                        account.select_entity,
                        {"days": 7},
                    ),
                    _search_button(
                        next_name,
                        "mdi:chevron-right",
                        account.select_entity,
                        {"page": "next"},
                    ),
                ],
            }
        )
        briefing_title = copy["briefing"]
        results_title = copy["results"]
        if not single:
            briefing_title = f"{briefing_title} · {account.title}"
            results_title = f"{results_title} · {account.title}"
        if account.briefing_entity:
            briefing_cards.append(
                _markdown_card(briefing_title, account.briefing_entity)
            )
        if account.search_entity:
            search_cards.append(_markdown_card(results_title, account.search_entity))
    if ready:
        briefing_rows.append({"entity": date_entity, "name": copy["date"]})
        search_rows.append({"entity": keyword_entity, "name": copy["keyword"]})
    else:
        briefing_cards.append({"type": "markdown", "content": copy["empty"]})
        search_cards.append({"type": "markdown", "content": copy["empty"]})

    trial_notes: list[str] = []
    seen_contacts: set[str] = set()
    for account in accounts:
        note = account.trial_note.strip()
        if not note or note in seen_contacts:
            continue
        seen_contacts.add(note)
        trial_notes.append(note)

    trial_markdown = copy["trial_markdown"].rstrip()
    intro = copy.get("trial_intro", "").strip()
    contacts = (
        f"💬 **微信** {copy.get('wechat', '17799860785')}\n\n"
        f"📱 **WhatsApp** {copy.get('whatsapp', '+86 19106884129')}"
    )
    extra = [note for note in trial_notes if note not in contacts]
    tail = "\n\n".join(part for part in (intro, contacts, *extra) if part)
    if tail:
        trial_markdown = f"{trial_markdown}\n\n---\n\n{tail}"

    views = [
        {
            "title": copy["view_briefing"],
            "path": "briefing",
            "cards": _entities_then(
                copy["view_briefing"], briefing_rows, briefing_cards
            ),
        },
        {
            "title": copy["view_search"],
            "path": "search",
            "cards": _search_view(
                copy["view_search"], search_rows, search_actions, search_cards
            ),
        },
        {
            "title": copy["view_trial"],
            "path": "trial",
            "cards": [{"type": "markdown", "content": trial_markdown}],
        },
    ]
    return {"views": views}


async def async_setup_insight_dashboard(
    hass: HomeAssistant, entry: ImouConfigEntry
) -> None:
    """Create the sidebar dashboard and refresh a briefing when the date changes."""
    try:
        await _ensure_listener(hass)
        date_entity, keyword_entity = await _ensure_helpers(hass)
        await _publish(hass, date_entity, keyword_entity)
        if _date_value(hass, date_entity):
            await _refresh_entry(hass, entry, _date_value(hass, date_entity))
            return
        await hass.services.async_call(
            "input_datetime",
            "set_datetime",
            {"entity_id": date_entity, "date": dt_util.now().date()},
            blocking=True,
        )
    except Exception:
        _LOGGER.exception("Unable to install the AI Insight dashboard")


async def async_unload_insight_dashboard(
    hass: HomeAssistant, entry: ImouConfigEntry
) -> None:
    """Drop this account from the dashboard, or remove it with the last account."""
    others = [
        candidate
        for candidate in hass.config_entries.async_entries(DOMAIN)
        if candidate.entry_id != entry.entry_id
        and candidate.state
        in (ConfigEntryState.LOADED, ConfigEntryState.SETUP_IN_PROGRESS)
    ]
    try:
        if others:
            data = hass.data.get(DOMAIN) or {}
            await _publish(
                hass,
                _stored_entity(data, _DATE_ENTITY_KEY, DATE_ENTITY_ID),
                _stored_entity(data, _KEYWORD_ENTITY_KEY, KEYWORD_ENTITY_ID),
            )
            return
        await _remove_dashboard(hass)
        data = hass.data.get(DOMAIN) or {}
        unsub = data.pop(_LISTENER_FLAG, None)
        if unsub is not None:
            unsub()
    except Exception:
        _LOGGER.exception("Unable to update the AI Insight dashboard")


def _markdown_card(title: str, entity_id: str) -> dict[str, Any]:
    return {
        "type": "markdown",
        "title": title,
        "content": (
            f"{{{{ state_attr('{entity_id}', 'markdown') or states('{entity_id}') }}}}"
        ),
    }


def _search_view(
    title: str,
    rows: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    cards: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Device and keyword stay in a list. Search and paging are real buttons."""
    if not rows:
        return cards
    return [
        {
            "type": "vertical-stack",
            "cards": [
                {"type": "entities", "title": title, "entities": rows},
                *actions,
            ],
        },
        *cards,
    ]


def _entities_then(
    title: str, rows: list[dict[str, Any]], cards: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if not rows:
        return cards
    return [{"type": "entities", "title": title, "entities": rows}, *cards]


def _stored_entity(data: Mapping[str, Any], key: str, fallback: str) -> str:
    stored = data.get(key)
    return stored if isinstance(stored, str) and stored else fallback


def _date_value(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    if state is None:
        return ""
    value = state.state.strip()
    if value in ("unknown", "unavailable", ""):
        return ""
    return value[:10]


async def _dashboard_accounts(hass: HomeAssistant) -> list[InsightDashboardAccount]:
    """Accounts for the dashboard. A custom trial link is kept under the contacts."""
    accounts: list[InsightDashboardAccount] = []
    for account, entry in zip(_accounts(hass), _loaded_entries(hass), strict=True):
        url = str(entry.options.get(PARAM_TRIAL_CONTACT_URL) or "").strip()
        accounts.append(replace(account, trial_note=url))
    return accounts


def _loaded_entries(hass: HomeAssistant) -> list[ImouConfigEntry]:
    return [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state
        in (ConfigEntryState.LOADED, ConfigEntryState.SETUP_IN_PROGRESS)
    ]


def _accounts(hass: HomeAssistant) -> list[InsightDashboardAccount]:
    registry = er.async_get(hass)
    accounts: list[InsightDashboardAccount] = []
    for entry in _loaded_entries(hass):
        accounts.append(
            InsightDashboardAccount(
                title=entry.title or "Imou Life",
                select_entity=_entity_id(
                    registry, "select", entry.entry_id, PARAM_INSIGHT_WORKBENCH_DEVICE
                ),
                briefing_entity=_entity_id(
                    registry, "sensor", entry.entry_id, PARAM_INSIGHT_BRIEFING
                ),
                search_entity=_entity_id(
                    registry, "sensor", entry.entry_id, PARAM_INSIGHT_SEARCH_RESULT
                ),
                trial_entity=_entity_id(
                    registry, "button", entry.entry_id, PARAM_AI_INSIGHT_TRIAL
                ),
            )
        )
    return accounts


def _entity_id(
    registry: er.EntityRegistry, domain: str, entry_id: str, entity_type: str
) -> str | None:
    return registry.async_get_entity_id(domain, DOMAIN, f"{entry_id}${entity_type}")


async def _copy(hass: HomeAssistant) -> dict[str, str]:
    language = resolve_ui_language(hass.config.language)
    await translation.async_get_translations(
        hass, language, "selector", integrations={DOMAIN}
    )
    return {
        key: selector_option_label(
            hass, language, "insight_dashboard", key, fallback
        )
        for key, fallback in _COPY_DEFAULTS.items()
    }


async def _ensure_listener(hass: HomeAssistant) -> None:
    data = hass.data.setdefault(DOMAIN, {})
    if data.get(_LISTENER_FLAG):
        return

    @callback
    def _on_state(event: Event) -> None:
        entity_id = event.data.get(ATTR_ENTITY_ID)
        old = event.data.get("old_state")
        new = event.data.get("new_state")
        if old is None or new is None or old.state == new.state:
            return
        if not isinstance(entity_id, str):
            return
        stored_date = _stored_entity(data, _DATE_ENTITY_KEY, DATE_ENTITY_ID)
        if entity_id == stored_date:
            hass.async_create_task(_refresh_loaded(hass, new.state), eager_start=True)
            return
        if not entity_id.startswith("select."):
            return
        row = er.async_get(hass).async_get(entity_id)
        if (
            row is None
            or row.platform != DOMAIN
            or not row.unique_id
            or not row.unique_id.endswith(f"${PARAM_INSIGHT_WORKBENCH_DEVICE}")
        ):
            return
        hass.async_create_task(
            _refresh_select(hass, entity_id, _date_value(hass, stored_date)),
            eager_start=True,
        )

    data[_LISTENER_FLAG] = hass.bus.async_listen(EVENT_STATE_CHANGED, _on_state)


async def _ensure_helpers(hass: HomeAssistant) -> tuple[str, str]:
    await async_setup_component(hass, "input_datetime", {})
    await async_setup_component(hass, "input_text", {})
    date_entity = await _ensure_date_helper(hass)
    keyword_entity = await _ensure_keyword_helper(hass)
    data = hass.data.setdefault(DOMAIN, {})
    data[_DATE_ENTITY_KEY] = date_entity
    data[_KEYWORD_ENTITY_KEY] = keyword_entity
    return date_entity, keyword_entity


async def _ensure_date_helper(hass: HomeAssistant) -> str:
    if hass.states.get(DATE_ENTITY_ID) is not None:
        return DATE_ENTITY_ID
    collection = _storage_collection(hass, "input_datetime/create")
    if collection is None:
        return DATE_ENTITY_ID
    for item in collection.async_items():
        if item.get(CONF_NAME) in ("智见简报日期", "imou_insight_briefing_end"):
            return f"input_datetime.{item[CONF_ID]}"
    item = await collection.async_create_item(
        {"name": "智见简报日期", "has_date": True, "has_time": False}
    )
    return f"input_datetime.{item[CONF_ID]}"


async def _ensure_keyword_helper(hass: HomeAssistant) -> str:
    if hass.states.get(KEYWORD_ENTITY_ID) is not None:
        return KEYWORD_ENTITY_ID
    collection = _storage_collection(hass, "input_text/create")
    if collection is None:
        return KEYWORD_ENTITY_ID
    for item in collection.async_items():
        if item.get(CONF_NAME) in ("智见搜索词", "imou_insight_search_keyword"):
            return f"input_text.{item[CONF_ID]}"
    item = await collection.async_create_item(
        {"name": "智见搜索词", "min": 0, "max": 64}
    )
    return f"input_text.{item[CONF_ID]}"


async def _publish(hass: HomeAssistant, date_entity: str, keyword_entity: str) -> None:
    await async_setup_component(hass, "lovelace", {})
    collection = _storage_collection(hass, "lovelace/dashboards/create")
    if collection is None:
        _LOGGER.debug("Lovelace dashboards are not ready")
        return
    copy = await _copy(hass)
    config = build_insight_dashboard(
        await _dashboard_accounts(hass),
        date_entity=date_entity,
        keyword_entity=keyword_entity,
        copy=copy,
    )
    existing = next(
        (
            item
            for item in collection.async_items()
            if item.get(CONF_URL_PATH) == DASHBOARD_URL
        ),
        None,
    )
    if existing is None:
        await collection.async_create_item(
            {
                CONF_URL_PATH: DASHBOARD_URL,
                "title": copy["title"],
                CONF_ICON: DASHBOARD_ICON,
                "show_in_sidebar": True,
            }
        )
    elif existing.get("title") != copy["title"] or existing.get(
        CONF_ICON
    ) != DASHBOARD_ICON:
        await collection.async_update_item(
            existing[CONF_ID],
            {"title": copy["title"], CONF_ICON: DASHBOARD_ICON},
        )
    store = hass.data[LOVELACE_DATA].dashboards.get(DASHBOARD_URL)
    if not isinstance(store, LovelaceStorage):
        _LOGGER.debug("AI Insight dashboard is not a storage dashboard")
        return
    try:
        current = await store.async_load(False)
    except HomeAssistantError:
        current = None
    if current != config:
        await store.async_save(config)


async def _remove_dashboard(hass: HomeAssistant) -> None:
    collection = _storage_collection(hass, "lovelace/dashboards/create")
    if collection is None:
        return
    existing = next(
        (
            item
            for item in collection.async_items()
            if item.get(CONF_URL_PATH) == DASHBOARD_URL
        ),
        None,
    )
    if existing is not None:
        await collection.async_delete_item(existing[CONF_ID])


async def _refresh_entry(
    hass: HomeAssistant, entry: ImouConfigEntry, end_date: str
) -> None:
    select_entity = _entity_id(
        er.async_get(hass),
        "select",
        entry.entry_id,
        PARAM_INSIGHT_WORKBENCH_DEVICE,
    )
    if select_entity:
        await _refresh_select(hass, select_entity, end_date)


async def _refresh_loaded(hass: HomeAssistant, end_date: str) -> None:
    day = str(end_date or "").strip()[:10]
    for select_entity in _select_entities(hass):
        await _refresh_select(hass, select_entity, day)


def _select_entities(hass: HomeAssistant) -> list[str]:
    return [
        account.select_entity
        for account in _accounts(hass)
        if account.select_entity is not None
    ]


async def _refresh_select(hass: HomeAssistant, entity_id: str, end_date: str) -> None:
    day = str(end_date or "").strip()[:10]
    if len(day) < 10 or not hass.services.has_service(
        DOMAIN, SERVICE_REFRESH_AI_INSIGHT_BRIEFING
    ):
        return
    try:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_REFRESH_AI_INSIGHT_BRIEFING,
            {"entity_id": entity_id, "end_date": day},
            blocking=True,
        )
    except HomeAssistantError as err:
        _LOGGER.debug("Briefing refresh skipped: %s", err)


def _storage_collection(hass: HomeAssistant, command: str) -> Any:
    """Return the live storage collection behind a websocket create command."""
    registered = hass.data.get("websocket_api", {}).get(command)
    if not registered:
        return None
    func = registered[0]
    seen: set[int] = set()
    while id(func) not in seen and hasattr(func, "__wrapped__"):
        seen.add(id(func))
        func = func.__wrapped__  # type: ignore[attr-defined]
    owner = getattr(func, "__self__", None)
    return getattr(owner, "storage_collection", None)
