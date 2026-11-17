"""AI Insight workbench service account selection."""

import pytest
from custom_components.imou_life.const import DOMAIN
from custom_components.imou_life.insight_workbench import (
    _resolve_config_entry,
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
