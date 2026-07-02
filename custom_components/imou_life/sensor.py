"""Imou sensor entities."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pyimouapi.const import PARAM_STATE
from pyimouapi.ha_device import ImouHaDevice

from .const import imou_life_device_key
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .entity import ImouEntity


def _iter_sensors(
    coordinator: ImouDataUpdateCoordinator,
) -> list[tuple[str, ImouHaDevice]]:
    """Return (sensor_type, device) pairs for supported sensors."""
    return [
        (sensor_type, device)
        for device in coordinator.devices
        for sensor_type in device.sensors
    ]


async def async_setup_entry(
    hass: HomeAssistant, entry: ImouConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Imou sensor entities."""
    coordinator = entry.runtime_data.coordinator

    def _async_add_sensors(new_devices: list[ImouHaDevice]) -> None:
        device_keys = {imou_life_device_key(device) for device in new_devices}
        async_add_entities(
            ImouSensor(coordinator, entry, sensor_type, device)
            for sensor_type, device in _iter_sensors(coordinator)
            if imou_life_device_key(device) in device_keys
        )

    coordinator.new_device_callbacks.append(_async_add_sensors)

    @callback
    def _remove_new_device_callback() -> None:
        if _async_add_sensors in coordinator.new_device_callbacks:
            coordinator.new_device_callbacks.remove(_async_add_sensors)

    entry.async_on_unload(_remove_new_device_callback)
    _async_add_sensors(coordinator.devices)


class ImouSensor(ImouEntity, SensorEntity):
    """Representation of an Imou sensor value."""

    @property
    def native_value(self) -> str | int | float | None:
        """Return the sensor value."""
        return self.device.sensors[self._entity_type][PARAM_STATE]

    @property
    def native_unit_of_measurement(self) -> str | None:
        match self._entity_type:
            case "battery":
                return "%"
            case "storage_used":
                if self.is_non_negative_number(self.native_value):
                    return "%"
                return None
            case "temperature_current":
                return "°C"
            case "humidity_current":
                return "%"
            case "power":
                return "W"
            case "voltage":
                return "V"
            case "current":
                return "A"
            case "use_electricity":
                return "kWh"
            case "use_time":
                return "min"
            case _:
                return None

    @property
    def device_class(self) -> SensorDeviceClass | None:
        match self._entity_type:
            case "battery":
                return SensorDeviceClass.BATTERY
            case "temperature_current":
                return SensorDeviceClass.TEMPERATURE
            case "humidity_current":
                return SensorDeviceClass.HUMIDITY
            case "power":
                return SensorDeviceClass.POWER
            case "voltage":
                return SensorDeviceClass.VOLTAGE
            case "current":
                return SensorDeviceClass.CURRENT
            case "use_electricity":
                return SensorDeviceClass.ENERGY
            case "use_time":
                return SensorDeviceClass.DURATION
            case _:
                return None

    @property
    def suggested_display_precision(self) -> int | None:
        match self._entity_type:
            case "battery":
                return 0
            case "temperature_current":
                return 1
            case "humidity_current":
                return 1
            case "storage_used":
                if self.is_non_negative_number(self.native_value):
                    return 0
                return None
            case _:
                return None
