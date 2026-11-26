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
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
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
from .helpers import (
    async_display_language,
    selector_option_label,
    translated_name_override,
)

_LOGGER = logging.getLogger(__name__)

DASHBOARD_URL = "imou-zhijian"
DASHBOARD_ICON = "mdi:creation"
DATE_ENTITY_ID = "input_datetime.imou_insight_briefing_end"
KEYWORD_ENTITY_ID = "input_text.imou_insight_search_keyword"
SEARCH_BEGIN_ENTITY_ID = "input_datetime.imou_insight_search_begin"
SEARCH_END_ENTITY_ID = "input_datetime.imou_insight_search_end"
ACCOUNT_ENTITY_ID = "input_select.imou_insight_account"
_LISTENER_FLAG = "insight_dashboard_listener"
_LANGUAGE_FLAG = "insight_dashboard_language"
_DATE_ENTITY_KEY = "insight_dashboard_date_entity"
_KEYWORD_ENTITY_KEY = "insight_dashboard_keyword_entity"
_SEARCH_BEGIN_ENTITY_KEY = "insight_dashboard_search_begin_entity"
_SEARCH_END_ENTITY_KEY = "insight_dashboard_search_end_entity"
_ACCOUNT_ENTITY_KEY = "insight_dashboard_account_entity"

_COPY_DEFAULTS = {
    "title": "AI Insight",
    "view_briefing": "AI Briefing",
    "view_search": "Smart Messages",
    "view_trial": "Request a trial",
    "device": "Camera",
    "account": "Account",
    "account_name": "Insight account",
    "date": "Date",
    "keyword": "Search text",
    "search_begin": "Start date",
    "search_end": "End date",
    "date_name": "Briefing date",
    "keyword_name": "Search text",
    "search_begin_name": "Start date",
    "search_end_name": "End date",
    "search_alarms": "Search alarms",
    "refresh_briefing": "Refresh the briefing",
    "morning_refresh": "Refresh the briefing each morning",
    "search": "Search",
    "previous": "Previous",
    "next": "Next",
    "briefing": "Daily briefing",
    "results": "Results",
    "trial_button": "AI Insight trial",
    "empty": "No camera is using AI Insight yet.",
    "trial_markdown": (
        "## ✨ AI Insight tells you what the cameras saw\n\n"
        "🌅 **A briefing for the day you pick**\n"
        "Choose a date. People, vehicles, pets, packages, and the day's total "
        "appear on one chart.\n\n"
        "🔍 **Find an alarm in one sentence**\n"
        "Type what you remember. Matching alarms show up with their time and "
        "picture.\n\n"
        "🚶 🚗 🐾 **Each kind of moment has its own icon**\n"
        "A person, a vehicle, a pet, and a package each get a mark. The title "
        "is the sentence saved with that alarm."
    ),
    "trial_intro": (
        "AI Insight adds smart search and a daily briefing. "
        "Add the official WeChat or WhatsApp below to start a trial."
    ),
    "wechat": "17799860785",
    "wechat_label": "WeChat",
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
    entry_id: str = ""


def keyword_entity_id(hass: HomeAssistant) -> str:
    """Text helper the search page reads when a call omits ``keyword``."""
    data = hass.data.get(DOMAIN) or {}
    stored = data.get(_KEYWORD_ENTITY_KEY)
    return stored if isinstance(stored, str) and stored else KEYWORD_ENTITY_ID


def search_date_entity_ids(hass: HomeAssistant) -> tuple[str, str]:
    """Start and end date helpers the search page reads."""
    data = hass.data.get(DOMAIN) or {}
    return (
        _stored_entity(data, _SEARCH_BEGIN_ENTITY_KEY, SEARCH_BEGIN_ENTITY_ID),
        _stored_entity(data, _SEARCH_END_ENTITY_KEY, SEARCH_END_ENTITY_ID),
    )


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


def account_option_labels(accounts: list[InsightDashboardAccount]) -> list[str]:
    """Names for the account picker. A repeated title keeps a short id."""
    titles = [account.title for account in accounts]
    labels: list[str] = []
    for account in accounts:
        title = account.title
        if titles.count(title) > 1 and account.entry_id:
            title = f"{title} ({account.entry_id[:6]})"
        labels.append(title)
    return labels


def _sectioned_control(label: str, row: dict[str, Any]) -> list[dict[str, Any]]:
    """Put the label on its own row so the control can use the full width."""
    control = {**row, "name": " "}
    return [{"type": "section", "label": label}, control]


def build_insight_dashboard(
    accounts: list[InsightDashboardAccount],
    *,
    date_entity: str,
    keyword_entity: str,
    search_begin_entity: str,
    search_end_entity: str,
    copy: Mapping[str, str],
    account_entity: str = "",
) -> dict[str, Any]:
    """Return the Lovelace config for the sidebar dashboard."""
    ready = [account for account in accounts if account.select_entity]
    single = len(ready) <= 1 or not account_entity
    labels = account_option_labels(ready)
    briefing_rows: list[dict[str, Any]] = []
    search_rows: list[dict[str, Any]] = []
    search_actions: list[dict[str, Any]] = []
    briefing_cards: list[dict[str, Any]] = []
    search_cards: list[dict[str, Any]] = []
    briefing_devices: list[dict[str, Any]] = []
    search_devices: list[dict[str, Any]] = []
    if not single:
        briefing_rows.extend(
            _sectioned_control(copy["account"], {"entity": account_entity})
        )
        search_rows.extend(
            _sectioned_control(copy["account"], {"entity": account_entity})
        )
    for account, label in zip(ready, labels, strict=True):
        select_entity = account.select_entity
        if select_entity is None:
            continue
        device_row = {"entity": select_entity, "name": " "}
        if single:
            briefing_devices.append(device_row)
            search_devices.append(device_row)
        else:
            briefing_devices.append(
                _device_row_for_account(account_entity, label, device_row)
            )
            search_devices.append(
                _device_row_for_account(account_entity, label, device_row)
            )
        # Cloud search only pages forward; do not offer a Previous control.
        actions = {
            "type": "horizontal-stack",
            "cards": [
                _search_button(copy["search"], "mdi:magnify", select_entity, {}),
                _search_button(
                    copy["next"],
                    "mdi:chevron-right",
                    select_entity,
                    {"page": "next"},
                ),
            ],
        }
        briefing_card = (
            _markdown_card(copy["briefing"], account.briefing_entity)
            if account.briefing_entity
            else None
        )
        search_card = (
            _markdown_card(copy["results"], account.search_entity)
            if account.search_entity
            else None
        )
        if single:
            search_actions.append(actions)
            if briefing_card is not None:
                briefing_cards.append(briefing_card)
            if search_card is not None:
                search_cards.append(search_card)
            continue
        if briefing_card is not None:
            briefing_cards.append(
                _account_conditional(account_entity, label, [briefing_card])
            )
        search_actions.append(_account_conditional(account_entity, label, [actions]))
        if search_card is not None:
            search_cards.append(
                _account_conditional(account_entity, label, [search_card])
            )
    if briefing_devices:
        briefing_rows.append({"type": "section", "label": copy["device"]})
        briefing_rows.extend(briefing_devices)
        search_rows.append({"type": "section", "label": copy["device"]})
        search_rows.extend(search_devices)
    if ready:
        briefing_rows.extend(_sectioned_control(copy["date"], {"entity": date_entity}))
        search_rows.extend(
            _sectioned_control(copy["keyword"], {"entity": keyword_entity})
        )
        search_rows.extend(
            _sectioned_control(copy["search_begin"], {"entity": search_begin_entity})
        )
        search_rows.extend(
            _sectioned_control(copy["search_end"], {"entity": search_end_entity})
        )
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
    wechat_label = copy.get("wechat_label", "WeChat")
    contacts = (
        f"💬 **{wechat_label}** {copy.get('wechat', '17799860785')}\n\n"
        f"📱 **WhatsApp** {copy.get('whatsapp', '+86 19106884129')}"
    )
    extra = [note for note in trial_notes if note not in contacts]
    tail = "\n\n".join(part for part in (intro, contacts, *extra) if part)
    if tail:
        trial_markdown = f"{trial_markdown}\n\n---\n\n{tail}"

    briefing_view = _entities_then(copy["view_briefing"], briefing_rows, briefing_cards)
    search_view = _search_view(
        copy["view_search"], search_rows, search_actions, search_cards
    )
    views = [
        {
            "title": copy["view_briefing"],
            "path": "briefing",
            "cards": briefing_view,
        },
        {
            "title": copy["view_search"],
            "path": "search",
            "cards": search_view,
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
        await _watch_language(hass)
        date_entity, keyword_entity, search_begin, search_end = await _ensure_helpers(
            hass
        )
        await _publish(hass, date_entity, keyword_entity, search_begin, search_end)
        today = dt_util.now().date()
        had_briefing_date = bool(_date_value(hass, date_entity))
        for entity_id in (date_entity, search_begin, search_end):
            if _date_value(hass, entity_id):
                continue
            await hass.services.async_call(
                "input_datetime",
                "set_datetime",
                {"entity_id": entity_id, "date": today},
                blocking=True,
            )
        if had_briefing_date:
            await _refresh_entry(hass, entry, _date_value(hass, date_entity))
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
            await _publish(hass, *_dashboard_inputs(data))
            return
        await _remove_dashboard(hass)
        data = hass.data.get(DOMAIN) or {}
        unsub = data.pop(_LISTENER_FLAG, None)
        if unsub is not None:
            unsub()
        language_unsub = data.pop(_LANGUAGE_FLAG, None)
        if language_unsub is not None:
            language_unsub()
    except Exception:
        _LOGGER.exception("Unable to update the AI Insight dashboard")


def _account_state_conditions(account_entity: str, label: str) -> list[dict[str, str]]:
    return [{"condition": "state", "entity": account_entity, "state": label}]


def _device_row_for_account(
    account_entity: str, label: str, device_row: dict[str, Any]
) -> dict[str, Any]:
    """Keep the camera on the same card as the account; show one at a time."""
    return {
        "type": "conditional",
        "conditions": _account_state_conditions(account_entity, label),
        "row": device_row,
    }


def _account_conditional(
    account_entity: str, label: str, cards: list[dict[str, Any]]
) -> dict[str, Any]:
    """Show this account's buttons or result only while it is selected."""
    card = cards[0] if len(cards) == 1 else {"type": "vertical-stack", "cards": cards}
    return {
        "type": "conditional",
        "conditions": _account_state_conditions(account_entity, label),
        "card": card,
    }


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
    """Conditions stay in one list. Search and paging are real buttons."""
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


def _dashboard_inputs(data: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        _stored_entity(data, _DATE_ENTITY_KEY, DATE_ENTITY_ID),
        _stored_entity(data, _KEYWORD_ENTITY_KEY, KEYWORD_ENTITY_ID),
        _stored_entity(data, _SEARCH_BEGIN_ENTITY_KEY, SEARCH_BEGIN_ENTITY_ID),
        _stored_entity(data, _SEARCH_END_ENTITY_KEY, SEARCH_END_ENTITY_ID),
    )


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
        if entry.state in (ConfigEntryState.LOADED, ConfigEntryState.SETUP_IN_PROGRESS)
    ]


def _accounts(hass: HomeAssistant) -> list[InsightDashboardAccount]:
    registry = er.async_get(hass)
    accounts: list[InsightDashboardAccount] = []
    for entry in _loaded_entries(hass):
        accounts.append(
            InsightDashboardAccount(
                title=entry.title or "Imou Life",
                entry_id=entry.entry_id,
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


_LANGUAGE_ENTITIES = (
    (DATE_ENTITY_ID, "date_name"),
    (KEYWORD_ENTITY_ID, "keyword_name"),
    (SEARCH_BEGIN_ENTITY_ID, "search_begin_name"),
    (SEARCH_END_ENTITY_ID, "search_end_name"),
    ("script.imou_search_insight_alarms", "search_alarms"),
    ("script.imou_refresh_insight_briefing", "refresh_briefing"),
)
_MORNING_AUTOMATION_ID = "imou_daily_insight_briefing"


async def _copy(hass: HomeAssistant) -> dict[str, str]:
    language = await async_display_language(hass)
    await translation.async_get_translations(
        hass, language, "selector", integrations={DOMAIN}
    )
    return {
        key: selector_option_label(hass, language, "insight_dashboard", key, fallback)
        for key, fallback in _COPY_DEFAULTS.items()
    }


async def _ensure_listener(hass: HomeAssistant) -> None:
    data = hass.data.setdefault(DOMAIN, {})
    if data.get(_LISTENER_FLAG):
        return

    @callback
    def _on_state(event: Event[EventStateChangedData]) -> None:
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


async def _watch_language(hass: HomeAssistant) -> None:
    """Republish the dashboard when the system or owner language changes."""
    from homeassistant.const import EVENT_CORE_CONFIG_UPDATE

    data = hass.data.setdefault(DOMAIN, {})
    if data.get(_LANGUAGE_FLAG):
        return

    @callback
    def _on_config(_event: Event[Any]) -> None:
        hass.async_create_task(_republish_current(hass), eager_start=True)

    data[_LANGUAGE_FLAG] = hass.bus.async_listen(EVENT_CORE_CONFIG_UPDATE, _on_config)


async def _republish_current(hass: HomeAssistant) -> None:
    data = hass.data.get(DOMAIN) or {}
    await _publish(hass, *_dashboard_inputs(data))


def _apply_language_names(hass: HomeAssistant, copy: Mapping[str, str]) -> None:
    """Point helper and script names at the current user or system language."""
    registry = er.async_get(hass)
    pairs = list(_LANGUAGE_ENTITIES)
    stored_account = (hass.data.get(DOMAIN) or {}).get(_ACCOUNT_ENTITY_KEY)
    if isinstance(stored_account, str):
        pairs.append((stored_account, "account_name"))
    automation = registry.async_get_entity_id(
        "automation", "automation", _MORNING_AUTOMATION_ID
    )
    if automation is not None:
        pairs.append((automation, "morning_refresh"))
    for entity_id, key in pairs:
        entry = registry.async_get(entity_id)
        translated = copy.get(key)
        if entry is None or not translated:
            continue
        desired = translated_name_override(entry.original_name, translated)
        if entry.name == desired:
            continue
        registry.async_update_entity(entity_id, name=desired)


async def _ensure_helpers(hass: HomeAssistant) -> tuple[str, str, str, str]:
    await async_setup_component(hass, "input_datetime", {})
    await async_setup_component(hass, "input_text", {})
    copy = await _copy(hass)
    date_entity = await _ensure_named_date(
        hass,
        DATE_ENTITY_ID,
        (
            "智见简报日期",
            "Briefing date",
            "imou_insight_briefing_end",
            copy["date_name"],
        ),
        copy["date_name"],
    )
    search_begin = await _ensure_named_date(
        hass,
        SEARCH_BEGIN_ENTITY_ID,
        (
            "智见搜索开始日期",
            "Search start date",
            "imou_insight_search_begin",
            copy["search_begin_name"],
        ),
        copy["search_begin_name"],
    )
    search_end = await _ensure_named_date(
        hass,
        SEARCH_END_ENTITY_ID,
        (
            "智见搜索结束日期",
            "Search end date",
            "imou_insight_search_end",
            copy["search_end_name"],
        ),
        copy["search_end_name"],
    )
    keyword_entity = await _ensure_keyword_helper(hass)
    data = hass.data.setdefault(DOMAIN, {})
    data[_DATE_ENTITY_KEY] = date_entity
    data[_KEYWORD_ENTITY_KEY] = keyword_entity
    data[_SEARCH_BEGIN_ENTITY_KEY] = search_begin
    data[_SEARCH_END_ENTITY_KEY] = search_end
    return date_entity, keyword_entity, search_begin, search_end


async def _ensure_named_date(
    hass: HomeAssistant,
    entity_id: str,
    names: tuple[str, ...],
    label: str,
) -> str:
    if hass.states.get(entity_id) is not None:
        return entity_id
    collection = _storage_collection(hass, "input_datetime/create")
    if collection is None:
        return entity_id
    for item in collection.async_items():
        if item.get(CONF_NAME) in names:
            return f"input_datetime.{item[CONF_ID]}"
    item = await collection.async_create_item(
        {
            "name": label,
            "has_date": True,
            "has_time": False,
        }
    )
    return f"input_datetime.{item[CONF_ID]}"


async def _ensure_keyword_helper(hass: HomeAssistant) -> str:
    if hass.states.get(KEYWORD_ENTITY_ID) is not None:
        return KEYWORD_ENTITY_ID
    collection = _storage_collection(hass, "input_text/create")
    if collection is None:
        return KEYWORD_ENTITY_ID
    copy = await _copy(hass)
    for item in collection.async_items():
        if item.get(CONF_NAME) in (
            "智见搜索词",
            "Search keyword",
            "imou_insight_search_keyword",
            copy["keyword_name"],
        ):
            return f"input_text.{item[CONF_ID]}"
    item = await collection.async_create_item(
        {
            "name": copy["keyword_name"],
            "min": 0,
            "max": 64,
        }
    )
    return f"input_text.{item[CONF_ID]}"


async def _publish(
    hass: HomeAssistant,
    date_entity: str,
    keyword_entity: str,
    search_begin_entity: str,
    search_end_entity: str,
) -> None:
    copy = await _copy(hass)
    accounts = await _dashboard_accounts(hass)
    ready = [account for account in accounts if account.select_entity]
    account_entity = ""
    if len(ready) > 1:
        account_entity = await _ensure_account_select(
            hass, account_option_labels(ready), copy["account_name"]
        )
        hass.data.setdefault(DOMAIN, {})[_ACCOUNT_ENTITY_KEY] = account_entity
    _apply_language_names(hass, copy)
    await async_setup_component(hass, "lovelace", {})
    collection = _storage_collection(hass, "lovelace/dashboards/create")
    if collection is None:
        _LOGGER.debug("Lovelace dashboards are not ready")
        return
    config = build_insight_dashboard(
        accounts,
        date_entity=date_entity,
        keyword_entity=keyword_entity,
        search_begin_entity=search_begin_entity,
        search_end_entity=search_end_entity,
        copy=copy,
        account_entity=account_entity,
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
    elif (
        existing.get("title") != copy["title"]
        or existing.get(CONF_ICON) != DASHBOARD_ICON
    ):
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
    selected = _selected_account_select(hass)
    targets = [selected] if selected else _select_entities(hass)
    for select_entity in targets:
        await _refresh_select(hass, select_entity, day)


def _selected_account_select(hass: HomeAssistant) -> str | None:
    """Camera select for the account currently chosen on the dashboard."""
    data = hass.data.get(DOMAIN) or {}
    account_entity = _stored_entity(data, _ACCOUNT_ENTITY_KEY, ACCOUNT_ENTITY_ID)
    state = hass.states.get(account_entity)
    if state is None:
        return None
    ready = [account for account in _accounts(hass) if account.select_entity]
    labels = account_option_labels(ready)
    if state.state not in labels:
        return None
    return ready[labels.index(state.state)].select_entity


async def _ensure_account_select(
    hass: HomeAssistant, labels: list[str], name: str
) -> str:
    """Keep one account picker whose options are the loaded Imou Life accounts."""
    if not labels:
        return ""
    await async_setup_component(hass, "input_select", {})
    collection = _storage_collection(hass, "input_select/create")
    if collection is None:
        return ""
    names = {name, "Insight account", "智见账号", "imou_insight_account"}
    item = next(
        (
            row
            for row in collection.async_items()
            if row.get(CONF_ID) == "imou_insight_account" or row.get(CONF_NAME) in names
        ),
        None,
    )
    if item is None:
        item = await collection.async_create_item(
            {"name": name, "options": labels, "icon": "mdi:account-switch"}
        )
    elif item.get("options") != labels or item.get(CONF_NAME) != name:
        await collection.async_update_item(
            item[CONF_ID], {"name": name, "options": labels}
        )
    entity_id = f"input_select.{item[CONF_ID]}"
    state = hass.states.get(entity_id)
    if state is None or state.state not in labels:
        try:
            await hass.services.async_call(
                "input_select",
                "select_option",
                {"entity_id": entity_id, "option": labels[0]},
                blocking=True,
            )
        except HomeAssistantError as err:
            _LOGGER.debug("Insight account picker not selected: %s", err)
    return entity_id


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
