"""Imou call event entities."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pyimouapi.ha_device import ImouHaDevice

from .const import EVENT_IMOU_ALARM, PARAM_CALL
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .entity import ImouEntity, async_add_imou_entities
from .helpers import alarm_push_active

PARALLEL_UPDATES = 0

_DOORBELL_CALL = frozenset(
    {
        "callbellevent",
        "call.session.called",
        "callevent",
        "callquickevent",
        "callsupplyevent",
        "calleventcall",
        "309100",
        "309200",
        "311000",
    }
)


def is_call_msg_type(msg_type: str | None) -> bool:
    """Return True when msg_type is an incoming call."""
    if not msg_type:
        return False
    key = msg_type.lower()
    if key.startswith("e_"):
        key = key[2:]
    return key in _DOORBELL_CALL


_CALL_SESSION_EVENT = "call.session.called"
_CALL_SESSION_SERVICES = frozenset(
    {
        "call.session.answer",
        "call.session.hangup",
        "call.session.refuse",
    }
)


def _iter_call_events(
    coordinator: ImouDataUpdateCoordinator,
) -> list[tuple[str, ImouHaDevice]]:
    """One call event per channel that reports a call feature."""
    return [
        (PARAM_CALL, device)
        for device in coordinator.devices
        if _device_offers_call(device)
    ]


def _device_offers_call(device: ImouHaDevice) -> bool:
    """True when this channel lists an incoming-call event or call controls."""
    if device.channel_id is None:
        return False
    capability_ids = device.capability_ids
    if capability_ids is None:
        capability = device.capability_set
        if capability is None:
            return False
        if _CALL_SESSION_EVENT in capability.events:
            return True
        return bool(_CALL_SESSION_SERVICES.intersection(capability.services))
    if _CALL_SESSION_EVENT in capability_ids:
        return True
    return bool(_CALL_SESSION_SERVICES.intersection(capability_ids))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Imou event entities."""
    async_add_imou_entities(entry, async_add_entities, ImouCallEvent, _iter_call_events)


class ImouCallEvent(ImouEntity, EventEntity):
    """Incoming call. Not a doorbell: recorders and cameras share this."""

    def __init__(
        self,
        coordinator: ImouDataUpdateCoordinator,
        config_entry: ImouConfigEntry,
        entity_type: str,
        device: ImouHaDevice,
    ) -> None:
        """Initialize the call event entity."""
        super().__init__(coordinator, config_entry, entity_type, device)
        self._attr_event_types = ["call"]

    @property
    def available(self) -> bool:
        """Unavailable when alarm push is off: we cannot hear a call."""
        return super().available and alarm_push_active(self._config_entry)

    async def async_added_to_hass(self) -> None:
        """Listen for alarm pushes that belong to this device."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_IMOU_ALARM, self._async_handle_alarm)
        )

    @callback
    def _async_handle_alarm(self, event: Event[dict[str, Any]]) -> None:
        """Fire when a classified call push matches this channel."""
        event_data = event.data
        if not self._event_matches_this_device(event_data):
            return
        msg_type = event_data.get("msg_type")
        if not is_call_msg_type(msg_type):
            return
        self._trigger_event("call", {"msg_type": msg_type})
        if self.platform is not None:
            self.async_write_ha_state()
