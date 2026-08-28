"""Tests keeping raised exceptions and the translation files in sync."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

COMPONENT_DIR = Path(__file__).parent.parent / "custom_components" / "imou_life"
REFERENCE = COMPONENT_DIR / "strings.json"
LANGUAGE_FILES = sorted((COMPONENT_DIR / "translations").glob("*.json"))
RAISED_KEY_RE = re.compile(
    r"translation_domain=DOMAIN,\s*\n\s*translation_key=\"([^\"]+)\""
)
PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def load(path: Path) -> dict:
    """Return the parsed translation file."""
    return json.loads(path.read_text(encoding="utf-8"))


def leaf_paths(obj: object, prefix: str = "") -> dict[str, str]:
    """Return dotted paths of every string leaf."""
    if isinstance(obj, dict):
        out: dict[str, str] = {}
        for key, value in obj.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            out.update(leaf_paths(value, path))
        return out
    return {prefix: str(obj)}


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

    for path in [REFERENCE, *LANGUAGE_FILES]:
        declared = set(load(path).get("exceptions", {}))
        assert raised <= declared, f"{path.name} is missing {sorted(raised - declared)}"


def test_no_unused_exception_messages() -> None:
    """Messages left behind after a refactor are dead weight."""
    declared = set(load(REFERENCE).get("exceptions", {}))
    assert declared == raised_exception_keys()


def test_language_files_exist() -> None:
    assert LANGUAGE_FILES, "expected translations/*.json"


@pytest.mark.parametrize("path", LANGUAGE_FILES, ids=lambda p: p.name)
def test_translations_match_strings_json(path: Path) -> None:
    """Each language must define the same messages with the same placeholders."""
    reference = leaf_paths(load(REFERENCE))
    translated = leaf_paths(load(path))
    assert set(translated) == set(reference), (
        f"{path.name} missing {sorted(set(reference) - set(translated))}"
        f" extra {sorted(set(translated) - set(reference))}"
    )
    for key, english in reference.items():
        assert set(PLACEHOLDER_RE.findall(english)) == set(
            PLACEHOLDER_RE.findall(translated[key])
        ), f"{path.name}: placeholders differ for {key}"
