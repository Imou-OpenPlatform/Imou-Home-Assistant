"""Tests pinning which entities are filed under a device's configuration."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from custom_components.imou_life.button import ImouButton
from custom_components.imou_life.const import (
    PARAM_RESTART_DEVICE,
    PARAM_STATUS,
)
from custom_components.imou_life.select import SELECT_TYPES
from custom_components.imou_life.sensor import SENSOR_DESCRIPTIONS
from custom_components.imou_life.switch import SWITCH_TYPES
from custom_components.imou_life.text import ImouText
from homeassistant.const import EntityCategory

# Moving a key in or out of these sets moves the entity between a device page's
# main controls and its configuration section, and changes whether voice
# assistants expose it. Update deliberately, not to make a test pass.
CONFIG_SWITCHES = {
    "ab_alarm_sound",
    "audio_encode_control",
    "frame_reverse",
    "header_detect",
    "light",
    "motion_detect",
    "pet_detect",
    "smart_track",
    "wide_dynamic",
}
CONFIG_SELECTS = {"device_volume", "mode", "night_vision_mode"}
DIAGNOSTIC_SENSORS = {PARAM_STATUS, "battery", "storage_used"}


def _categorised(descriptions, category: EntityCategory) -> set[str]:
    return {
        description.key
        for description in descriptions
        if description.entity_category is category
    }


def test_switch_configuration_split() -> None:
    """Detection and indicator toggles are configuration, controls are not."""
    assert _categorised(SWITCH_TYPES, EntityCategory.CONFIG) == CONFIG_SWITCHES
    remaining = {d.key for d in SWITCH_TYPES} - CONFIG_SWITCHES
    assert remaining == {"close_camera", "switch", "white_light"}


def test_select_configuration_split() -> None:
    """The PTZ preset stays a control; the other selects are settings."""
    assert _categorised(SELECT_TYPES, EntityCategory.CONFIG) == CONFIG_SELECTS
    assert {d.key for d in SELECT_TYPES} - CONFIG_SELECTS == {"collection_point"}


def test_sensors_are_measurements_or_diagnostics() -> None:
    """Only device health readings are diagnostic; measurements stay primary."""
    assert (
        _categorised(SENSOR_DESCRIPTIONS.values(), EntityCategory.DIAGNOSTIC)
        == DIAGNOSTIC_SENSORS
    )
    assert not _categorised(SENSOR_DESCRIPTIONS.values(), EntityCategory.CONFIG)


def test_text_entities_are_configuration() -> None:
    """Text entities hold thresholds and timers, never live readings."""
    # Home Assistant's metaclass turns _attr_* into properties, so the effective
    # value only shows up on an instance.
    text = object.__new__(ImouText)
    assert text.entity_category is EntityCategory.CONFIG


@pytest.mark.parametrize(
    ("entity_type", "expected"),
    [
        (PARAM_RESTART_DEVICE, EntityCategory.CONFIG),
        ("ptz_up", None),
        ("siren_start", None),
        ("mute", None),
    ],
)
def test_button_configuration_split(
    entity_type: str, expected: EntityCategory | None
) -> None:
    """Only the reboot button is configuration; the rest are actions."""
    button = MagicMock(spec=ImouButton)
    button._entity_type = entity_type
    assert ImouButton.entity_category.fget(button) is expected
