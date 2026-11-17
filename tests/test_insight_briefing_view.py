"""Briefing chart markdown."""

from custom_components.imou_life.insight_briefing_view import briefing_card
from pyimouapi.insight.statistics import (
    DailyAlarmRollup,
    InsightBriefing,
    parse_alarm_message_statistics,
)


def test_briefing_card_writes_a_bar_chart(tmp_path) -> None:
    briefing = parse_alarm_message_statistics(
        {
            "statistics": [
                {
                    "date": "20261115",
                    "totalcount": 12,
                    "humanAlarm": 4,
                    "carAlarm": 2,
                    "petAlarm": 5,
                    "motionAlarm": 1,
                }
            ]
        }
    )
    summary, markdown = briefing_card(tmp_path, "ENTRY1", briefing)
    assert summary == "2026-11-15 · 全部 12"
    assert markdown.startswith("![智见简报](/local/imou/briefing-ENTRY1-")
    chart = next(tmp_path.glob("briefing-ENTRY1-*.svg")).read_text(encoding="utf-8")
    assert "2026-11-15" in chart
    assert "人形" in chart
    assert "包裹" in chart
    assert "全部" in chart
    assert "画面变化" not in chart
    assert ">4<" in chart
    assert ">5<" in chart
    assert 'height="32"' in chart
    assert 'font-size="18"' in chart


def test_live_totals_include_package_and_are_not_a_quiet_day(tmp_path) -> None:
    """totalCount is the day total. A non-zero day is not labeled empty."""
    briefing = parse_alarm_message_statistics(
        {
            "statistics": [
                {
                    "date": "20261116",
                    "humanAlarm": 39,
                    "carAlarm": 0,
                    "petAlarm": 0,
                    "totalCount": 426,
                    "packageAlarm": 19,
                }
            ]
        }
    )
    summary, _markdown = briefing_card(tmp_path, "ENTRY1", briefing)
    chart = next(tmp_path.glob("*.svg")).read_text(encoding="utf-8")
    assert summary == "2026-11-16 · 全部 426"
    assert ">426<" in chart
    assert ">39<" in chart
    assert ">19<" in chart
    assert "这一天没有告警" not in chart
    assert "包裹" in chart


def test_quiet_day_still_draws_the_chart(tmp_path) -> None:
    briefing = InsightBriefing(
        daily=(
            DailyAlarmRollup(
                date="20261115", total=0, human=0, vehicle=0, pet=0, package=0
            ),
        ),
        risk_notes=(),
    )
    _summary, markdown = briefing_card(tmp_path, "ENTRY1", briefing)
    chart = next(tmp_path.glob("*.svg")).read_text(encoding="utf-8")
    assert "这一天没有告警" in chart
    assert "![智见简报](" in markdown


def test_refresh_replaces_the_previous_chart(tmp_path) -> None:
    first = InsightBriefing(
        daily=(DailyAlarmRollup("20261115", 1, 1, 0, 0, 0),),
        risk_notes=(),
    )
    second = InsightBriefing(
        daily=(DailyAlarmRollup("20261115", 3, 1, 2, 0, 0),),
        risk_notes=(),
    )
    briefing_card(tmp_path, "ENTRY1", first)
    briefing_card(tmp_path, "ENTRY1", second)
    charts = list(tmp_path.glob("briefing-ENTRY1-*.svg"))
    assert len(charts) == 1
    assert ">3<" in charts[0].read_text(encoding="utf-8")
