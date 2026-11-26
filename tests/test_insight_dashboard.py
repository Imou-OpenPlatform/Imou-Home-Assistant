"""Sidebar dashboard layout for AI Insight."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.imou_life.insight_dashboard import (
    _COPY_DEFAULTS,
    InsightDashboardAccount,
    account_option_labels,
    build_insight_dashboard,
)
from custom_components.imou_life.insight_workbench import _search_keyword
from homeassistant.core import ServiceCall


def _config() -> dict:
    account = InsightDashboardAccount(
        title="海外",
        select_entity="select.cam",
        briefing_entity="sensor.brief",
        search_entity="sensor.search",
        trial_entity="button.trial",
        trial_note="📱 **WhatsApp**: WhatsApp +86 19106884129",
    )
    copy = {
        **_COPY_DEFAULTS,
        "view_briefing": "AI简报",
        "view_search": "智慧消息",
        "view_trial": "申请试用",
        "trial_markdown": "✨ pick a date and the briefing appears",
    }
    return build_insight_dashboard(
        [account],
        date_entity="input_datetime.imou_insight_briefing_end",
        keyword_entity="input_text.imou_insight_search_keyword",
        search_begin_entity="input_datetime.imou_insight_search_begin",
        search_end_entity="input_datetime.imou_insight_search_end",
        copy=copy,
    )


def test_briefing_follows_the_date_without_a_refresh_button() -> None:
    """Changing the date is the refresh. The briefing page has no refresh button."""
    config = _config()
    briefing = config["views"][0]
    assert briefing["path"] == "briefing"
    assert briefing["title"] == "AI简报"
    rendered = str(briefing)
    assert "input_datetime.imou_insight_briefing_end" in rendered
    assert "refresh_ai_insight_briefing" not in rendered
    assert "button.trial" not in rendered


def test_trial_page_shows_the_dialog_copy_without_a_button() -> None:
    """Trial sits beside Briefing and Search, with the dialog text on the page."""
    config = _config()
    trial = config["views"][2]
    assert [view["path"] for view in config["views"]] == [
        "briefing",
        "search",
        "trial",
    ]
    assert trial["title"] == "申请试用"
    assert len(trial["cards"]) == 1
    rendered = trial["cards"][0]["content"]
    assert "✨" in rendered
    assert "WhatsApp" in rendered
    assert "start a trial" in rendered
    assert "button" not in rendered
    search = config["views"][1]["cards"][0]
    assert search["type"] == "vertical-stack"
    search_cards = search["cards"]
    actions = [
        button["tap_action"]
        for card in search_cards
        if card["type"] == "horizontal-stack"
        for button in card["cards"]
    ]
    assert [action["data"] for action in actions] == [
        {"page": "previous"},
        {},
        {"page": "next"},
    ]
    assert search_cards[0]["entities"][-4:] == [
        {"type": "section", "label": "Start date"},
        {"entity": "input_datetime.imou_insight_search_begin", "name": " "},
        {"type": "section", "label": "End date"},
        {"entity": "input_datetime.imou_insight_search_end", "name": " "},
    ]
    rendered = str(search)
    assert "input_datetime.imou_insight_search_begin" in rendered
    assert "input_datetime.imou_insight_search_end" in rendered
    assert {action["service"] for action in actions} == {"imou_life.search_ai_alarms"}


def _account(title: str, suffix: str, entry_id: str = "") -> InsightDashboardAccount:
    return InsightDashboardAccount(
        title=title,
        select_entity=f"select.{suffix}",
        briefing_entity=f"sensor.brief_{suffix}",
        search_entity=f"sensor.search_{suffix}",
        trial_entity=None,
        entry_id=entry_id,
    )


def test_several_accounts_pick_the_account_before_the_camera() -> None:
    """Account and camera share one card; buttons and results follow the account."""
    copy = {**_COPY_DEFAULTS, "account": "账号", "view_search": "智慧消息"}
    config = build_insight_dashboard(
        [
            _account("Imou Life Official", "sg", "lc27d061aaaa"),
            _account("Imou Life Official", "cn", "lc1e8f13bbbb"),
        ],
        date_entity="input_datetime.imou_insight_briefing_end",
        keyword_entity="input_text.imou_insight_search_keyword",
        search_begin_entity="input_datetime.imou_insight_search_begin",
        search_end_entity="input_datetime.imou_insight_search_end",
        copy=copy,
        account_entity="input_select.imou_insight_account",
    )
    search = config["views"][1]["cards"][0]
    assert search["type"] == "vertical-stack"
    shared = search["cards"][0]["entities"]
    assert shared[0:2] == [
        {"type": "section", "label": "账号"},
        {"entity": "input_select.imou_insight_account", "name": " "},
    ]
    assert shared[2] == {"type": "section", "label": "Camera"}
    assert [row["type"] for row in shared[3:5]] == ["conditional", "conditional"]
    assert shared[3]["row"] == {"entity": "select.sg", "name": " "}
    assert shared[4]["row"] == {"entity": "select.cn", "name": " "}
    assert [row.get("entity") for row in shared[5:] if "entity" in row] == [
        "input_text.imou_insight_search_keyword",
        "input_datetime.imou_insight_search_begin",
        "input_datetime.imou_insight_search_end",
    ]
    assert [row["label"] for row in shared[5:] if row.get("type") == "section"] == [
        "Search text",
        "Start date",
        "End date",
    ]
    switches = [card for card in search["cards"][1:] if card["type"] == "conditional"]
    assert [card["conditions"][0]["state"] for card in switches] == [
        "Imou Life Official (lc27d0)",
        "Imou Life Official (lc1e8f)",
    ]
    first_actions = switches[0]["card"]["cards"]
    assert [button["name"] for button in first_actions] == [
        "Previous",
        "Search",
        "Next",
    ]
    assert first_actions[1]["tap_action"]["target"] == {"entity_id": "select.sg"}
    result_cards = config["views"][1]["cards"][1:]
    assert all(card["type"] == "conditional" for card in result_cards)
    assert "sensor.search_cn" not in str(result_cards[0])
    briefing_rows = config["views"][0]["cards"][0]["entities"]
    assert briefing_rows[0:2] == [
        {"type": "section", "label": "账号"},
        {"entity": "input_select.imou_insight_account", "name": " "},
    ]
    assert briefing_rows[2] == {"type": "section", "label": "Camera"}
    assert briefing_rows[3]["type"] == "conditional"
    assert briefing_rows[3]["row"]["entity"] == "select.sg"
    assert briefing_rows[-2:] == [
        {"type": "section", "label": "Date"},
        {"entity": "input_datetime.imou_insight_briefing_end", "name": " "},
    ]


def test_repeated_account_titles_stay_distinct() -> None:
    """Two accounts with the same name need different picker options."""
    accounts = [
        _account("Imou Life", "a", "abcdef123456"),
        _account("Imou Life", "b", "123456abcdef"),
    ]
    assert account_option_labels(accounts) == [
        "Imou Life (abcdef)",
        "Imou Life (123456)",
    ]


def test_search_reads_the_text_box_when_keyword_is_omitted() -> None:
    """The search button does not template the text box, so the service reads it."""
    hass = MagicMock()
    hass.data = {}
    hass.states.get.return_value = SimpleNamespace(state="有人经过")
    call = ServiceCall(hass, "imou_life", "search_ai_alarms", {"days": 7})
    assert _search_keyword(hass, call) == "有人经过"


def test_search_keeps_an_explicit_keyword() -> None:
    """Automations that pass keyword still win over the text box."""
    hass = MagicMock()
    hass.data = {}
    call = ServiceCall(hass, "imou_life", "search_ai_alarms", {"keyword": "  车辆  "})
    assert _search_keyword(hass, call) == "车辆"
    hass.states.get.assert_not_called()
