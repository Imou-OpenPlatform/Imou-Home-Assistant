"""Imou binary sensor entities."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pyimouapi.const import PARAM_STATE
from pyimouapi.ha_device import ImouHaDevice

from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .entity import ImouEntity, async_add_imou_entities

PARALLEL_UPDATES = 0


def _iter_binary_sensors(
    coordinator: ImouDataUpdateCoordinator,
) -> list[tuple[str, ImouHaDevice]]:
    """Return (binary_sensor_type, device) pairs for supported binary sensors."""
    return [
        (binary_sensor_type, device)
        for device in coordinator.devices
        for binary_sensor_type in device.binary_sensors
    ]


async def async_setup_entry(
    hass: HomeAssistant, entry: ImouConfigEntry, async_add_entities: AddEntitiesCallback
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
