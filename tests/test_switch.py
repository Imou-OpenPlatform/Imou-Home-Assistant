"""Tests for Imou Life switch platform."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.imou_life.const import (
    PARAM_FRAME_REVERSE,
    PARAM_MOTION_DETECT,
    PARAM_PET_DETECT,
    PARAM_PLUG_SWITCH,
    PARAM_SMART_TRACK,
    PARAM_WIDE_DYNAMIC,
)
from custom_components.imou_life.switch import SWITCH_TYPES, _iter_switches
from pyimouapi.const import PARAM_STATE
from pyimouapi.ha_device import ImouHaDevice

FEATURE_SWITCH_KEYS = {
    PARAM_FRAME_REVERSE,
    PARAM_PET_DETECT,
    PARAM_SMART_TRACK,
    PARAM_WIDE_DYNAMIC,
}


def _mock_coordinator(devices: list[ImouHaDevice]) -> MagicMock:
    coordinator = MagicMock()
    coordinator.devices = devices
    return coordinator


def _mock_device(switches: dict[str, dict]) -> ImouHaDevice:
    device = MagicMock(spec=ImouHaDevice)
    device.switches = switches
    return device


def test_iter_switches_whitelist_only() -> None:
    """Unknown switch keys from the API are not turned into entities."""
    device = _mock_device(
        {
            PARAM_MOTION_DETECT: {PARAM_STATE: True},
            "legacy_unknown_switch": {PARAM_STATE: False},
        }
    )
    pairs = _iter_switches(_mock_coordinator([device]))
    assert len(pairs) == 1
    assert pairs[0][0].key == PARAM_MOTION_DETECT


def test_switch_types_include_plug_switch() -> None:
    """Plug switch remains in the supported whitelist."""
    assert PARAM_PLUG_SWITCH in {description.key for description in SWITCH_TYPES}


def test_switch_types_include_feature_switches() -> None:
    """Pet, flip, WDR, and smart-track must be registered or they never appear."""
    keys = {description.key for description in SWITCH_TYPES}
    assert keys >= FEATURE_SWITCH_KEYS
