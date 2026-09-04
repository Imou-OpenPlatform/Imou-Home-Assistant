"""Base entity for Imou Life."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any, NoReturn, override

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pyimouapi.const import PARAM_STATE
from pyimouapi.exceptions import ImouException, InvalidAppIdOrSecretException
from pyimouapi.ha_device import DeviceStatus, ImouHaDevice

from .const import (
    DOMAIN,
    EVENT_IMOU_ALARM,
    PARAM_STATUS,
    imou_life_device_key,
    imou_life_device_keys_from_ids,
)
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .devices import imou_device_info, parent_device_key
from .repairs import async_notify_imou_api_error


class ImouEntity(CoordinatorEntity[ImouDataUpdateCoordinator]):
    """Base class for Imou entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ImouDataUpdateCoordinator,
        config_entry: ConfigEntry,
        entity_type: str,
        device: ImouHaDevice,
    ) -> None:
        """Initialize ImouEntity."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._entity_type = entity_type
        self._last_known_device = device
        self._device_key = imou_life_device_key(device)
        self._attr_unique_id = f"{self._device_key}${entity_type}"
        self._attr_translation_key = entity_type
        self._attr_device_info = imou_device_info(
            device, parent_device_key(coordinator.devices, device)
        )

    @property
    def device(self) -> ImouHaDevice:
        """Return the live device from the coordinator.

        A device dropped from the account leaves its entities behind until the
        registry catches up, and HA reads capability attributes before it reads
        `available`, so raising here would break the update for every entity
        after this one. The last known device keeps those reads answerable;
        `available` is what reports the device as gone.
        """
        device = self.coordinator.devices_by_key.get(self._device_key)
        if device is not None:
            self._last_known_device = device
        return self._last_known_device

    @property
    @override
    def available(self) -> bool:
        """Return True if entity is available."""
        if not super().available:
            return False
        if self._device_key not in self.coordinator.devices_by_key:
            return False
        if self._entity_type == PARAM_STATUS:
            return True
        if PARAM_STATUS not in self.device.sensors:
            return False
        return (
            self.device.sensors[PARAM_STATUS][PARAM_STATE] != DeviceStatus.OFFLINE.value
        )

    def _event_matches_this_device(self, event_data: dict[str, Any]) -> bool:
        """Return True when the push is for this entity's device key."""
        keys = imou_life_device_keys_from_ids(
            event_data.get("device_id"),
            event_data.get("channel_id"),
            event_data.get("product_id"),
        )
        return self._device_key in keys

    def _raise_imou_ha_error(
        self, err: ImouException, translation_key: str
    ) -> NoReturn:
        """Surface quota as a repair, then raise the translated HA error."""
        if isinstance(err, InvalidAppIdOrSecretException):
            self._config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="invalid_auth",
            ) from err
        async_notify_imou_api_error(self.hass, self._config_entry, err)
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key=translation_key,
            translation_placeholders={"error": err.message},
        ) from err


class ImouAlarmPushEntity(ImouEntity):
    """Hold on/off from ``EVENT_IMOU_ALARM`` until an off push or a timer.

    Subclasses set ``_hold_seconds`` and implement ``_alarm_state``.
    They must also set ``_attr_is_on`` on the leaf class so Home Assistant
    wraps it and invalidates the cached ``is_on``.
    """

    _hold_seconds: int

    def __init__(
        self,
        coordinator: ImouDataUpdateCoordinator,
        config_entry: ConfigEntry,
        entity_type: str,
        device: ImouHaDevice,
    ) -> None:
        """Initialize the push-hold entity."""
        super().__init__(coordinator, config_entry, entity_type, device)
        self._unsub_off: Callable[[], None] | None = None

    def _alarm_state(self, msg_type: str | None) -> bool | None:
        """Return True/False when this entity should change, else None."""
        raise NotImplementedError

    async def async_added_to_hass(self) -> None:
        """Listen for alarm pushes that belong to this device."""
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

    @callback
    def _set_push_state(self, is_on: bool, *, auto_off: bool) -> None:
        """Update held state and optionally schedule auto-off."""
        self._cancel_off_timer()
        self._attr_is_on = is_on
        if is_on and auto_off:
            self._unsub_off = async_call_later(
                self.hass, self._hold_seconds, self._async_auto_off
            )
        if self.platform is not None:
            self.async_write_ha_state()

    @callback
    def _async_auto_off(self, _now: datetime) -> None:
        """Clear the held state after the interval when the cloud sends no off."""
        self._unsub_off = None
        self._attr_is_on = False
        if self.platform is not None:
            self.async_write_ha_state()

    @callback
    def _async_handle_alarm(self, event: Event[dict[str, Any]]) -> None:
        """Turn on, hold, or clear from a classified Imou alarm push."""
        event_data = event.data
        if not self._event_matches_this_device(event_data):
            return
        state = self._alarm_state(event_data.get("msg_type"))
        if state is None:
            return
        self._set_push_state(state, auto_off=state)


@callback
def async_add_imou_entities(
    entry: ImouConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    entity_class: type[ImouEntity],
    iter_pairs: Callable[
        [ImouDataUpdateCoordinator], Iterable[tuple[Any, ImouHaDevice]]
    ],
) -> None:
    """Add entities for the known devices and for any discovered later.

    ``iter_pairs`` yields (entity type or description, device) for everything the
    platform supports; entities are then built only for the devices the
    coordinator has just reported, so a later discovery does not re-add the ones
    already present.
    """
    coordinator = entry.runtime_data.coordinator

    def _async_add(new_devices: list[ImouHaDevice]) -> None:
        device_keys = {imou_life_device_key(device) for device in new_devices}
        async_add_entities(
            entity_class(coordinator, entry, entity_key, device)
            for entity_key, device in iter_pairs(coordinator)
            if imou_life_device_key(device) in device_keys
        )

    coordinator.new_device_callbacks.append(_async_add)

    @callback
    def _remove_new_device_callback() -> None:
        if _async_add in coordinator.new_device_callbacks:
            coordinator.new_device_callbacks.remove(_async_add)

    entry.async_on_unload(_remove_new_device_callback)
    _async_add(coordinator.devices)
