"""Tests that the declared minimum Home Assistant version is the tested one."""

from __future__ import annotations

import tomllib
from pathlib import Path

import homeassistant.const as ha_const
import pytest

REPO_ROOT = Path(__file__).parent.parent


def _minor(version: str) -> tuple[int, int]:
    major, minor = version.split(".")[:2]
    return int(major), int(minor)


@pytest.fixture(name="dev_ha_version")
def dev_ha_version_fixture() -> str:
    """Return the Home Assistant version pinned in the dev dependency group."""
    with (REPO_ROOT / "pyproject.toml").open("rb") as file:
        pyproject = tomllib.load(file)
    pins = [
        dep
        for dep in pyproject["dependency-groups"]["dev"]
        if dep.startswith("homeassistant==")
    ]
    assert len(pins) == 1, f"expected one homeassistant pin, got {pins}"
    return pins[0].split("==")[1]


def test_dev_pin_matches_installed_home_assistant(dev_ha_version: str) -> None:
    """The environment runs the Home Assistant version the project pins."""
    assert ha_const.__version__ == dev_ha_version


def test_tests_run_against_the_declared_minimum(dev_ha_version: str) -> None:
    """hacs.json advertises the release series the test suite actually exercises.

    Advertising an older minimum than the one under test lets a newer API slip in
    unnoticed and break the very users the minimum promises to support.
    """
    import json

    hacs = json.loads((REPO_ROOT / "hacs.json").read_text(encoding="utf-8"))
    assert _minor(hacs["homeassistant"]) == _minor(dev_ha_version)


def test_python_floor_supports_home_assistant() -> None:
    """requires-python is not below what Home Assistant itself needs."""
    import importlib.metadata as metadata

    with (REPO_ROOT / "pyproject.toml").open("rb") as file:
        declared = tomllib.load(file)["project"]["requires-python"]
    ha_requires = metadata.metadata("homeassistant")["Requires-Python"]

    assert _minor(declared.lstrip(">=")) >= _minor(ha_requires.lstrip(">="))
