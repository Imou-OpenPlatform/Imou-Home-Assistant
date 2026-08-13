"""Tests keeping raised exceptions and the translation files in sync."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

COMPONENT_DIR = Path(__file__).parent.parent / "custom_components" / "imou_life"
TRANSLATION_FILES = (
    COMPONENT_DIR / "strings.json",
    COMPONENT_DIR / "translations" / "en.json",
    COMPONENT_DIR / "translations" / "zh-Hans.json",
)
# translation_key="..." on the line following translation_domain=DOMAIN
RAISED_KEY_RE = re.compile(
    r"translation_domain=DOMAIN,\s*\n\s*translation_key=\"([^\"]+)\""
)
PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def load(path: Path) -> dict:
    """Return the parsed translation file."""
    return json.loads(path.read_text(encoding="utf-8"))


def raised_exception_keys() -> set[str]:
    """Return every exception translation key raised by the integration."""
    keys: set[str] = set()
    for path in COMPONENT_DIR.glob("*.py"):
        keys.update(RAISED_KEY_RE.findall(path.read_text(encoding="utf-8")))
    return keys


def test_every_raised_key_is_translated() -> None:
    """A raised key with no message would surface as the raw key to users."""
    raised = raised_exception_keys()
    assert raised, "expected the integration to raise translated exceptions"

    for path in TRANSLATION_FILES:
        declared = set(load(path).get("exceptions", {}))
        assert raised <= declared, f"{path.name} is missing {sorted(raised - declared)}"


def test_no_unused_exception_messages() -> None:
    """Messages left behind after a refactor are dead weight."""
    declared = set(load(COMPONENT_DIR / "strings.json").get("exceptions", {}))

    assert declared == raised_exception_keys()


@pytest.mark.parametrize("path", TRANSLATION_FILES[1:], ids=lambda p: p.name)
def test_translations_match_strings_json(path: Path) -> None:
    """Each language must define the same messages with the same placeholders."""
    reference = load(COMPONENT_DIR / "strings.json")["exceptions"]
    translated = load(path)["exceptions"]

    assert set(translated) == set(reference)
    for key, entry in reference.items():
        assert set(PLACEHOLDER_RE.findall(entry["message"])) == set(
            PLACEHOLDER_RE.findall(translated[key]["message"])
        ), f"{path.name}: placeholders differ for {key}"
