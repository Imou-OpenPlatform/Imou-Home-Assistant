"""Imou switch entities."""

from __future__ import annotations

from typing import Any, override

from homeassistant.components.switch import (
    SwitchDeviceClass,
    SwitchEntity,
    SwitchEntityDescription,
)
from homeassistant.const import STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from pyimouapi.const import PARAM_STATE
from pyimouapi.exceptions import ImouException
from pyimouapi.ha_device import ImouHaDevice

from .const import (
    PARAM_AB_ALARM_SOUND,
    PARAM_AUDIO_ENCODE_CONTROL,
    PARAM_CLOSE_CAMERA,
    PARAM_FRAME_REVERSE,
    PARAM_HEADER_DETECT,
    PARAM_LIGHT,
    PARAM_LINKAGE_SIREN,
    PARAM_LINKAGE_WHITE_LIGHT,
    PARAM_LOCAL_EVENT_RECORD,
    PARAM_MOTION_DETECT,
    PARAM_NOTIFY_ON_ALARM,
    PARAM_PET_DETECT,
    PARAM_PLAY_SOUND,
    PARAM_PLUG_SWITCH,
    PARAM_SMART_TRACK,
    PARAM_WIDE_DYNAMIC,
)
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .entity import ImouEntity, async_add_imou_entities
from .helpers import camera_channel_devices

PARALLEL_UPDATES = 0

# Detection, recording and indicator toggles are device settings rather than
# controls, so they belong under the device's configuration section. Privacy
# mode and the plug relay stay primary: those are operated, not configured.
# The camera white light is a light entity.
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
        key=PARAM_LINKAGE_SIREN,
        translation_key=PARAM_LINKAGE_SIREN,
        entity_category=EntityCategory.CONFIG,
    ),
    SwitchEntityDescription(
        key=PARAM_LINKAGE_WHITE_LIGHT,
        translation_key=PARAM_LINKAGE_WHITE_LIGHT,
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
    async_add_imou_entities(
        entry, async_add_entities, ImouLocalRecordSwitch, _iter_local_record_switches
    )
    async_add_imou_entities(
        entry,
        async_add_entities,
        ImouNotifyOnAlarmSwitch,
        _iter_notify_on_alarm_switches,
    )


def _iter_local_record_switches(
    coordinator: ImouDataUpdateCoordinator,
) -> list[tuple[str, ImouHaDevice]]:
    """One local-only record switch per camera channel."""
    return [
        (PARAM_LOCAL_EVENT_RECORD, device)
        for device in camera_channel_devices(coordinator.devices)
    ]


def _iter_notify_on_alarm_switches(
    coordinator: ImouDataUpdateCoordinator,
) -> list[tuple[str, ImouHaDevice]]:
    """One HA-only notify switch per Imou device."""
    return [(PARAM_NOTIFY_ON_ALARM, device) for device in coordinator.devices]


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
            self._raise_imou_ha_error(err, "switch_operation_failed")
        self.async_write_ha_state()


class ImouLocalRecordSwitch(ImouEntity, SwitchEntity, RestoreEntity):
    """HA-only switch: save a short clip when this camera raises an alarm."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_is_on = False

    def __init__(
        self,
        coordinator: ImouDataUpdateCoordinator,
        config_entry: ImouConfigEntry,
        entity_type: str,
        device: ImouHaDevice,
    ) -> None:
        """Initialize the local recording switch."""
        super().__init__(coordinator, config_entry, entity_type, device)

    @property
    @override
    def available(self) -> bool:
        """Keep the setting usable when the camera is offline."""
        if not super(ImouEntity, self).available:
            return False
        return self._device_key in self.coordinator.devices_by_key

    async def async_added_to_hass(self) -> None:
        """Restore the last on/off state after a restart."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._attr_is_on = last_state.state == STATE_ON

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable local recording for this camera."""
        self._attr_is_on = True
        self.async_write_ha_state()

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable local recording for this camera."""
        self._attr_is_on = False
        self.async_write_ha_state()


class ImouNotifyOnAlarmSwitch(ImouEntity, SwitchEntity, RestoreEntity):
    """HA-only switch: send account notify targets when this device alarms."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_is_on = True

    def __init__(
        self,
        coordinator: ImouDataUpdateCoordinator,
        config_entry: ImouConfigEntry,
        entity_type: str,
        device: ImouHaDevice,
    ) -> None:
        """Initialize the notify-on-alarm switch."""
        super().__init__(coordinator, config_entry, entity_type, device)

    @property
    @override
    def available(self) -> bool:
        """Keep the setting usable when the device is offline."""
        if not super(ImouEntity, self).available:
            return False
        return self._device_key in self.coordinator.devices_by_key

    async def async_added_to_hass(self) -> None:
        """Restore the last on/off state after a restart."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None:
            self._attr_is_on = last_state.state == STATE_ON

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable alarm notifications for this device."""
        self._attr_is_on = True
        self.async_write_ha_state()

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable alarm notifications for this device."""
        self._attr_is_on = False
        self.async_write_ha_state()
