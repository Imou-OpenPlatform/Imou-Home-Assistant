"""Tests that the manifest declares what the code actually reaches for.

Home Assistant only guarantees a component is set up before this integration
when the manifest names it. Importing one without declaring it works right up
until someone runs an installation that does not happen to load it already.
"""

import ast
import json
from pathlib import Path

import pytest

COMPONENT = Path(__file__).parent.parent / "custom_components" / "imou_life"
MANIFEST = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))


def _imported_components(path: Path) -> set[str]:
    """Return the homeassistant components a module imports."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.ImportFrom) or node.module is None:
            continue
        if node.module == "homeassistant.components":
            found.update(alias.name for alias in node.names)
        elif node.module.startswith("homeassistant.components."):
            found.add(node.module.split(".")[2])
    return found


def test_every_imported_component_is_declared() -> None:
    """A component the code imports has to be a declared dependency."""
    declared = set(MANIFEST.get("dependencies", []))
    for path in sorted(COMPONENT.glob("*.py")):
        for component in _imported_components(path):
            # A platform file importing its own platform is the platform
            # itself, which needs no declaring.
            if component == path.stem:
                continue
            assert component in declared, (
                f"{path.name} imports homeassistant.components.{component}, "
                f"so the manifest must list it under dependencies"
            )


def test_webhook_is_declared() -> None:
    """Event push registers a webhook, which needs that component loaded."""
    assert "webhook" in MANIFEST["dependencies"]


def test_loggers_names_the_api_library() -> None:
    """The logger integration groups the library's output with this domain."""
    assert MANIFEST["loggers"] == ["pyimouapi"]


@pytest.mark.parametrize(
    "key", ["domain", "name", "codeowners", "config_flow", "documentation", "version"]
)
def test_required_keys_present(key: str) -> None:
    """Keys HACS and Home Assistant expect on a custom integration."""
    assert MANIFEST.get(key)


def test_requirement_matches_the_pinned_library() -> None:
    """The shipped requirement has to be the version the tests ran against."""
    from importlib.metadata import version

    (requirement,) = MANIFEST["requirements"]
    name, _, pinned = requirement.partition("==")
    assert name == "pyimouapi"
    assert pinned == version("pyimouapi")
