"""Imou button entities."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pyimouapi.const import PARAM_DURATION, PARAM_SIREN_START, PARAM_SIREN_STOP
from pyimouapi.exceptions import ImouException
from pyimouapi.ha_device import ImouHaDevice

from .const import (
    DOMAIN,
    PARAM_PTZ,
    PARAM_RESTART_DEVICE,
    PARAM_ROTATION_DURATION,
    SERVICE_CONTROL_MOVE_PTZ,
)
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .entity import ImouEntity, async_add_imou_entities

_LOGGER = logging.getLogger(__package__)

PARALLEL_UPDATES = 0

_SIREN_BUTTONS = frozenset({PARAM_SIREN_START, PARAM_SIREN_STOP})


def _iter_buttons(
    coordinator: ImouDataUpdateCoordinator,
) -> list[tuple[str, ImouHaDevice]]:
    """Return (button_type, device) pairs for supported buttons."""
    return [
        (button_type, device)
        for device in coordinator.devices
        for button_type in device.buttons
        if button_type not in _SIREN_BUTTONS
    ]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Imou button entities."""
    async_add_imou_entities(entry, async_add_entities, ImouButton, _iter_buttons)

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

    @property
    def entity_category(self) -> EntityCategory | None:
        """Keep the reboot button out of the way of the PTZ controls."""
        if self._entity_type == PARAM_RESTART_DEVICE:
            return EntityCategory.CONFIG
        return None

    async def async_handle_control_move_ptz(self, duration: int) -> None:
        """Service: move PTZ for the given duration."""
        _LOGGER.debug("PTZ move for %ss on entity type %s", duration, self._entity_type)
        if PARAM_PTZ not in self._entity_type:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="not_a_ptz_button",
                translation_placeholders={"entity_id": self.entity_id},
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
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="button_press_failed",
                translation_placeholders={"error": e.message},
            ) from e
