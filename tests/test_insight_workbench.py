"""AI Insight workbench service account selection."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from custom_components.imou_life.const import DOMAIN
from custom_components.imou_life.insight_workbench import (
    _briefing_stat_days,
    _resolve_config_entry,
    _search_window,
    search_card,
)
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pyimouapi.insight.search import InsightAlarmHit
from pytest_homeassistant_custom_component.common import MockConfigEntry

from . import USER_INPUT


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_two_accounts_follow_the_targeted_workbench(
    hass: HomeAssistant,
) -> None:
    """A targeted 智见设备 picks that account when two are loaded."""
    first = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, unique_id="account-a")
    second = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, unique_id="account-b")
    first.add_to_hass(hass)
    second.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "select",
        DOMAIN,
        f"{second.entry_id}$insight_workbench_device",
        suggested_object_id="workbench_b",
        config_entry=second,
    )

    call = ServiceCall(
        hass,
        DOMAIN,
        "refresh_ai_insight_briefing",
        {"entity_id": "select.workbench_b"},
    )

    assert _resolve_config_entry(hass, call).entry_id == second.entry_id


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_two_accounts_without_a_target_are_rejected(hass: HomeAssistant) -> None:
    """Refreshing with no entity is ambiguous once a second account is loaded."""
    MockConfigEntry(domain=DOMAIN, data=USER_INPUT, unique_id="account-a").add_to_hass(
        hass
    )
    MockConfigEntry(domain=DOMAIN, data=USER_INPUT, unique_id="account-b").add_to_hass(
        hass
    )
    call = ServiceCall(hass, DOMAIN, "refresh_ai_insight_briefing", {})

    with pytest.raises(ServiceValidationError) as err:
        _resolve_config_entry(hass, call)

    assert err.value.translation_key == "insight_workbench_entry_required"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_one_account_needs_no_target(hass: HomeAssistant) -> None:
    """A single loaded account is the workbench account."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, unique_id="only")
    entry.add_to_hass(hass)
    call = ServiceCall(hass, DOMAIN, "refresh_ai_insight_briefing", {})

    assert _resolve_config_entry(hass, call).entry_id == entry.entry_id


def test_briefing_uses_the_selected_calendar_day() -> None:
    """A picked date is that whole day, sent as yyyyMMdd."""
    call = ServiceCall(
        MagicMock(),
        DOMAIN,
        "refresh_ai_insight_briefing",
        {"end_date": "2026-11-19"},
    )

    assert _briefing_stat_days(call) == ("20261119", "20261119")


def test_search_window_spans_the_selected_dates() -> None:
    """Search runs from 00:00:00 on the start date through 23:59:59 on the end date."""
    call = ServiceCall(
        MagicMock(),
        DOMAIN,
        "search_ai_alarms",
        {"begin_date": "2026-11-01", "end_date": "2026-11-03"},
    )

    assert _search_window(MagicMock(), call) == (
        "2026-11-01 00:00:00",
        "2026-11-03 23:59:59",
    )


def test_search_window_reads_the_date_boxes() -> None:
    """The search button omits dates, so the service reads the two date helpers."""
    hass = MagicMock()
    hass.data = {}
    hass.states.get.side_effect = lambda entity_id: {
        "input_datetime.imou_insight_search_begin": SimpleNamespace(state="2026-11-02"),
        "input_datetime.imou_insight_search_end": SimpleNamespace(state="2026-11-04"),
    }.get(entity_id)
    call = ServiceCall(hass, DOMAIN, "search_ai_alarms", {})

    assert _search_window(hass, call) == (
        "2026-11-02 00:00:00",
        "2026-11-04 23:59:59",
    )


def test_search_card_lists_a_hit() -> None:
    """Each result uses the cloud copy as its title and an icon for the type."""
    person = InsightAlarmHit(
        alarm_id="1",
        begin_time="2026-11-15 08:00:00",
        ai_copywriting="有人经过",
        security_risk_level=1,
        ai_tags=("human",),
        thumb_url=None,
        alarm_type="llmCloudMsg",
    )
    car = InsightAlarmHit(
        alarm_id="2",
        begin_time="2026-11-15 09:00:00",
        ai_copywriting="一辆车经过",
        security_risk_level=2,
        ai_tags=("vehicle",),
        thumb_url=None,
        alarm_type="llmCloudMsg",
    )
    summary, markdown = search_card(
        "有人",
        [(person, "/local/imou/thumbs/1.jpg"), (car, None)],
    )
    assert summary == "有人经过"
    assert "🚶 **有人经过**" in markdown
    assert "Page 1" in markdown
    assert "🚗 **一辆车经过**" in markdown
    assert "![有人经过](/local/imou/thumbs/1.jpg)" in markdown
    assert "风险" not in markdown
    assert "human" not in markdown


def test_search_card_empty() -> None:
    """An empty result says nothing matched."""
    summary, markdown = search_card("猫", [])
    assert summary == markdown
    assert 'No alarms match "猫".' in summary
