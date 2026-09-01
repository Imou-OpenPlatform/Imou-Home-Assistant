"""Imou siren entities."""

from __future__ import annotations

from typing import Any

from homeassistant.components.siren import SirenEntity, SirenEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pyimouapi.const import PARAM_SIREN_START, PARAM_SIREN_STOP
from pyimouapi.exceptions import ImouException
from pyimouapi.ha_device import ImouHaDevice

from .const import PARAM_SIREN, SIREN_OFF_DELAY
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .entity import ImouAlarmPushEntity, async_add_imou_entities

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


class ImouSiren(ImouAlarmPushEntity, SirenEntity):
    """Manual siren control; assumed on ~15s or until a sirenOff push."""

    _attr_supported_features = SirenEntityFeature.TURN_ON | SirenEntityFeature.TURN_OFF
    _attr_is_on = False
    _hold_seconds = SIREN_OFF_DELAY

    def _alarm_state(self, msg_type: str | None) -> bool | None:
        """Map sirenOn / sirenOff pushes onto held state."""
        return siren_push_state(msg_type)

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
            self._raise_imou_ha_error(err, "button_press_failed")
        self._set_push_state(enable, auto_off=enable)
