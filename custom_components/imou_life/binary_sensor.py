"""Imou binary sensor entities."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pyimouapi.capability.binding import binding_supported, cached_v2_target_map
from pyimouapi.const import PARAM_STATE
from pyimouapi.ha_device import ImouHaDevice

from .const import MOTION_OFF_DELAY, PARAM_MOTION
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .entity import ImouAlarmPushEntity, ImouEntity, async_add_imou_entities
from .helpers import alarm_push_active

PARALLEL_UPDATES = 0

# Picture change and person detect, including each binding's fallback target.
_MOTION_CAPABILITY_KEYS = frozenset({"motion_detect", "header_detect"})

_MOTION_ON = frozenset(
    {
        "videomotion",
        "detection.motion.detected",
        "human",
        "detection.person.detected",
        "mobiledetect",
        "motiondetect",
        "alarmpir",
        "pir_alarm",
        "aiperarea",
        "aiperareaalarm",
        "multivideoaiperarea",
        "multivideoaiperareaalarm",
        "smartmixdetect",
        "crosslinedetection",
        "areadetect",
        "areadetectalarm",
        "multivideoareadetect",
        "multivideodetectalarm",
    }
)
_MOTION_OFF = frozenset(
    {
        "clearalarmpir",
        "pir_cleared",
    }
)


def motion_binary_state(msg_type: str | None) -> bool | None:
    """Return True/False when msg_type drives motion, else None."""
    if not msg_type:
        return None
    key = msg_type.lower()
    if key.startswith("e_"):
        key = key[2:]
    if key in _MOTION_OFF:
        return False
    if key in _MOTION_ON:
        return True
    return None


def _iter_binary_sensors(
    coordinator: ImouDataUpdateCoordinator,
) -> list[tuple[str, ImouHaDevice]]:
    """Return (binary_sensor_type, device) pairs for supported binary sensors."""
    return [
        (binary_sensor_type, device)
        for device in coordinator.devices
        for binary_sensor_type in device.binary_sensors
    ]


def _iter_motion_sensors(
    coordinator: ImouDataUpdateCoordinator,
) -> list[tuple[str, ImouHaDevice]]:
    """One motion sensor per channel that reports picture or person detect."""
    return [
        (PARAM_MOTION, device)
        for device in coordinator.devices
        if _device_offers_motion(device)
    ]


def _device_offers_motion(device: ImouHaDevice) -> bool:
    """True when this channel's capability ids include picture or person detect."""
    if device.channel_id is None:
        return False
    capability_ids = device.capability_ids
    if not capability_ids:
        return False
    return any(
        binding_supported(binding, capability_ids)
        for binding in cached_v2_target_map()
        if binding.ha_key in _MOTION_CAPABILITY_KEYS
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Imou binary_sensor entities."""
    async_add_imou_entities(
        entry, async_add_entities, ImouBinarySensor, _iter_binary_sensors
    )
    async_add_imou_entities(
        entry, async_add_entities, ImouMotionBinarySensor, _iter_motion_sensors
    )


class ImouBinarySensor(ImouEntity, BinarySensorEntity):
    """Representation of an Imou binary sensor."""

    @property
    def is_on(self) -> bool | None:
        """Return True when the sensor is active."""
        return self.device.binary_sensors[self._entity_type][PARAM_STATE]

    @property
    def device_class(self) -> BinarySensorDeviceClass | None:
        """Return the device class when known."""
        match self._entity_type:
            case "door_contact_status":
                return BinarySensorDeviceClass.DOOR
            case _:
                return None


class ImouMotionBinarySensor(ImouAlarmPushEntity, BinarySensorEntity):
    """Camera motion from alarm pushes. Holds Detected, then returns to Clear."""

    _attr_device_class = BinarySensorDeviceClass.MOTION
    _attr_is_on = False
    _hold_seconds = MOTION_OFF_DELAY

    @property
    def available(self) -> bool:
        """Unavailable when alarm push is off: Clear would mean no motion."""
        return super().available and alarm_push_active(self._config_entry)

    def _alarm_state(self, msg_type: str | None) -> bool | None:
        """Map picture / human / PIR pushes onto held motion state."""
        return motion_binary_state(msg_type)
