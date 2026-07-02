from typing import Any

import voluptuous as vol
from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pyimouapi.const import PARAM_STATE
from pyimouapi.exceptions import ImouException
from pyimouapi.ha_device import ImouHaDevice

from .const import (
    PARAM_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    imou_life_device_key,
)
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .entity import ImouEntity


def _iter_switches(
    coordinator: ImouDataUpdateCoordinator,
) -> list[tuple[str, ImouHaDevice]]:
    """Return (switch_type, device) pairs for supported switches."""
    return [
        (switch_type, device)
        for device in coordinator.devices
        for switch_type in device.switches
    ]


async def async_setup_entry(
    hass: HomeAssistant, entry: ImouConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Imou switch entities."""
    coordinator = entry.runtime_data.coordinator

    def _async_add_switches(new_devices: list[ImouHaDevice]) -> None:
        device_keys = {imou_life_device_key(device) for device in new_devices}
        async_add_entities(
            ImouSwitch(coordinator, entry, switch_type, device)
            for switch_type, device in _iter_switches(coordinator)
            if imou_life_device_key(device) in device_keys
        )

    coordinator.new_device_callbacks.append(_async_add_switches)

    @callback
    def _remove_new_device_callback() -> None:
        if _async_add_switches in coordinator.new_device_callbacks:
            coordinator.new_device_callbacks.remove(_async_add_switches)

    entry.async_on_unload(_remove_new_device_callback)
    _async_add_switches(coordinator.devices)

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_TURN_ON,
        {
            vol.Required(PARAM_ENTITY_ID): cv.entity_id,
        },
        "async_turn_on",
    )
    platform.async_register_entity_service(
        SERVICE_TURN_OFF,
        {
            vol.Required(PARAM_ENTITY_ID): cv.entity_id,
        },
        "async_turn_off",
    )


class ImouSwitch(ImouEntity, SwitchEntity):
    """Representation of an Imou switch."""

    async def async_turn_on(self, **kwargs: Any) -> None:
        try:
            await self.coordinator.device_manager.async_switch_operation(
                self.device,
                self._entity_type,
                True,
            )
            self.device.switches[self._entity_type][PARAM_STATE] = True
            self.async_write_ha_state()
        except ImouException as e:
            raise HomeAssistantError(e.message) from e

    async def async_turn_off(self, **kwargs: Any) -> None:
        try:
            await self.coordinator.device_manager.async_switch_operation(
                self.device,
                self._entity_type,
                False,
            )
            self.device.switches[self._entity_type][PARAM_STATE] = False
            self.async_write_ha_state()
        except ImouException as e:
            raise HomeAssistantError(e.message) from e

    @property
    def is_on(self) -> bool | None:
        """Return True if the switch is on."""
        return self.device.switches[self._entity_type][PARAM_STATE]

    @property
    def device_class(self) -> SwitchDeviceClass | None:
        """Return device class when applicable."""
        if self._entity_type == "switch":
            return SwitchDeviceClass.SWITCH
        return None
