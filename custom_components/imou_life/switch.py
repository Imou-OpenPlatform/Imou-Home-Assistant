from typing import Any

import voluptuous as vol
from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pyimouapi.const import PARAM_STATE
from pyimouapi.exceptions import ImouException

from .const import PARAM_ENTITY_ID, SERVICE_TURN_OFF, SERVICE_TURN_ON
from .coordinator import ImouConfigEntry
from .entity import ImouEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ImouConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Imou switch entities."""
    coordinator = entry.runtime_data.coordinator
    entities: list[ImouSwitch] = []
    for device in coordinator.devices:
        for switch_type in device.switches:
            entities.append(ImouSwitch(coordinator, entry, switch_type, device))
    if entities:
        async_add_entities(entities)

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
            await self._coordinator.device_manager.async_switch_operation(
                self._device,
                self._entity_type,
                True,
            )
            self._device.switches[self._entity_type][PARAM_STATE] = True
            self.async_write_ha_state()
        except ImouException as e:
            raise HomeAssistantError(e.message) from e

    async def async_turn_off(self, **kwargs: Any) -> None:
        try:
            await self._coordinator.device_manager.async_switch_operation(
                self._device,
                self._entity_type,
                False,
            )
            self._device.switches[self._entity_type][PARAM_STATE] = False
            self.async_write_ha_state()
        except ImouException as e:
            raise HomeAssistantError(e.message) from e

    @property
    def is_on(self) -> bool | None:
        """Return True if the switch is on."""
        return self._device.switches[self._entity_type][PARAM_STATE]

    @property
    def device_class(self) -> SwitchDeviceClass | None:
        """Return device class when applicable."""
        if self._entity_type == "switch":
            return SwitchDeviceClass.SWITCH
        return None
