"""Imou button entities."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pyimouapi.const import PARAM_DURATION
from pyimouapi.exceptions import ImouException
from pyimouapi.ha_device import ImouHaDevice

from .const import (
    PARAM_PTZ,
    PARAM_RESTART_DEVICE,
    PARAM_ROTATION_DURATION,
    SERVICE_CONTROL_MOVE_PTZ,
    imou_life_device_key,
)
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .entity import ImouEntity

_LOGGER = logging.getLogger(__package__)

PARALLEL_UPDATES = 0


def _iter_buttons(
    coordinator: ImouDataUpdateCoordinator,
) -> list[tuple[str, ImouHaDevice]]:
    """Return (button_type, device) pairs for supported buttons."""
    return [
        (button_type, device)
        for device in coordinator.devices
        for button_type in device.buttons
    ]


async def async_setup_entry(
    hass: HomeAssistant, entry: ImouConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Imou button entities."""
    coordinator = entry.runtime_data.coordinator

    def _async_add_buttons(new_devices: list[ImouHaDevice]) -> None:
        device_keys = {imou_life_device_key(device) for device in new_devices}
        async_add_entities(
            ImouButton(coordinator, entry, button_type, device)
            for button_type, device in _iter_buttons(coordinator)
            if imou_life_device_key(device) in device_keys
        )

    coordinator.new_device_callbacks.append(_async_add_buttons)

    @callback
    def _remove_new_device_callback() -> None:
        if _async_add_buttons in coordinator.new_device_callbacks:
            coordinator.new_device_callbacks.remove(_async_add_buttons)

    entry.async_on_unload(_remove_new_device_callback)
    _async_add_buttons(coordinator.devices)

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_CONTROL_MOVE_PTZ,
        {
            vol.Required(PARAM_DURATION, default=500): vol.All(
                vol.Coerce(int), vol.Range(min=100, max=10000)
            ),
        },
        "async_handle_control_move_ptz",
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
        _LOGGER.debug("PTZ move for %ss on entity type %s", duration, self._entity_type)
        if PARAM_PTZ not in self._entity_type:
            raise HomeAssistantError(
                f"Invalid entity type {self._entity_type}; expected PTZ button"
            )
        await self._async_do_press(duration)

    async def _async_do_press(self, duration: int) -> None:
        """Send button command to the cloud API."""
        try:
            await self.coordinator.device_manager.async_press_button(
                self.device,
                self._entity_type,
                duration,
            )
        except ImouException as e:
            raise HomeAssistantError(e.message) from e
