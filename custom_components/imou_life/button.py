"""Imou button entities."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pyimouapi.const import PARAM_DURATION
from pyimouapi.exceptions import ImouException

from .const import (
    PARAM_ENTITY_ID,
    PARAM_PTZ,
    PARAM_RESTART_DEVICE,
    PARAM_ROTATION_DURATION,
    SERVICE_CONTROL_MOVE_PTZ,
    SERVICE_RESTART_DEVICE,
)
from .coordinator import ImouConfigEntry
from .entity import ImouEntity

_LOGGER = logging.getLogger(__package__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ImouConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Imou button entities."""
    coordinator = entry.runtime_data
    entities: list[ImouButton] = []
    for device in coordinator.devices:
        for button_type in device.buttons:
            entities.append(
                ImouButton(
                    coordinator,
                    entry,
                    button_type,
                    device,
                )
            )
    if entities:
        async_add_entities(entities)

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_CONTROL_MOVE_PTZ,
        {
            vol.Required(PARAM_ENTITY_ID): cv.entity_id,
            vol.Required(PARAM_DURATION, default=500): vol.All(
                vol.Coerce(int), vol.Range(min=100, max=10000)
            ),
        },
        "async_handle_control_move_ptz",
    )
    platform.async_register_entity_service(
        SERVICE_RESTART_DEVICE,
        {
            vol.Required(PARAM_ENTITY_ID): cv.entity_id,
        },
        "async_handle_restart_device",
    )


class ImouButton(ImouEntity, ButtonEntity):
    """Representation of an Imou button."""

    async def async_press(self) -> None:
        """Handle the button press."""
        await self._async_do_press(
            self._config_entry.options.get(PARAM_ROTATION_DURATION, 500)
        )

    @property
    def device_class(self) -> ButtonDeviceClass | None:
        """Return restart class for the reboot button."""
        if self._entity_type == PARAM_RESTART_DEVICE:
            return ButtonDeviceClass.RESTART
        return None

    async def async_handle_control_move_ptz(self, duration: int) -> None:
        """Service: move PTZ for the given duration."""
        _LOGGER.debug(
            "PTZ move for %ss on entity type %s", duration, self._entity_type
        )
        if PARAM_PTZ not in self._entity_type:
            raise HomeAssistantError(
                f"Invalid entity type {self._entity_type}; expected PTZ button"
            )
        await self._async_do_press(duration)

    async def async_handle_restart_device(self) -> None:
        """Service: restart the device."""
        _LOGGER.debug("Restart device for entity type %s", self._entity_type)
        if self._entity_type != PARAM_RESTART_DEVICE:
            raise HomeAssistantError(
                f"Invalid entity type {self._entity_type}; expected restart button"
            )
        await self._async_do_press(0)

    async def _async_do_press(self, duration: int) -> None:
        """Send button command to the cloud API."""
        try:
            await self._coordinator.device_manager.async_press_button(
                self._device,
                self._entity_type,
                duration,
            )
        except ImouException as e:
            raise HomeAssistantError(e.message) from e
