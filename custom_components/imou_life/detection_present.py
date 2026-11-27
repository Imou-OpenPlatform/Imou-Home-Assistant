"""Shared wording for who was seen, how sure that is, and the risk bar."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .helpers import fill_template, selector_option_label

_CATEGORIES = (
    ("tag_person", "🚶", "Person", ("human", "person", "people", "人")),
    ("tag_vehicle", "🚗", "Vehicle", ("vehicle", "car", "veh", "车")),
    ("tag_pet", "🐾", "Pet", ("pet", "animal", "宠物")),
    ("tag_package", "📦", "Package", ("package", "parcel", "包裹")),
    ("tag_object", "▪️", "Object", ("box", "object", "目标")),
)
_SURE_FALLBACKS = {
    "sure_fair": "Fairly sure",
    "sure_high": "Sure",
    "sure_very": "Very sure",
}


def ten_bar(filled: int) -> str:
    """A 10-step bar. Filled steps are solid."""
    count = max(0, min(int(filled), 10))
    return "●" * count + "○" * (10 - count)


def detection_labels(hass: HomeAssistant, language: str) -> dict[str, str]:
    """Names shared by capture-and-detect, search, and alarm notifications."""

    def option(key: str, fallback: str) -> str:
        return selector_option_label(hass, language, "insight_dashboard", key, fallback)

    return {
        "seen": option("search_seen", "Seen"),
        "picture": option("picture_name", "Picture"),
        "tag_person": option("tag_person", "Person"),
        "tag_vehicle": option("tag_vehicle", "Vehicle"),
        "tag_pet": option("tag_pet", "Pet"),
        "tag_package": option("tag_package", "Package"),
        "tag_object": option("tag_object", "Object"),
        "sure_name": option("sure_name", "Confidence"),
        "sure": option("sure_detail", "{word} ({score})"),
        "sure_fair": option("sure_fair", "Fairly sure"),
        "sure_high": option("sure_high", "Sure"),
        "sure_very": option("sure_very", "Very sure"),
    }


def seen_line(labels: dict[str, str], tags: list[str]) -> str:
    """One line naming who was seen, with the same icons search uses."""
    names = _seen_names(labels, tags)
    if not names:
        return ""
    heading = labels.get("seen") or "Seen"
    return f"**{heading}** {' · '.join(names)}"


def confidence_line(labels: dict[str, str], score: float) -> str:
    """How sure a capture-and-detect result is, on the same 10-step bar."""
    word_key = _sure_word_key(score)
    word = labels.get(word_key) or _SURE_FALLBACKS[word_key]
    shown = str(int(score)) if float(score).is_integer() else f"{score:g}"
    detail = fill_template(
        labels.get("sure") or "{word} ({score})",
        "{word} ({score})",
        word=word,
        score=shown,
    )
    heading = labels.get("sure_name") or "Confidence"
    return f"**{heading}** {ten_bar(_sure_steps(score))} {detail}"


def picture_line(labels: dict[str, str], description: str) -> str:
    """The sentence saved with an alarm, under the same kind of heading."""
    text = description.strip()
    if not text:
        return ""
    heading = labels.get("picture") or "Picture"
    return f"**{heading}** {text}"


def _seen_names(labels: dict[str, str], tags: list[str]) -> list[str]:
    remaining = {tag.strip().lower() for tag in tags if tag.strip()}
    names: list[str] = []
    for key, icon, fallback, aliases in _CATEGORIES:
        if not remaining.intersection(aliases):
            continue
        label = labels.get(key) or fallback
        names.append(f"{icon} {label}")
        remaining.difference_update(aliases)
    for tag in tags:
        text = tag.strip()
        if text and text.lower() in remaining:
            names.append(text)
            remaining.discard(text.lower())
    return names


def _sure_word_key(score: float) -> str:
    if score >= 95:
        return "sure_very"
    if score >= 90:
        return "sure_high"
    return "sure_fair"


def _sure_steps(score: float) -> int:
    return max(1, min(10, int((score + 5) / 10)))
