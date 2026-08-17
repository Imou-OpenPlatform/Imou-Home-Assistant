"""Tests for Imou Life switch platform."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.imou_life.const import (
    PARAM_FRAME_REVERSE,
    PARAM_LINKAGE_SIREN,
    PARAM_LINKAGE_WHITE_LIGHT,
    PARAM_MOTION_DETECT,
    PARAM_NOTIFY_ON_ALARM,
    PARAM_PET_DETECT,
    PARAM_PLAY_SOUND,
    PARAM_PLUG_SWITCH,
    PARAM_SMART_TRACK,
    PARAM_WIDE_DYNAMIC,
)
from custom_components.imou_life.switch import (
    SWITCH_TYPES,
    _iter_notify_on_alarm_switches,
    _iter_switches,
)
from pyimouapi.const import PARAM_STATE
from pyimouapi.ha_device import ImouHaDevice

FEATURE_SWITCH_KEYS = {
    PARAM_FRAME_REVERSE,
    PARAM_LINKAGE_SIREN,
    PARAM_LINKAGE_WHITE_LIGHT,
    PARAM_PET_DETECT,
    PARAM_PLAY_SOUND,
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
    """Feature switches must be registered or they never appear."""
    keys = {description.key for description in SWITCH_TYPES}
    assert keys >= FEATURE_SWITCH_KEYS


def test_iter_notify_on_alarm_covers_every_device() -> None:
    """Notify-on-alarm is HA-only and is created for cameras and non-cameras."""
    camera = _mock_device({})
    camera.channel_id = "0"
    plug = _mock_device({})
    plug.channel_id = None
    pairs = _iter_notify_on_alarm_switches(_mock_coordinator([camera, plug]))
    assert [key for key, _device in pairs] == [
        PARAM_NOTIFY_ON_ALARM,
        PARAM_NOTIFY_ON_ALARM,
    ]
    assert [device for _key, device in pairs] == [camera, plug]
