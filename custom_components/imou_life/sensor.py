"""Imou sensor entities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from pyimouapi.const import PARAM_STATE, PARAM_STATE_VARIANT, STATE_VARIANT_NUMERIC
from pyimouapi.ha_device import ImouHaDevice

from .const import (
    AI_INSIGHT_STATUS_OPTIONS,
    CLOUD_STORAGE_STATUS_OPTIONS,
    PARAM_AI_INSIGHT_STATUS,
    PARAM_CLOUD_STORAGE_STATUS,
    PARAM_INSIGHT_BRIEFING,
    PARAM_INSIGHT_SEARCH_RESULT,
    PARAM_STATUS,
    PARAM_STORAGE_USED,
)
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .entity import ImouEntity, async_add_imou_entities
from .helpers import camera_channel_devices
from .insight_account import ImouAccountEntity
from .runtime_data import get_runtime_data

PARALLEL_UPDATES = 0

STATUS_OPTIONS = ["online", "offline", "sleep", "upgrading"]


@dataclass(frozen=True, kw_only=True)
class ImouSensorEntityDescription(SensorEntityDescription):
    """Describes an Imou sensor entity."""


SENSOR_DESCRIPTIONS: dict[str, ImouSensorEntityDescription] = {
    PARAM_STATUS: ImouSensorEntityDescription(
        key=PARAM_STATUS,
        translation_key=PARAM_STATUS,
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=STATUS_OPTIONS,
    ),
    "battery": ImouSensorEntityDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=0,
    ),
    "storage_used": ImouSensorEntityDescription(
        key="storage_used",
        translation_key="storage_used",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        suggested_display_precision=0,
    ),
    "temperature_current": ImouSensorEntityDescription(
        key="temperature_current",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    "humidity_current": ImouSensorEntityDescription(
        key="humidity_current",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    "power": ImouSensorEntityDescription(
        key="power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "voltage": ImouSensorEntityDescription(
        key="voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "current": ImouSensorEntityDescription(
        key="current",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    "switch_cnt": ImouSensorEntityDescription(
        key="switch_cnt",
        translation_key="switch_cnt",
        state_class=SensorStateClass.TOTAL,
    ),
    "use_electricity": ImouSensorEntityDescription(
        key="use_electricity",
        translation_key="use_electricity",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    "use_time": ImouSensorEntityDescription(
        key="use_time",
        translation_key="use_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    PARAM_AI_INSIGHT_STATUS: ImouSensorEntityDescription(
        key=PARAM_AI_INSIGHT_STATUS,
        translation_key=PARAM_AI_INSIGHT_STATUS,
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=list(AI_INSIGHT_STATUS_OPTIONS),
    ),
    PARAM_CLOUD_STORAGE_STATUS: ImouSensorEntityDescription(
        key=PARAM_CLOUD_STORAGE_STATUS,
        translation_key=PARAM_CLOUD_STORAGE_STATUS,
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=list(CLOUD_STORAGE_STATUS_OPTIONS),
    ),
}


def _iter_sensors(
    coordinator: ImouDataUpdateCoordinator,
) -> list[tuple[ImouSensorEntityDescription, ImouHaDevice]]:
    """Return (description, device) pairs for supported sensors."""
    standard = [
        (SENSOR_DESCRIPTIONS[sensor_type], device)
        for device in coordinator.devices
        for sensor_type in device.sensors
        if sensor_type in SENSOR_DESCRIPTIONS
    ]
    insight: list[tuple[ImouSensorEntityDescription, ImouHaDevice]] = []
    for device in camera_channel_devices(coordinator.devices):
        insight.append((SENSOR_DESCRIPTIONS[PARAM_AI_INSIGHT_STATUS], device))
        insight.append((SENSOR_DESCRIPTIONS[PARAM_CLOUD_STORAGE_STATUS], device))
    return standard + insight


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Imou sensor entities."""
    async_add_imou_entities(entry, async_add_entities, ImouSensor, _iter_sensors)
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        [
            ImouInsightSearchSensor(coordinator, entry),
            ImouInsightBriefingSensor(coordinator, entry),
        ]
    )


class ImouInsightSearchSensor(ImouAccountEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: ImouDataUpdateCoordinator,
        config_entry: ImouConfigEntry,
    ) -> None:
        super().__init__(coordinator, config_entry, PARAM_INSIGHT_SEARCH_RESULT)
        runtime = get_runtime_data(config_entry)
        if runtime is not None:
            runtime.workbench_search_sensor = self

    @callback
    def set_markdown(self, summary: str, markdown: str) -> None:
        self._attr_native_value = summary[:255]
        self._attr_extra_state_attributes = {"markdown": markdown}
        self.async_write_ha_state()


class ImouInsightBriefingSensor(ImouAccountEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: ImouDataUpdateCoordinator,
        config_entry: ImouConfigEntry,
    ) -> None:
        super().__init__(coordinator, config_entry, PARAM_INSIGHT_BRIEFING)
        runtime = get_runtime_data(config_entry)
        if runtime is not None:
            runtime.workbench_briefing_sensor = self

    @callback
    def set_briefing(self, summary: str, markdown: str) -> None:
        self._attr_native_value = summary[:255]
        self._attr_extra_state_attributes = {"markdown": markdown}
        self.async_write_ha_state()


class ImouSensor(ImouEntity, SensorEntity):
    """Representation of an Imou sensor value."""

    entity_description: ImouSensorEntityDescription

    def __init__(
        self,
        coordinator: ImouDataUpdateCoordinator,
        config_entry: ImouConfigEntry,
        description: ImouSensorEntityDescription,
        device: ImouHaDevice,
    ) -> None:
        """Initialize Imou sensor."""
        super().__init__(coordinator, config_entry, description.key, device)
        self.entity_description = description
        if description.translation_key is None:
            self._attr_translation_key = None

    @property
    def _is_numeric_variant(self) -> bool:
        """Return True when the sensor value is numeric."""
        return (
            self.device.sensors[self._entity_type].get(PARAM_STATE_VARIANT)
            == STATE_VARIANT_NUMERIC
        )

    def _storage_status_text(self) -> bool:
        """A card fault is text. A used percentage stays a measurement."""
        if self._entity_type != PARAM_STORAGE_USED:
            return False
        value = self.device.sensors[self._entity_type].get(PARAM_STATE)
        return isinstance(value, str)

    @property
    @override
    def native_unit_of_measurement(self) -> str | None:
        # Home Assistant rejects a text state on a sensor that still has a unit.
        if self._storage_status_text():
            return None
        return self.entity_description.native_unit_of_measurement

    @property
    @override
    def state_class(self) -> SensorStateClass | str | None:
        if self._storage_status_text():
            return None
        return self.entity_description.state_class

    @property
    @override
    def suggested_display_precision(self) -> int | None:
        if self._storage_status_text():
            return None
        return self.entity_description.suggested_display_precision

    @property
    @override
    def native_value(self) -> StateType:
        """Return the sensor value."""
        if self._entity_type == PARAM_AI_INSIGHT_STATUS:
            return self._ai_insight_state()
        if self._entity_type == PARAM_CLOUD_STORAGE_STATUS:
            return self._cloud_storage_state()
        value = self.device.sensors[self._entity_type].get(PARAM_STATE)
        if value is None:
            return None
        if self._entity_type == PARAM_STORAGE_USED:
            if isinstance(value, str):
                return value
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return value
            return None
        if self.entity_description.device_class == SensorDeviceClass.ENUM:
            return value
        if not self._is_numeric_variant:
            return None
        return value

    def _ai_insight_state(self) -> str:
        return self.device.ai_insight_label

    def _cloud_storage_state(self) -> str:
        return self.device.cloud_storage_label
