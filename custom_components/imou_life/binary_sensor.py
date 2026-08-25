"""Imou binary sensor entities."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later
from pyimouapi.const import PARAM_STATE
from pyimouapi.ha_device import ImouHaDevice

from .const import (
    EVENT_IMOU_ALARM,
    MOTION_OFF_DELAY,
    PARAM_MOTION,
    imou_life_device_key,
    imou_life_device_keys_from_ids,
)
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


class ImouMotionBinarySensor(ImouEntity, BinarySensorEntity):
    """Camera motion from Imou alarm pushes; auto-off if the cloud sends no clear."""

    _attr_device_class = BinarySensorDeviceClass.MOTION
    _attr_is_on = False

    def __init__(
        self,
        coordinator: ImouDataUpdateCoordinator,
        config_entry: ImouConfigEntry,
        entity_type: str,
        device: ImouHaDevice,
    ) -> None:
        """Initialize the motion sensor."""
        super().__init__(coordinator, config_entry, entity_type, device)
        self._unsub_off: Callable[[], None] | None = None

    async def async_added_to_hass(self) -> None:
        """Listen for alarm pushes that belong to this camera."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_IMOU_ALARM, self._async_handle_alarm)
        )
        self.async_on_remove(self._cancel_off_timer)

    @callback
    def _cancel_off_timer(self) -> None:
        """Drop a pending auto-off, if any."""
        if self._unsub_off is not None:
            self._unsub_off()
            self._unsub_off = None

    def _event_matches_this_camera(self, event_data: dict[str, Any]) -> bool:
        """Return True when the push is for this entity's device key."""
        keys = imou_life_device_keys_from_ids(
            event_data.get("device_id"),
            event_data.get("channel_id"),
            event_data.get("product_id"),
        )
        return imou_life_device_key(self.device) in keys

    @callback
    def _set_motion(self, is_on: bool, *, auto_off: bool) -> None:
        """Update motion state and optionally schedule auto-off."""
        self._cancel_off_timer()
        self._attr_is_on = is_on
        if is_on and auto_off:
            self._unsub_off = async_call_later(
                self.hass, MOTION_OFF_DELAY, self._async_auto_off
            )
        if self.platform is not None:
            self.async_write_ha_state()

    @callback
    def _async_auto_off(self, _now: datetime) -> None:
        """Clear motion after the hold interval."""
        self._unsub_off = None
        self._attr_is_on = False
        if self.platform is not None:
            self.async_write_ha_state()

    @callback
    def _async_handle_alarm(self, event: Event[dict[str, Any]]) -> None:
        """Turn on, hold, or clear from a classified Imou alarm push."""
        event_data = event.data
        if not self._event_matches_this_camera(event_data):
            return
        state = motion_binary_state(event_data.get("msg_type"))
        if state is None:
            return
        self._set_motion(state, auto_off=state)
