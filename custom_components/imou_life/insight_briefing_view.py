"""Chart and card text for an AI Insight briefing."""

from __future__ import annotations

from pathlib import Path

from pyimouapi.insight.statistics import (
    DailyAlarmRollup,
    IdentifiedEvent,
    InsightBriefing,
    LabelTiming,
    detection_totals,
)

_SERIES_ZH = (
    ("人形", "human", "#3b82c4"),
    ("车辆", "vehicle", "#2a9d8f"),
    ("宠物", "pet", "#d08a3a"),
    ("包裹", "package", "#7c6bb0"),
    ("全部", "total", "#4c5d73"),
)
_SERIES_EN = (
    ("Person", "human", "#3b82c4"),
    ("Vehicle", "vehicle", "#2a9d8f"),
    ("Pet", "pet", "#d08a3a"),
    ("Package", "package", "#7c6bb0"),
    ("Total", "total", "#4c5d73"),
)
_EVENT_ZH = {
    "ELDERLY_APPEAR": "老人出现",
    "PACKAGE_APPEAR": "包裹出现",
    "PET_ACTIVITY": "宠物活动",
    "PET_APPEAR": "宠物出现",
    "CHILD_ARRIVE": "儿童到家或离开",
    "CHILD_LEAVE": "儿童到家或离开",
    "CHILD_HOME": "儿童到家或离开",
    "UNUSUAL_BEHAVIOR": "异常行为",
    "ABNORMAL_BEHAVIOR": "异常行为",
    "ENTRANCE_RISK": "门口有风险",
}
_EVENT_EN = {
    "ELDERLY_APPEAR": "Elderly person",
    "PACKAGE_APPEAR": "Package",
    "PET_ACTIVITY": "Pet activity",
    "PET_APPEAR": "Pet",
    "CHILD_ARRIVE": "Child arriving or leaving",
    "CHILD_LEAVE": "Child arriving or leaving",
    "CHILD_HOME": "Child arriving or leaving",
    "UNUSUAL_BEHAVIOR": "Unusual behavior",
    "ABNORMAL_BEHAVIOR": "Unusual behavior",
    "ENTRANCE_RISK": "Risk at the entrance",
}
_EVENT_PREFIX_ZH = (
    ("ELDERLY", "老人出现"),
    ("PACKAGE", "包裹出现"),
    ("PARCEL", "包裹出现"),
    ("CHILD", "儿童到家或离开"),
    ("PET", "宠物活动"),
    ("UNUSUAL", "异常行为"),
    ("ABNORMAL", "异常行为"),
    ("ENTRANCE", "门口有风险"),
    ("DOOR", "门口有风险"),
)
_EVENT_PREFIX_EN = (
    ("ELDERLY", "Elderly person"),
    ("PACKAGE", "Package"),
    ("PARCEL", "Package"),
    ("CHILD", "Child arriving or leaving"),
    ("PET", "Pet activity"),
    ("UNUSUAL", "Unusual behavior"),
    ("ABNORMAL", "Unusual behavior"),
    ("ENTRANCE", "Risk at the entrance"),
    ("DOOR", "Risk at the entrance"),
)
_DETECTION_ZH = (
    ("elderly", "老人"),
    ("package", "包裹"),
    ("child", "儿童"),
    ("pet", "宠物"),
    ("doorway", "门口安全"),
)
_DETECTION_EN = (
    ("elderly", "Elderly"),
    ("package", "Package"),
    ("child", "Child"),
    ("pet", "Pet"),
    ("doorway", "Doorway"),
)
_TIMING_ZH = {
    "humanAlarm": "人",
    "carAlarm": "车",
    "petAlarm": "宠物",
    "packageAlarm": "包裹",
}
_TIMING_EN = {
    "humanAlarm": "Person",
    "carAlarm": "Vehicle",
    "petAlarm": "Pet",
    "packageAlarm": "Package",
}
_ROW_H = 56
_BAR_X = 132
_BAR_W = 480
_BAR_H = 32


def _chinese(language: str) -> bool:
    return language.lower().startswith("zh")


def briefing_card(
    folder: Path,
    entry_id: str,
    briefing: InsightBriefing,
    language: str = "zh",
) -> tuple[str, str]:
    """Write the chart and return (entity summary, card markdown)."""
    chinese = _chinese(language)
    if not briefing.daily and not briefing.risk_notes:
        text = "这段时间没有简报。" if chinese else "No briefing for this period."
        return text, text

    lines = _briefing_lines(briefing, chinese)
    title = "智见简报" if chinese else "AI Insight briefing"
    if briefing.daily:
        url = _write_chart(folder, entry_id, briefing.daily, language)
        if lines:
            lines.append("")
        lines.append(f"![{title}]({url})")
    markdown = "\n".join(lines).strip()
    return _summary(briefing, chinese), markdown


def _briefing_lines(briefing: InsightBriefing, chinese: bool) -> list[str]:
    """The daily sentence, detection counts, events, and risk periods."""
    if not briefing.daily:
        return []
    sentences = [risk.text for row in briefing.daily for risk in row.risks if risk.text]
    if chinese:
        headline = sentences[0] if sentences else "今天没有异常情况。"
        detected = "检测"
        events_title = "识别事件"
        times_word = "次"
        risk_title = "风险"
        period_label = "时段"
        count_label = "次数"
        empty_period = "—"
        hours_title = "各时段"
        colon = ": "
    else:
        headline = sentences[0] if sentences else "Nothing unusual today."
        detected = "Detections"
        events_title = "Identified events"
        times_word = " times"
        risk_title = "Risk"
        period_label = "Periods"
        count_label = "Count"
        empty_period = "—"
        hours_title = "Through the day"
        colon = ": "
    names = _DETECTION_ZH if chinese else _DETECTION_EN
    lines = [headline, "", f"**{detected}**"]
    for row in briefing.daily:
        when = _display_date(row.date)
        prefix = f"{when} " if len(briefing.daily) > 1 else ""
        totals = detection_totals(row.events)
        lines.append(
            prefix + " · ".join(f"{label} {totals[key]}" for key, label in names)
        )
    event_lines = [
        f"- {_event_name(event, chinese)} {event.count}{times_word}"
        for row in briefing.daily
        for event in row.events
    ]
    if event_lines:
        lines.extend(["", f"**{events_title}**", *event_lines])
    periods = [
        f"{risk.start_hour}:00-{risk.end_hour}:00"
        for row in briefing.daily
        for risk in row.risks
        if risk.start_hour and risk.end_hour and risk.count
    ]
    risk_count = sum(risk.count for row in briefing.daily for risk in row.risks)
    lines.extend(
        [
            "",
            f"**{risk_title}**",
            f"{period_label}{colon}{'、'.join(periods) if periods else empty_period}",
            f"{count_label}{colon}{risk_count}",
        ]
    )
    hour_lines = [
        line
        for row in briefing.daily
        for timing in row.timings
        if (line := _timing_line(timing, chinese))
    ]
    if hour_lines:
        lines.extend(["", f"**{hours_title}**", *hour_lines])
    return lines


def _event_name(event: IdentifiedEvent, chinese: bool) -> str:
    key = event.code.strip().upper().replace("-", "_")
    table = _EVENT_ZH if chinese else _EVENT_EN
    if key in table:
        return table[key]
    prefixes = _EVENT_PREFIX_ZH if chinese else _EVENT_PREFIX_EN
    for prefix, name in prefixes:
        if key.startswith(prefix):
            return name
    words = key.replace("_", " ").lower()
    return words[:1].upper() + words[1:] if words else event.code


def _timing_line(timing: LabelTiming, chinese: bool) -> str | None:
    names = _TIMING_ZH if chinese else _TIMING_EN
    label = names.get(
        timing.label, _event_name(IdentifiedEvent(timing.label, 0), chinese)
    )
    parts = [
        f"{index * 2:02d}:00-{(index + 1) * 2:02d}:00 {count}"
        for index, count in enumerate(timing.buckets)
        if count
    ]
    if not parts and timing.times:
        parts = list(timing.times[:3])
    if not parts:
        return None
    colon = ": "
    return f"- {label}{colon}" + "、".join(parts)


def _summary(briefing: InsightBriefing, chinese: bool) -> str:
    title = "智见简报" if chinese else "AI Insight briefing"
    if not briefing.daily:
        return title
    first = _display_date(briefing.daily[0].date)
    last = _display_date(briefing.daily[-1].date)
    total = sum(row.total for row in briefing.daily)
    when = first if first == last else f"{first} - {last}"
    label = "全部" if chinese else "total"
    return f"{when} · {label} {total}"


def _write_chart(
    folder: Path,
    entry_id: str,
    daily: tuple[DailyAlarmRollup, ...],
    language: str,
) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    safe_id = "".join(ch for ch in entry_id if ch.isalnum()) or "account"
    for old in folder.glob(f"briefing-{safe_id}-*.svg"):
        old.unlink()
    name = f"briefing-{safe_id}-{_chart_stamp(daily)}.svg"
    (folder / name).write_text(_render_svg(daily, language), encoding="utf-8")
    return f"/local/imou/{name}"


def _chart_stamp(daily: tuple[DailyAlarmRollup, ...]) -> str:
    parts = [
        f"{row.date}-{row.total}-{row.human}-{row.vehicle}-{row.pet}-{row.package}"
        for row in daily
    ]
    return str(abs(hash(tuple(parts))) % 10**12)


def _render_svg(daily: tuple[DailyAlarmRollup, ...], language: str) -> str:
    blocks: list[str] = []
    y = 16
    width = 720
    for row in daily:
        block, height = _day_block(row, y, language)
        blocks.append(block)
        y += height
    height = y + 12
    body = "\n".join(blocks)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="100%" role="img">'
        f"<title>{'智见简报' if _chinese(language) else 'AI Insight briefing'}</title>"
        f"{body}</svg>"
    )


def _day_block(row: DailyAlarmRollup, top: int, language: str) -> tuple[str, int]:
    values = {
        "total": row.total,
        "human": row.human,
        "vehicle": row.vehicle,
        "pet": row.pet,
        "package": row.package,
    }
    peak = max(values.values()) or 1
    quiet = peak == 1 and all(value == 0 for value in values.values())
    title_y = top + 28
    parts = [
        f'<text x="8" y="{title_y}" fill="#1f2933" font-size="22" '
        f'font-family="system-ui,sans-serif" font-weight="600">'
        f"{_xml(_display_date(row.date))}</text>"
    ]
    cursor = title_y + 12
    if quiet:
        cursor += 28
        parts.append(
            f'<text x="8" y="{cursor}" fill="#6b7280" font-size="16" '
            f'font-family="system-ui,sans-serif">'
            f"{'这一天没有告警' if _chinese(language) else 'No alarms this day'}</text>"
        )
    cursor += 20
    series = _SERIES_ZH if _chinese(language) else _SERIES_EN
    for label, key, color in series:
        value = values[key]
        bar_w = 0 if value == 0 else max(36, round(value / peak * _BAR_W))
        parts.append(_bar_row(cursor, label, value, bar_w, color))
        cursor += _ROW_H
    return "\n".join(parts), cursor - top + 8


def _bar_row(y: int, label: str, value: int, bar_w: int, color: str) -> str:
    mid = y + _BAR_H - 8
    track = (
        f'<rect x="{_BAR_X}" y="{y}" width="{_BAR_W}" height="{_BAR_H}" rx="16" '
        'fill="#e8edf2"/>'
    )
    bar = ""
    if bar_w > 0:
        bar = (
            f'<rect x="{_BAR_X}" y="{y}" width="{bar_w}" height="{_BAR_H}" rx="16" '
            f'fill="{color}"/>'
        )
    return (
        f'<text x="8" y="{mid}" fill="#334155" font-size="18" '
        f'font-family="system-ui,sans-serif">{_xml(label)}</text>'
        f"{track}{bar}"
        f'<text x="{_BAR_X + _BAR_W + 16}" y="{mid}" fill="#1f2933" font-size="18" '
        f'font-family="system-ui,sans-serif">{value}</text>'
    )


def _display_date(raw: str) -> str:
    text = raw.strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def _xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
