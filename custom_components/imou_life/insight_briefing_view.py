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

from .media_view import URL_PREFIX as MEDIA_URL_PREFIX

_SERIES_ZH = (
    ("人", "human", "#3b82c4"),
    ("车", "vehicle", "#2a9d8f"),
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
_WIDTH = 560
_PAD = 16
_CONTENT_W = _WIDTH - _PAD * 2
_ROW_H = 58
_BAR_X = 110
_BAR_W = 360
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

    title = "智见简报" if chinese else "AI Insight briefing"
    if not briefing.daily:
        return _summary(briefing, chinese), title
    url = _write_chart(folder, entry_id, briefing, language)
    return _summary(briefing, chinese), f"![{title}]({url})"


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


def _copy(chinese: bool) -> dict[str, str]:
    if chinese:
        return {
            "headline": "今天没有异常情况。",
            "detected": "检测",
            "events": "识别事件",
            "times": "次",
            "risk": "风险",
            "period": "时段",
            "count": "次数",
            "empty": "—",
            "hours": "各时段",
            "alarms": "告警",
            "quiet": "这一天没有告警",
        }
    return {
        "headline": "Nothing unusual today.",
        "detected": "Detections",
        "events": "Identified events",
        "times": " times",
        "risk": "Risk",
        "period": "Periods",
        "count": "Count",
        "empty": "—",
        "hours": "Through the day",
        "alarms": "Alarms",
        "quiet": "No alarms this day",
    }


def _timing_label(timing: LabelTiming, chinese: bool) -> str:
    names = _TIMING_ZH if chinese else _TIMING_EN
    return names.get(
        timing.label, _event_name(IdentifiedEvent(timing.label, 0), chinese)
    )


def _timing_color(label: str) -> tuple[str, str]:
    """Fill and text color for one two-hour cell."""
    colors = {
        "humanAlarm": ("#d7e8f8", "#1e4f86"),
        "carAlarm": ("#d5efe9", "#1d6b5a"),
        "petAlarm": ("#f6e6d4", "#8a4b12"),
        "packageAlarm": ("#e6e1f4", "#4c3d78"),
    }
    return colors.get(label, ("#e8edf2", "#334155"))


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
    briefing: InsightBriefing,
    language: str,
) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    safe_id = "".join(ch for ch in entry_id if ch.isalnum()) or "account"
    for old in folder.glob(f"briefing-{safe_id}-*.svg"):
        old.unlink()
    name = f"briefing-{safe_id}-{_chart_stamp(briefing.daily)}.svg"
    (folder / name).write_text(_render_svg(briefing, language), encoding="utf-8")
    return f"{MEDIA_URL_PREFIX}/imou/{name}"


def _chart_stamp(daily: tuple[DailyAlarmRollup, ...]) -> str:
    parts: list[str] = []
    for row in daily:
        parts.append(
            f"{row.date}-{row.total}-{row.human}-{row.vehicle}-{row.pet}-{row.package}"
        )
        parts.extend(f"{event.code}:{event.count}" for event in row.events)
        parts.extend(
            f"{risk.start_hour}-{risk.end_hour}-{risk.count}-{risk.text}"
            for risk in row.risks
        )
        parts.extend(
            f"{timing.label}:{','.join(str(count) for count in timing.buckets)}"
            for timing in row.timings
        )
    return str(abs(hash(tuple(parts))) % 10**12)


def _render_svg(briefing: InsightBriefing, language: str) -> str:
    chinese = _chinese(language)
    copy = _copy(chinese)
    blocks: list[str] = []
    y = 8
    headline, height = _headline_block(briefing, y, copy)
    blocks.append(headline)
    y += height
    for row in briefing.daily:
        block, height = _day_block(
            row,
            y,
            language,
            copy,
            show_date=len(briefing.daily) > 1,
        )
        blocks.append(block)
        y += height
    height = y + 8
    body = "\n".join(blocks)
    title = "智见简报" if chinese else "AI Insight briefing"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_WIDTH} {height}" '
        f'width="100%" role="img">'
        f"<title>{title}</title>"
        f"{body}</svg>"
    )


def _headline_block(
    briefing: InsightBriefing, top: int, copy: dict[str, str]
) -> tuple[str, int]:
    sentences = [risk.text for row in briefing.daily for risk in row.risks if risk.text]
    text = sentences[0] if sentences else copy["headline"]
    calm = not sentences
    fill = "#eef6f1" if calm else "#fff6eb"
    color = "#1f6b45" if calm else "#8a4b12"
    lines = _wrap_text(text, _CONTENT_W - 32, 20)
    line_h = 28
    box_h = 20 + line_h * len(lines) + 8
    parts = [
        f'<rect x="{_PAD}" y="{top}" width="{_CONTENT_W}" height="{box_h}" '
        f'rx="14" fill="{fill}"/>'
    ]
    cursor = top + 20 + 19
    for line in lines:
        parts.append(
            f'<text x="{_PAD + 18}" y="{cursor}" fill="{color}" font-size="20" '
            f'font-family="system-ui,sans-serif" font-weight="600">'
            f"{_xml(line)}</text>"
        )
        cursor += line_h
    return "\n".join(parts), box_h + 16


def _day_block(
    row: DailyAlarmRollup,
    top: int,
    language: str,
    copy: dict[str, str],
    *,
    show_date: bool,
) -> tuple[str, int]:
    chinese = _chinese(language)
    parts: list[str] = []
    cursor = top
    if show_date:
        parts.append(_section_title(cursor + 18, _display_date(row.date)))
        cursor += 28
    cursor = _append(parts, cursor, _detection_row(row, cursor, chinese, copy))
    cursor = _append(parts, cursor, _event_rows(row, cursor, chinese, copy))
    cursor = _append(parts, cursor, _risk_row(row, cursor, copy))
    cursor = _append(parts, cursor, _hour_grid(row, cursor, chinese, copy))
    cursor = _append(parts, cursor, _alarm_block(row, cursor, language, copy))
    return "\n".join(parts), cursor - top + 8


def _append(parts: list[str], cursor: int, block: tuple[str, int]) -> int:
    svg, height = block
    if svg:
        parts.append(svg)
    return cursor + height


def _section_title(y: int, text: str) -> str:
    return (
        f'<text x="{_PAD}" y="{y}" fill="#64748b" font-size="16" '
        f'font-family="system-ui,sans-serif" font-weight="600">{_xml(text)}</text>'
    )


def _detection_row(
    row: DailyAlarmRollup, top: int, chinese: bool, copy: dict[str, str]
) -> tuple[str, int]:
    catalog = _DETECTION_ZH if chinese else _DETECTION_EN
    totals = detection_totals(row.events)
    names = [(key, label) for key, label in catalog if key in totals]
    if not names:
        return "", 0
    gap = 8
    chip_w = (_CONTENT_W - gap * (len(names) - 1)) / len(names)
    chip_h = 82
    parts = [_section_title(top + 16, copy["detected"])]
    y = top + 28
    for index, (key, label) in enumerate(names):
        value = totals[key]
        x = _PAD + index * (chip_w + gap)
        number_color = "#111827" if value else "#94a3b8"
        parts.append(
            f'<rect x="{x:.1f}" y="{y}" width="{chip_w:.1f}" height="{chip_h}" '
            f'rx="14" fill="#f4f6f8"/>'
            f'<text x="{x + chip_w / 2:.1f}" y="{y + 40}" fill="{number_color}" '
            f'font-size="28" font-family="system-ui,sans-serif" font-weight="700" '
            f'text-anchor="middle">{value}</text>'
            f'<text x="{x + chip_w / 2:.1f}" y="{y + 65}" fill="#64748b" '
            f'font-size="15" font-family="system-ui,sans-serif" '
            f'text-anchor="middle">{_xml(label)}</text>'
        )
    return "\n".join(parts), 28 + chip_h + 18


def _event_rows(
    row: DailyAlarmRollup, top: int, chinese: bool, copy: dict[str, str]
) -> tuple[str, int]:
    if not row.events:
        return "", 0
    parts = [_section_title(top + 16, copy["events"])]
    cursor = top + 36
    for event in row.events:
        label = f"{_event_name(event, chinese)} {event.count}{copy['times']}"
        parts.append(
            f'<text x="{_PAD}" y="{cursor}" fill="#1f2933" font-size="17" '
            f'font-family="system-ui,sans-serif">{_xml(label)}</text>'
        )
        cursor += 28
    return "\n".join(parts), cursor - top + 4


def _risk_row(row: DailyAlarmRollup, top: int, copy: dict[str, str]) -> tuple[str, int]:
    periods = [
        f"{risk.start_hour}:00-{risk.end_hour}:00"
        for risk in row.risks
        if risk.start_hour and risk.end_hour and risk.count
    ]
    period = "、".join(periods) if periods else copy["empty"]
    count = sum(risk.count for risk in row.risks)
    gap = 8
    card_w = (_CONTENT_W - gap) / 2
    card_h = 72
    y = top + 28
    parts = [_section_title(top + 16, copy["risk"])]
    for index, (label, value, emphasize) in enumerate(
        (
            (copy["period"], period, bool(periods)),
            (copy["count"], str(count), count > 0),
        )
    ):
        x = _PAD + index * (card_w + gap)
        value_color = "#111827" if emphasize else "#94a3b8"
        parts.append(
            f'<rect x="{x:.1f}" y="{y}" width="{card_w:.1f}" height="{card_h}" '
            f'rx="14" fill="#f4f6f8"/>'
            f'<text x="{x + 18:.1f}" y="{y + 28}" fill="#64748b" font-size="15" '
            f'font-family="system-ui,sans-serif">{_xml(label)}</text>'
            f'<text x="{x + 18:.1f}" y="{y + 56}" fill="{value_color}" font-size="22" '
            f'font-family="system-ui,sans-serif" font-weight="700">'
            f"{_xml(value)}</text>"
        )
    return "\n".join(parts), 28 + card_h + 18


def _hour_grid(
    row: DailyAlarmRollup, top: int, chinese: bool, copy: dict[str, str]
) -> tuple[str, int]:
    rows = [timing for timing in row.timings if any(timing.buckets) or timing.times]
    if not rows:
        return "", 0
    columns = max((len(timing.buckets) for timing in rows), default=12) or 12
    label_w = 76
    gap = 3
    grid_w = _CONTENT_W - label_w - 6
    cell_w = (grid_w - gap * (columns - 1)) / columns
    parts = [_section_title(top + 16, copy["hours"])]
    header_y = top + 40
    for index in range(columns):
        x = _PAD + label_w + 6 + index * (cell_w + gap) + cell_w / 2
        parts.append(
            f'<text x="{x:.1f}" y="{header_y}" fill="#64748b" font-size="12" '
            f'font-family="system-ui,sans-serif" font-weight="600" '
            f'text-anchor="middle">{index * 2:02d}</text>'
        )
    cursor = header_y + 12
    cell_h = 32
    for timing in rows:
        fill, color = _timing_color(timing.label)
        parts.append(
            f'<text x="{_PAD}" y="{cursor + 22}" fill="#334155" font-size="16" '
            f'font-family="system-ui,sans-serif" font-weight="500">'
            f"{_xml(_timing_label(timing, chinese))}</text>"
        )
        buckets = list(timing.buckets) + [0] * (columns - len(timing.buckets))
        for index, count in enumerate(buckets[:columns]):
            x = _PAD + label_w + 6 + index * (cell_w + gap)
            if count:
                parts.append(
                    f'<rect x="{x:.1f}" y="{cursor}" width="{cell_w:.1f}" '
                    f'height="{cell_h}" rx="6" fill="{fill}"/>'
                    f'<text x="{x + cell_w / 2:.1f}" y="{cursor + 22}" fill="{color}" '
                    f'font-size="14" font-family="system-ui,sans-serif" '
                    f'font-weight="700" text-anchor="middle">{count}</text>'
                )
            else:
                parts.append(
                    f'<rect x="{x:.1f}" y="{cursor}" width="{cell_w:.1f}" '
                    f'height="{cell_h}" rx="6" fill="#f4f6f8"/>'
                )
        cursor += cell_h + 8
    return "\n".join(parts), cursor - top + 10


def _alarm_block(
    row: DailyAlarmRollup, top: int, language: str, copy: dict[str, str]
) -> tuple[str, int]:
    values = {
        "total": row.total,
        "human": row.human,
        "vehicle": row.vehicle,
        "pet": row.pet,
        "package": row.package,
    }
    peak = max(values.values()) or 1
    quiet = all(value == 0 for value in values.values())
    if quiet:
        return (
            f'<text x="{_PAD}" y="{top + 24}" fill="#94a3b8" font-size="16" '
            f'font-family="system-ui,sans-serif">{_xml(copy["quiet"])}</text>',
            40,
        )
    parts = [_section_title(top + 16, f"{copy['alarms']} · {_display_date(row.date)}")]
    cursor = top + 32
    series = _SERIES_ZH if _chinese(language) else _SERIES_EN
    for label, key, color in series:
        value = values[key]
        bar_w = 0 if value == 0 else max(36, round(value / peak * _BAR_W))
        parts.append(_bar_row(cursor, label, value, bar_w, color))
        cursor += _ROW_H
    return "\n".join(parts), cursor - top + 4


def _bar_row(y: int, label: str, value: int, bar_w: int, color: str) -> str:
    mid = y + _BAR_H - 9
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
        f'<text x="{_PAD}" y="{mid}" fill="#334155" font-size="20" '
        f'font-family="system-ui,sans-serif" font-weight="500">{_xml(label)}</text>'
        f"{track}{bar}"
        f'<text x="{_BAR_X + _BAR_W + 14}" y="{mid}" fill="#1f2933" font-size="20" '
        f'font-family="system-ui,sans-serif" font-weight="600">{value}</text>'
    )


def _wrap_text(text: str, width: int, font_px: int) -> list[str]:
    """Break a sentence so a long risk line stays inside the card."""
    max_units = max(8.0, width / font_px)
    lines: list[str] = []
    current = ""
    units = 0.0
    for ch in text:
        size = 1.0 if ord(ch) > 127 else 0.55
        if current and units + size > max_units:
            lines.append(current)
            current = ch
            units = size
        else:
            current += ch
            units += size
    if current:
        lines.append(current)
    return lines or [text]


def _display_date(raw: str) -> str:
    text = raw.strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text


def _xml(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
