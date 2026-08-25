"""Webhook motion / human / PIR as a camera binary_sensor."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from custom_components.imou_life.binary_sensor import (
    _iter_motion_sensors,
    motion_binary_state,
)
from custom_components.imou_life.const import PARAM_MOTION
from pyimouapi.ha_device import ImouHaDevice


def _device(*, channel_id: object | None, device_id: str = "SN1") -> MagicMock:
    device = MagicMock(spec=ImouHaDevice)
    device.device_id = device_id
    device.channel_id = channel_id
    device.product_id = None
    device.device_name = "Front"
    device.channel_name = "Front"
    device.manufacturer = "Imou"
    device.model = "IPC"
    device.swversion = "1.0"
    device.sensors = {}
    return device


def _coordinator(devices: list[MagicMock]) -> MagicMock:
    coordinator = MagicMock()
    coordinator.devices = devices
    coordinator.devices_by_key = {}
    coordinator.last_update_success = True
    return coordinator


@pytest.mark.parametrize(
    ("msg_type", "expected"),
    [
        ("videoMotion", True),
        ("e_videoMotion", True),
        ("human", True),
        ("mobileDetect", True),
        ("alarmPIR", True),
        ("e_alarmPIR", True),
        ("pir_alarm", True),
        ("e_clearAlarmPIR", False),
        ("pir_cleared", False),
        ("smokeAlarm", None),
        ("gasAlarm", None),
        ("abAlarmSound", None),
        ("e_pet", None),
        ("e_multiVideoAiPerArea", None),
        ("e_multiVideoAiPerAreaAlarm", None),
        (None, None),
    ],
)
def test_motion_binary_state(msg_type: str | None, expected: bool | None) -> None:
    """Only picture / human / PIR pushes drive the motion sensor."""
    assert motion_binary_state(msg_type) is expected


def test_iter_motion_sensors_cameras_only() -> None:
    """Motion is a camera channel entity, not a plug or bare hub."""
    camera = _device(channel_id="0")
    plug = _device(channel_id=None, device_id="PLUG")
    pairs = _iter_motion_sensors(_coordinator([camera, plug]))
    assert [(key, device) for key, device in pairs] == [(PARAM_MOTION, camera)]
