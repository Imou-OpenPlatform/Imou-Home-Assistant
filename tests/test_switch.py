"""Tests for Imou Life switch platform."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.imou_life.const import PARAM_MOTION_DETECT, PARAM_PLUG_SWITCH
from custom_components.imou_life.switch import SWITCH_TYPES, _iter_switches
from pyimouapi.const import PARAM_STATE
from pyimouapi.ha_device import ImouHaDevice


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
