"""Tests for Imou Life sensor platform."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from custom_components.imou_life.const import DOMAIN
from custom_components.imou_life.sensor import (
    SENSOR_DESCRIPTIONS,
    ImouSensor,
    _iter_sensors,
)
from pyimouapi.const import (
    PARAM_BATTERY,
    PARAM_STATE,
    PARAM_STATE_VARIANT,
    PARAM_STATUS,
    PARAM_STORAGE_USED,
    STATE_VARIANT_ENUM,
    STATE_VARIANT_NUMERIC,
)
from pyimouapi.ha_device import ImouHaDevice
from pytest_homeassistant_custom_component.common import MockConfigEntry


def _mock_coordinator(devices: list[ImouHaDevice]) -> MagicMock:
    coordinator = MagicMock()
    coordinator.devices = devices
    return coordinator


def _mock_device(sensors: dict[str, dict]) -> ImouHaDevice:
    device = MagicMock(spec=ImouHaDevice)
    device.sensors = sensors
    device.device_id = "d1"
    device.channel_id = "0"
    device.product_id = "ipc"
    device.channel_name = "Device 1"
    device.device_name = "Device 1"
    device.manufacturer = "Imou"
    device.model = "IPC"
    device.swversion = "1.0"
    return device


def test_iter_sensors_ignores_unknown_keys() -> None:
    """Unknown sensor keys from the API are not turned into entities."""
    device = _mock_device(
        {
            PARAM_BATTERY: {
                PARAM_STATE: 85,
                PARAM_STATE_VARIANT: STATE_VARIANT_NUMERIC,
            },
            "legacy_unknown_sensor": {PARAM_STATE: "1"},
        }
    )
    pairs = _iter_sensors(_mock_coordinator([device]))
    assert len(pairs) == 1
    assert pairs[0][0].key == PARAM_BATTERY


def test_storage_used_error_code_returns_none() -> None:
    """Storage error codes do not mix into the numeric storage_used state."""
    device = _mock_device(
        {
            PARAM_STATUS: {
                PARAM_STATE: "online",
                PARAM_STATE_VARIANT: STATE_VARIANT_ENUM,
            },
            PARAM_STORAGE_USED: {
                PARAM_STATE: "e1",
                PARAM_STATE_VARIANT: STATE_VARIANT_ENUM,
            },
        }
    )
    coordinator = _mock_coordinator([device])
    coordinator.devices_by_key = {"d1_0": device}
    entry = MockConfigEntry(domain=DOMAIN)
    sensor = ImouSensor(
        coordinator,
        entry,
        SENSOR_DESCRIPTIONS[PARAM_STORAGE_USED],
        device,
    )
    assert sensor.native_value is None


def test_storage_used_numeric_value() -> None:
    """Numeric storage used sensors expose the numeric state."""
    device = _mock_device(
        {
            PARAM_STATUS: {
                PARAM_STATE: "online",
                PARAM_STATE_VARIANT: STATE_VARIANT_ENUM,
            },
            PARAM_STORAGE_USED: {
                PARAM_STATE: 42,
                PARAM_STATE_VARIANT: STATE_VARIANT_NUMERIC,
            },
        }
    )
    coordinator = _mock_coordinator([device])
    coordinator.devices_by_key = {"d1_0": device}
    entry = MockConfigEntry(domain=DOMAIN)
    sensor = ImouSensor(
        coordinator,
        entry,
        SENSOR_DESCRIPTIONS[PARAM_STORAGE_USED],
        device,
    )
    assert sensor.native_value == 42


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("online", "online"),
        ("offline", "offline"),
    ],
)
def test_status_enum_native_value(state: str, expected: str) -> None:
    """Status sensor returns enum values unchanged."""
    device = _mock_device(
        {
            PARAM_STATUS: {PARAM_STATE: state, PARAM_STATE_VARIANT: STATE_VARIANT_ENUM},
        }
    )
    coordinator = _mock_coordinator([device])
    coordinator.devices_by_key = {"d1_0": device}
    entry = MockConfigEntry(domain=DOMAIN)
    sensor = ImouSensor(
        coordinator,
        entry,
        SENSOR_DESCRIPTIONS[PARAM_STATUS],
        device,
    )
    assert sensor.native_value == expected
