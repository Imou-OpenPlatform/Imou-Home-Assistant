"""Imou binary sensor entities."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pyimouapi.const import PARAM_STATE
from pyimouapi.ha_device import ImouHaDevice

from .const import PARAM_MOTION
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .entity import ImouEntity, async_add_imou_entities

PARALLEL_UPDATES = 0

_MOTION_ON = frozenset(
    {
        "videomotion",
        "human",
        "mobiledetect",
        "alarmpir",
        "pir_alarm",
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
    """One HA-only motion sensor per camera channel."""
    return [
        (PARAM_MOTION, device)
        for device in coordinator.devices
        if device.channel_id is not None
    ]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Imou binary_sensor entities."""
    async_add_imou_entities(
        entry, async_add_entities, ImouBinarySensor, _iter_binary_sensors
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
