import logging

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import ImouEntity

_LOGGER: logging.Logger = logging.getLogger(__package__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    imou_coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = []
    for device in imou_coordinator.devices:
        for sensor_type in device.sensors:
            sensor_entity = ImouSensor(imou_coordinator, entry, sensor_type, device)
            entities.append(sensor_entity)
    async_add_entities(entities)


class ImouSensor(ImouEntity,SensorEntity):
    """imou sensor."""

    @property
    def native_value(self):
        return self._device.sensors[self._entity_type]

    @property
    def device_class(self):
        match self._entity_type:
            case "battery":
                return SensorDeviceClass.BATTERY
            case "temperature_current":
                return SensorDeviceClass.TEMPERATURE
            case "humidity_current":
                return SensorDeviceClass.HUMIDITY
            case _:
                return None
