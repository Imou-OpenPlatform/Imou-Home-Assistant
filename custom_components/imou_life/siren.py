"""Imou siren entities."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.components.siren import SirenEntity, SirenEntityFeature
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later
from pyimouapi.const import PARAM_SIREN_START, PARAM_SIREN_STOP
from pyimouapi.exceptions import ImouException
from pyimouapi.ha_device import ImouHaDevice

from .const import (
    DOMAIN,
    EVENT_IMOU_ALARM,
    PARAM_SIREN,
    SIREN_OFF_DELAY,
    imou_life_device_key,
    imou_life_device_keys_from_ids,
)
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .entity import ImouEntity, async_add_imou_entities

PARALLEL_UPDATES = 0

_SIREN_ON = frozenset({"sirenon"})
_SIREN_OFF = frozenset({"sirenoff", "siren_alarm_cleared"})


def siren_push_state(msg_type: str | None) -> bool | None:
    """Return True/False when msg_type reflects siren on/off, else None."""
    if not msg_type:
        return None
    key = msg_type.lower()
    if key.startswith("e_"):
        key = key[2:]
    if key in _SIREN_OFF:
        return False
    if key in _SIREN_ON:
        return True
    return None


def _iter_sirens(
    coordinator: ImouDataUpdateCoordinator,
) -> list[tuple[str, ImouHaDevice]]:
    """One siren entity per device that exposes manual start/stop."""
    return [
        (PARAM_SIREN, device)
        for device in coordinator.devices
        if PARAM_SIREN_START in device.buttons or PARAM_SIREN_STOP in device.buttons
    ]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Imou siren entities."""
    async_add_imou_entities(entry, async_add_entities, ImouSiren, _iter_sirens)


class ImouSiren(ImouEntity, SirenEntity):
    """Manual siren control; assumed on ~15s or until a sirenOff push."""

    _attr_supported_features = (
        SirenEntityFeature.TURN_ON | SirenEntityFeature.TURN_OFF
    )
    _attr_is_on = False

    def __init__(
        self,
        coordinator: ImouDataUpdateCoordinator,
        config_entry: ImouConfigEntry,
        entity_type: str,
        device: ImouHaDevice,
    ) -> None:
        """Initialize the siren."""
        super().__init__(coordinator, config_entry, entity_type, device)
        self._unsub_off: Callable[[], None] | None = None

    async def async_added_to_hass(self) -> None:
        """Listen for siren on/off pushes from the cloud."""
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

    def _event_matches_this_device(self, event_data: dict[str, Any]) -> bool:
        """Return True when the push is for this entity's device key."""
        keys = imou_life_device_keys_from_ids(
            event_data.get("device_id"),
            event_data.get("channel_id"),
            event_data.get("product_id"),
        )
        return imou_life_device_key(self.device) in keys

    @callback
    def _set_siren(self, is_on: bool, *, auto_off: bool) -> None:
        """Update siren state and optionally schedule auto-off."""
        self._cancel_off_timer()
        self._attr_is_on = is_on
        if is_on and auto_off:
            self._unsub_off = async_call_later(
                self.hass, SIREN_OFF_DELAY, self._async_auto_off
            )
        if self.platform is not None:
            self.async_write_ha_state()

    @callback
    def _async_auto_off(self, _now: datetime) -> None:
        """Clear siren after the hold interval when the cloud sends no off push."""
        self._unsub_off = None
        self._attr_is_on = False
        if self.platform is not None:
            self.async_write_ha_state()

    @callback
    def _async_handle_alarm(self, event: Event[dict[str, Any]]) -> None:
        """Sync from sirenOn / sirenOff alarm pushes."""
        event_data = event.data
        if not self._event_matches_this_device(event_data):
            return
        state = siren_push_state(event_data.get("msg_type"))
        if state is None:
            return
        self._set_siren(state, auto_off=state)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Sound the siren."""
        await self._async_siren_operation(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Silence the siren."""
        await self._async_siren_operation(False)

    async def _async_siren_operation(self, enable: bool) -> None:
        """Call the existing siren start/stop API path."""
        button_type = PARAM_SIREN_START if enable else PARAM_SIREN_STOP
        try:
            await self.coordinator.device_manager.async_press_button(
                self.device,
                button_type,
                0,
            )
        except ImouException as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="button_press_failed",
                translation_placeholders={"error": err.message},
            ) from err
        self._set_siren(enable, auto_off=enable)
