"""Imou number entities."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pyimouapi.const import PARAM_REF, PARAM_STATE
from pyimouapi.exceptions import ImouException
from pyimouapi.ha_device import ImouHaDevice

from .const import (
    COUNT_DOWN_MAX_MINUTES,
    OVERCHARGE_HIGH_REF,
    OVERCHARGE_MAX_WATTS_DEFAULT,
    OVERCHARGE_MAX_WATTS_HIGH,
    OVERCHARGE_MIN_WATTS,
    PARAM_COUNT_DOWN_SWITCH,
    PARAM_OVERCHARGE_SWITCH,
)
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .countdown import CountdownTracker, parse_countdown_minutes
from .entity import ImouEntity, async_add_imou_entities

PARALLEL_UPDATES = 0


def _iter_countdowns(
    coordinator: ImouDataUpdateCoordinator,
) -> list[tuple[str, ImouHaDevice]]:
    """Return countdown numbers for plugs that expose the timer."""
    return [
        (PARAM_COUNT_DOWN_SWITCH, device)
        for device in coordinator.devices
        if PARAM_COUNT_DOWN_SWITCH in device.texts
    ]


def _iter_overcharge(
    coordinator: ImouDataUpdateCoordinator,
) -> list[tuple[str, ImouHaDevice]]:
    """Return max-power numbers for plugs that expose the limit."""
    return [
        (PARAM_OVERCHARGE_SWITCH, device)
        for device in coordinator.devices
        if PARAM_OVERCHARGE_SWITCH in device.texts
    ]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Imou number entities."""
    async_add_imou_entities(
        entry, async_add_entities, ImouCountdownNumber, _iter_countdowns
    )
    async_add_imou_entities(
        entry, async_add_entities, ImouOverchargeNumber, _iter_overcharge
    )


class ImouCountdownNumber(ImouEntity, NumberEntity):
    """Plug switch delay in minutes; remaining counts down on this Home Assistant."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = NumberDeviceClass.DURATION
    _attr_native_min_value = 0
    _attr_native_max_value = COUNT_DOWN_MAX_MINUTES
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_mode = NumberMode.BOX

    @property
    def _tracker(self) -> CountdownTracker:
        return self._config_entry.runtime_data.countdown

    @property
    def native_value(self) -> float:
        """Return remaining minutes from the local clock, else the device value."""
        if self._tracker.ends_at(self._device_key) is not None:
            return float(self._tracker.remaining_minutes(self._device_key))
        return float(parse_countdown_minutes(self.device))

    async def async_set_native_value(self, value: float) -> None:
        """Write minutes to the device and start the local remaining clock."""
        minutes = int(value)
        try:
            await self.coordinator.device_manager.async_set_text_value(
                self.device,
                PARAM_COUNT_DOWN_SWITCH,
                str(minutes),
            )
        except ImouException as err:
            self._raise_imou_ha_error(err, "set_text_value_failed")
        self._tracker.start_minutes(
            self.hass, self.coordinator, self._device_key, minutes
        )
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Continue a countdown that the last status refresh already reported."""
        await super().async_added_to_hass()
        self._tracker.seed_if_idle(self.hass, self.coordinator, self.device)


class ImouOverchargeNumber(ImouEntity, NumberEntity):
    """Plug max power in watts."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = NumberDeviceClass.POWER
    _attr_native_min_value = OVERCHARGE_MIN_WATTS
    _attr_native_step = 1
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_mode = NumberMode.BOX

    @property
    def native_max_value(self) -> float:
        """Higher-capacity plugs accept a larger watt limit."""
        ref = self.device.texts[PARAM_OVERCHARGE_SWITCH].get(PARAM_REF)
        if ref == OVERCHARGE_HIGH_REF:
            return float(OVERCHARGE_MAX_WATTS_HIGH)
        return float(OVERCHARGE_MAX_WATTS_DEFAULT)

    @property
    def native_value(self) -> float | None:
        """Return the configured watt limit."""
        raw = self.device.texts[PARAM_OVERCHARGE_SWITCH][PARAM_STATE]
        try:
            return float(int(raw))
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        """Write the watt limit to the device."""
        watts = int(value)
        try:
            await self.coordinator.device_manager.async_set_text_value(
                self.device,
                PARAM_OVERCHARGE_SWITCH,
                str(watts),
            )
        except ImouException as err:
            self._raise_imou_ha_error(err, "set_text_value_failed")
        self.async_write_ha_state()
