"""Chart and card text for an AI Insight briefing."""

from __future__ import annotations

from pathlib import Path

from pyimouapi.insight.statistics import DailyAlarmRollup, InsightBriefing

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

    lines: list[str] = []
    title = "智见简报" if chinese else "AI Insight briefing"
    if briefing.daily:
        url = _write_chart(folder, entry_id, briefing.daily, language)
        lines.append(f"![{title}]({url})")
    if briefing.risk_notes:
        lines.append("")
        lines.append("**风险提示**" if chinese else "**Notes**")
        lines.extend(f"- {note}" for note in briefing.risk_notes[:20])
    markdown = "\n".join(lines).strip()
    return _summary(briefing, chinese), markdown


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
