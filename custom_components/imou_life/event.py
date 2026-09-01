"""Imou doorbell / incoming-call event entities."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventDeviceClass, EventEntity
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pyimouapi.ha_device import ImouHaDevice

from .const import (
    EVENT_IMOU_ALARM,
    PARAM_DOORBELL,
    imou_life_device_key,
    imou_life_device_keys_from_ids,
)
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .entity import ImouEntity, async_add_imou_entities
from .helpers import (
    PAAS_CALL_ABILITY,
    alarm_push_active,
    device_has_paas_ability,
    device_iot_events_match,
)

PARALLEL_UPDATES = 0

_DOORBELL_CALL = frozenset(
    {
        "callbellevent",
        "callevent",
        "callquickevent",
        "callsupplyevent",
        "calleventcall",
        "309100",
        "309200",
        "311000",
    }
)


def is_doorbell_call_msg_type(msg_type: str | None) -> bool:
    """Return True when msg_type is an incoming call / doorbell press."""
    if not msg_type:
        return False
    key = msg_type.lower()
    if key.startswith("e_"):
        key = key[2:]
    return key in _DOORBELL_CALL


def _iter_doorbell_events(
    coordinator: ImouDataUpdateCoordinator,
) -> list[tuple[str, ImouHaDevice]]:
    """One HA-only doorbell event per camera that reports a call feature."""
    return [
        (PARAM_DOORBELL, device)
        for device in coordinator.devices
        if _device_offers_doorbell(coordinator, device)
    ]


def _device_offers_doorbell(
    coordinator: ImouDataUpdateCoordinator, device: ImouHaDevice
) -> bool:
    """PaaS: CallAbility. IoT: product-model call events, not every camera."""
    if device.channel_id is None:
        return False
    if device.product_id:
        return device_iot_events_match(coordinator, device, is_doorbell_call_msg_type)
    return device_has_paas_ability(device, PAAS_CALL_ABILITY)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Imou event entities."""
    async_add_imou_entities(
        entry, async_add_entities, ImouDoorbellEvent, _iter_doorbell_events
    )


class ImouDoorbellEvent(ImouEntity, EventEntity):
    """Camera doorbell from Imou call pushes; event type is HA ring."""

    _attr_device_class = EventDeviceClass.DOORBELL

    def __init__(
        self,
        coordinator: ImouDataUpdateCoordinator,
        config_entry: ImouConfigEntry,
        entity_type: str,
        device: ImouHaDevice,
    ) -> None:
        """Initialize the doorbell event entity."""
        super().__init__(coordinator, config_entry, entity_type, device)
        self._attr_event_types = ["ring"]

    @property
    def available(self) -> bool:
        """Unavailable when alarm push is off: we cannot hear a press."""
        return super().available and alarm_push_active(self._config_entry)

    async def async_added_to_hass(self) -> None:
        """Listen for alarm pushes that belong to this device."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_IMOU_ALARM, self._async_handle_alarm)
        )

    def _event_matches_this_device(self, event_data: dict[str, Any]) -> bool:
        """Return True when the push is for this entity's device key."""
        keys = imou_life_device_keys_from_ids(
            event_data.get("device_id"),
            event_data.get("channel_id"),
            event_data.get("product_id"),
        )
        return imou_life_device_key(self.device) in keys

    @callback
    def _async_handle_alarm(self, event: Event[dict[str, Any]]) -> None:
        """Fire ring when a classified call push matches this camera."""
        event_data = event.data
        if not self._event_matches_this_device(event_data):
            return
        msg_type = event_data.get("msg_type")
        if not is_doorbell_call_msg_type(msg_type):
            return
        self._trigger_event("ring", {"msg_type": msg_type})
        if self.platform is not None:
            self.async_write_ha_state()
