"""Imou binary sensor entities."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pyimouapi.const import PARAM_STATE
from pyimouapi.ha_device import ImouHaDevice

from .const import imou_life_device_key
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .entity import ImouEntity


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
    coordinator = entry.runtime_data.coordinator

    def _async_add_binary_sensors(new_devices: list[ImouHaDevice]) -> None:
        device_keys = {imou_life_device_key(device) for device in new_devices}
        async_add_entities(
            ImouBinarySensor(coordinator, entry, binary_sensor_type, device)
            for binary_sensor_type, device in _iter_binary_sensors(coordinator)
            if imou_life_device_key(device) in device_keys
        )

    coordinator.new_device_callbacks.append(_async_add_binary_sensors)

    @callback
    def _remove_new_device_callback() -> None:
        if _async_add_binary_sensors in coordinator.new_device_callbacks:
            coordinator.new_device_callbacks.remove(_async_add_binary_sensors)

    entry.async_on_unload(_remove_new_device_callback)
    _async_add_binary_sensors(coordinator.devices)


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
