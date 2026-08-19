"""Tests for Imou Life select platform."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.imou_life.const import (
    PARAM_COLLECTION_POINT,
    PARAM_DEVICE_VOLUME,
    PARAM_MODE,
    PARAM_NIGHT_VISION_MODE,
)
from custom_components.imou_life.select import SELECT_TYPES, _iter_selects
from pyimouapi.const import PARAM_CURRENT_OPTION, PARAM_OPTIONS
from pyimouapi.ha_device import ImouHaDevice


def _mock_coordinator(devices: list[ImouHaDevice]) -> MagicMock:
    coordinator = MagicMock()
    coordinator.devices = devices
    return coordinator


def _mock_device(selects: dict[str, dict]) -> ImouHaDevice:
    device = MagicMock(spec=ImouHaDevice)
    device.selects = selects
    return device


def test_iter_selects_whitelist_only() -> None:
    """Only supported select types are exposed."""
    device = _mock_device(
        {
            PARAM_NIGHT_VISION_MODE: {
                PARAM_OPTIONS: ["intelligent", "fullcolor"],
                PARAM_CURRENT_OPTION: "intelligent",
            },
            PARAM_MODE: {
                PARAM_OPTIONS: ["home", "away", "disarm"],
                PARAM_CURRENT_OPTION: "home",
            },
            "legacy_unknown_select": {
                PARAM_OPTIONS: ["x"],
                PARAM_CURRENT_OPTION: "x",
            },
        }
    )
    pairs = _iter_selects(_mock_coordinator([device]))
    keys = {description.key for description, _ in pairs}
    assert PARAM_MODE not in keys
    assert keys == {PARAM_NIGHT_VISION_MODE}


def test_select_types_match_core_whitelist() -> None:
    """Supported select keys stay aligned with Core imou."""
    assert {description.key for description in SELECT_TYPES} == {
        PARAM_COLLECTION_POINT,
        PARAM_DEVICE_VOLUME,
        PARAM_NIGHT_VISION_MODE,
    }
