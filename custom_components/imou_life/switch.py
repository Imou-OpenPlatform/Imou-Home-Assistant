from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pyimouapi.const import PARAM_STATE
from pyimouapi.exceptions import ImouException
from pyimouapi.ha_device import ImouHaDevice

from .const import (
    SWITCH_TYPES,
    imou_life_device_key,
)
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .entity import ImouEntity

PARALLEL_UPDATES = 0

SWITCH_DEVICE_CLASS: dict[str, SwitchDeviceClass] = {
    "light": SwitchDeviceClass.SWITCH,
    "switch": SwitchDeviceClass.SWITCH,
}


def _iter_switches(
    coordinator: ImouDataUpdateCoordinator,
) -> list[tuple[str, ImouHaDevice]]:
    """Return (switch_type, device) pairs for supported switches."""
    return [
        (switch_type, device)
        for device in coordinator.devices
        for switch_type in device.switches
        if switch_type in SWITCH_TYPES
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


class ImouSwitch(ImouEntity, SwitchEntity):
    """Representation of an Imou switch."""

    def __init__(
        self,
        coordinator: ImouDataUpdateCoordinator,
        config_entry: ImouConfigEntry,
        entity_type: str,
        device: ImouHaDevice,
    ) -> None:
        """Initialize ImouSwitch."""
        super().__init__(coordinator, config_entry, entity_type, device)
        self._attr_device_class = SWITCH_DEVICE_CLASS.get(entity_type)

    async def async_turn_on(self, **kwargs: Any) -> None:
        try:
            await self.coordinator.device_manager.async_switch_operation(
                self.device,
                self._entity_type,
                True,
            )
        except ImouException as e:
            raise HomeAssistantError(e.message) from e
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        try:
            await self.coordinator.device_manager.async_switch_operation(
                self.device,
                self._entity_type,
                False,
            )
        except ImouException as e:
            raise HomeAssistantError(e.message) from e
        await self.coordinator.async_request_refresh()

    @property
    def is_on(self) -> bool | None:
        """Return True if the switch is on."""
        return self.device.switches[self._entity_type][PARAM_STATE]
