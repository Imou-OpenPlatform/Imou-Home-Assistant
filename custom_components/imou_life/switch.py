"""Imou switch entities."""

from __future__ import annotations

from typing import Any, override

from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from pyimouapi.const import PARAM_STATE
from pyimouapi.exceptions import ImouException
from pyimouapi.ha_device import ImouHaDevice

from .const import (
    DOMAIN,
    PARAM_AB_ALARM_SOUND,
    PARAM_AUDIO_ENCODE_CONTROL,
    PARAM_CLOSE_CAMERA,
    PARAM_FRAME_REVERSE,
    PARAM_HEADER_DETECT,
    PARAM_LIGHT,
    PARAM_MOTION_DETECT,
    PARAM_PET_DETECT,
    PARAM_PLAY_SOUND,
    PARAM_PLUG_SWITCH,
    PARAM_SMART_TRACK,
    PARAM_WHITE_LIGHT,
    PARAM_WIDE_DYNAMIC,
)
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .entity import ImouEntity, async_add_imou_entities

PARALLEL_UPDATES = 0

# Detection, recording and indicator toggles are device settings rather than
# controls, so they belong under the device's configuration section. Privacy
# mode, the white light and the plug relay stay primary: those are operated,
# not configured.
SWITCH_TYPES: tuple[SwitchEntityDescription, ...] = (
    SwitchEntityDescription(
        key=PARAM_AB_ALARM_SOUND,
        translation_key=PARAM_AB_ALARM_SOUND,
        entity_category=EntityCategory.CONFIG,
    ),
    SwitchEntityDescription(
        key=PARAM_AUDIO_ENCODE_CONTROL,
        translation_key=PARAM_AUDIO_ENCODE_CONTROL,
        entity_category=EntityCategory.CONFIG,
    ),
    SwitchEntityDescription(
        key=PARAM_CLOSE_CAMERA,
        translation_key=PARAM_CLOSE_CAMERA,
    ),
    SwitchEntityDescription(
        key=PARAM_FRAME_REVERSE,
        translation_key=PARAM_FRAME_REVERSE,
        entity_category=EntityCategory.CONFIG,
    ),
    SwitchEntityDescription(
        key=PARAM_HEADER_DETECT,
        translation_key=PARAM_HEADER_DETECT,
        entity_category=EntityCategory.CONFIG,
    ),
    SwitchEntityDescription(
        key=PARAM_LIGHT,
        translation_key=PARAM_LIGHT,
        device_class=SwitchDeviceClass.SWITCH,
        entity_category=EntityCategory.CONFIG,
    ),
    SwitchEntityDescription(
        key=PARAM_MOTION_DETECT,
        translation_key=PARAM_MOTION_DETECT,
        entity_category=EntityCategory.CONFIG,
    ),
    SwitchEntityDescription(
        key=PARAM_PET_DETECT,
        translation_key=PARAM_PET_DETECT,
        entity_category=EntityCategory.CONFIG,
    ),
    SwitchEntityDescription(
        key=PARAM_PLAY_SOUND,
        translation_key=PARAM_PLAY_SOUND,
        entity_category=EntityCategory.CONFIG,
    ),
    SwitchEntityDescription(
        key=PARAM_SMART_TRACK,
        translation_key=PARAM_SMART_TRACK,
        entity_category=EntityCategory.CONFIG,
    ),
    SwitchEntityDescription(
        key=PARAM_PLUG_SWITCH,
        translation_key=PARAM_PLUG_SWITCH,
        device_class=SwitchDeviceClass.SWITCH,
    ),
    SwitchEntityDescription(
        key=PARAM_WHITE_LIGHT,
        translation_key=PARAM_WHITE_LIGHT,
    ),
    SwitchEntityDescription(
        key=PARAM_WIDE_DYNAMIC,
        translation_key=PARAM_WIDE_DYNAMIC,
        entity_category=EntityCategory.CONFIG,
    ),
)


def _iter_switches(
    coordinator: ImouDataUpdateCoordinator,
) -> list[tuple[SwitchEntityDescription, ImouHaDevice]]:
    """Return (description, device) pairs for supported switches."""
    return [
        (description, device)
        for device in coordinator.devices
        for description in SWITCH_TYPES
        if description.key in device.switches
    ]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Imou switch entities."""
    async_add_imou_entities(entry, async_add_entities, ImouSwitch, _iter_switches)


class ImouSwitch(ImouEntity, SwitchEntity):
    """Representation of an Imou switch."""

    entity_description: SwitchEntityDescription

    def __init__(
        self,
        coordinator: ImouDataUpdateCoordinator,
        config_entry: ImouConfigEntry,
        description: SwitchEntityDescription,
        device: ImouHaDevice,
    ) -> None:
        """Initialize ImouSwitch."""
        super().__init__(coordinator, config_entry, description.key, device)
        self.entity_description = description

    @property
    @override
    def is_on(self) -> bool | None:
        """Return True if the switch is on."""
        return self.device.switches[self._entity_type][PARAM_STATE]

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        await self._async_switch_operation(True)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        await self._async_switch_operation(False)

    async def _async_switch_operation(self, enable: bool) -> None:
        """Call the vendor library to change switch state."""
        try:
            await self.coordinator.device_manager.async_switch_operation(
                self.device,
                self._entity_type,
                enable,
            )
        except ImouException as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="switch_operation_failed",
                translation_placeholders={"error": err.message},
            ) from err
        self.async_write_ha_state()
