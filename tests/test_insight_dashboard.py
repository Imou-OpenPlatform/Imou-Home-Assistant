"""Sidebar dashboard layout for AI Insight."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.imou_life.insight_dashboard import (
    _COPY_DEFAULTS,
    InsightDashboardAccount,
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
        "view_briefing": "简报",
        "view_search": "搜索",
        "view_trial": "试用",
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
    assert briefing["title"] == "简报"
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
    assert trial["title"] == "试用"
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
    assert search_cards[0]["entities"][-2:] == [
        {"entity": "input_datetime.imou_insight_search_begin", "name": "Start"},
        {"entity": "input_datetime.imou_insight_search_end", "name": "End"},
    ]
    rendered = str(search)
    assert "input_datetime.imou_insight_search_begin" in rendered
    assert "input_datetime.imou_insight_search_end" in rendered
    assert {action["service"] for action in actions} == {"imou_life.search_ai_alarms"}


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
