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
    assert "今天没有异常情况" in markdown
    assert "老人 0" in markdown
    assert "门口安全 0" in markdown
    assert "![智见简报](/local/imou/briefing-ENTRY1-" in markdown
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


def test_english_briefing_uses_english_series_names(tmp_path) -> None:
    """An English Home Assistant does not draw the Chinese series names."""
    briefing = parse_alarm_message_statistics(
        {
            "statistics": [
                {
                    "date": "20261115",
                    "totalcount": 12,
                    "humanAlarm": 4,
                    "carAlarm": 2,
                    "petAlarm": 5,
                    "packageAlarm": 1,
                }
            ]
        }
    )
    summary, markdown = briefing_card(tmp_path, "ENTRY1", briefing, "en")
    assert summary == "2026-11-15 · total 12"
    assert "Nothing unusual today." in markdown
    assert "![AI Insight briefing](" in markdown
    chart = next(tmp_path.glob("briefing-ENTRY1-*.svg")).read_text(encoding="utf-8")
    assert "Person" in chart
    assert "Vehicle" in chart
    assert "Pet" in chart
    assert "Package" in chart
    assert "Total" in chart
    assert "人形" not in chart


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
    assert "今天没有异常情况" in markdown
    assert "![智见简报](" in markdown


def test_briefing_names_detected_events_and_risk(tmp_path) -> None:
    """Label codes become the names on the daily screen."""
    briefing = parse_alarm_message_statistics(
        {
            "statistics": [
                {
                    "date": "20261118",
                    "bigModelStatisticsLabels": [
                        {"labelType": "ELDERLY_APPEAR", "count": 2},
                        {"labelType": "PACKAGE_APPEAR", "count": 1},
                    ],
                }
            ],
            "aiStatisticsAnalysisResult": [
                {
                    "analysisDay": "20261118",
                    "riskStatementInfo": [
                        {
                            "startHour": "18",
                            "endHour": "20",
                            "count": 3,
                            "riskEventDoc": "有人在门口停留",
                        }
                    ],
                }
            ],
        }
    )
    _summary, markdown = briefing_card(tmp_path, "ENTRY1", briefing)
    assert markdown.startswith("有人在门口停留")
    assert "老人 2" in markdown
    assert "包裹 1" in markdown
    assert "老人出现 2次" in markdown
    assert "包裹出现 1次" in markdown
    assert "时段: 18:00-20:00" in markdown
    assert "次数: 3" in markdown


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
