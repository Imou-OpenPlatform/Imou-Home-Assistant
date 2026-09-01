"""Imou alarm control panel for IoT arming mode."""

from __future__ import annotations

import logging

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pyimouapi.const import PARAM_STATE
from pyimouapi.exceptions import ImouException
from pyimouapi.ha_device import ImouHaDevice

from .const import PARAM_MODE
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .entity import ImouEntity, async_add_imou_entities

_LOGGER = logging.getLogger(__package__)

PARALLEL_UPDATES = 0

_MODE_TO_STATE = {
    "home": AlarmControlPanelState.ARMED_HOME,
    "away": AlarmControlPanelState.ARMED_AWAY,
    "disarm": AlarmControlPanelState.DISARMED,
}


def _iter_alarm_control_panels(
    coordinator: ImouDataUpdateCoordinator,
) -> list[tuple[str, ImouHaDevice]]:
    """Return (entity_type, device) pairs for devices with arming panel."""
    return [
        (PARAM_MODE, device)
        for device in coordinator.devices
        if device.alarm_control_panel is not None
    ]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Imou alarm control panel entities."""
    async_add_imou_entities(
        entry, async_add_entities, ImouAlarmControlPanel, _iter_alarm_control_panels
    )


class ImouAlarmControlPanel(ImouEntity, AlarmControlPanelEntity):
    """Representation of an Imou arming panel."""

    _attr_code_arm_required = False
    _attr_supported_features = (
        AlarmControlPanelEntityFeature.ARM_HOME
        | AlarmControlPanelEntityFeature.ARM_AWAY
    )

    def __init__(
        self,
        coordinator: ImouDataUpdateCoordinator,
        config_entry: ImouConfigEntry,
        entity_type: str,
        device: ImouHaDevice,
    ) -> None:
        """Initialize ImouAlarmControlPanel."""
        super().__init__(coordinator, config_entry, entity_type, device)
        # ImouEntity.__init__ sets this from entity_type ("mode").
        self._attr_translation_key = "arming"

    @property
    def alarm_state(self) -> AlarmControlPanelState | None:
        """Return the current arming state."""
        panel = self.device.alarm_control_panel
        if not panel:
            return None
        mode = panel.get(PARAM_STATE)
        mapped = _MODE_TO_STATE.get(mode)
        if mapped is None:
            _LOGGER.debug("Unknown alarm mode %r for %s", mode, self._device_key)
            return None
        return mapped

    async def _async_set_mode(self, mode: str) -> None:
        """Send arming mode change to the cloud API."""
        try:
            await self.coordinator.device_manager.async_set_alarm_mode(
                self.device, mode
            )
        except ImouException as err:
            self._raise_imou_ha_error(err, "alarm_arm_disarm_failed")
        self.async_write_ha_state()

    async def async_alarm_arm_home(self, code: str | None = None) -> None:
        """Arm the device in home mode."""
        await self._async_set_mode("home")

    async def async_alarm_arm_away(self, code: str | None = None) -> None:
        """Arm the device in away mode."""
        await self._async_set_mode("away")

    async def async_alarm_disarm(self, code: str | None = None) -> None:
        """Disarm the device."""
        await self._async_set_mode("disarm")
