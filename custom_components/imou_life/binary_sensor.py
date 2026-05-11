"""Imou binary sensor entities."""

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorDeviceClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pyimouapi.const import PARAM_STATE

from .coordinator import ImouConfigEntry
from .entity import ImouEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ImouConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Imou binary_sensor entities."""
    coordinator = entry.runtime_data
    entities: list[ImouBinarySensor] = []
    for device in coordinator.devices:
        for binary_sensor_type in device.binary_sensors:
            entities.append(
                ImouBinarySensor(
                    coordinator,
                    entry,
                    binary_sensor_type,
                    device,
                )
            )
    if entities:
        async_add_entities(entities)


class ImouBinarySensor(ImouEntity, BinarySensorEntity):
    """Representation of an Imou binary sensor."""

    @property
    def is_on(self) -> bool | None:
        """Return True when the sensor is active."""
        return self._device.binary_sensors[self._entity_type][PARAM_STATE]

    @property
    def device_class(self) -> BinarySensorDeviceClass | None:
        """Return the device class when known."""
        match self._entity_type:
            case "door_contact_status":
                return BinarySensorDeviceClass.DOOR
            case _:
                return None
