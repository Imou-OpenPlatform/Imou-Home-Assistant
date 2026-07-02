"""Imou camera entity."""

from __future__ import annotations

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pyimouapi.const import PARAM_STATE
from pyimouapi.exceptions import ImouException
from pyimouapi.ha_device import ImouHaDevice

from .const import (
    PARAM_DOWNLOAD_SNAP_WAIT_TIME,
    PARAM_HEADER_DETECT,
    PARAM_LIVE_PROTOCOL,
    PARAM_LIVE_RESOLUTION,
    PARAM_MOTION_DETECT,
    PARAM_STORAGE_USED,
    imou_life_device_key,
)
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .entity import ImouEntity


def _iter_cameras(
    coordinator: ImouDataUpdateCoordinator,
) -> list[tuple[str, ImouHaDevice]]:
    """Return (entity_type, device) pairs for camera entities."""
    return [
        ("camera", device)
        for device in coordinator.devices
        if device.channel_id is not None
    ]


async def async_setup_entry(
    hass: HomeAssistant, entry: ImouConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Imou camera entities."""
    coordinator = entry.runtime_data.coordinator

    def _async_add_cameras(new_devices: list[ImouHaDevice]) -> None:
        device_keys = {imou_life_device_key(device) for device in new_devices}
        async_add_entities(
            ImouCamera(coordinator, entry, entity_type, device)
            for entity_type, device in _iter_cameras(coordinator)
            if imou_life_device_key(device) in device_keys
        )

    coordinator.new_device_callbacks.append(_async_add_cameras)

    @callback
    def _remove_new_device_callback() -> None:
        if _async_add_cameras in coordinator.new_device_callbacks:
            coordinator.new_device_callbacks.remove(_async_add_cameras)

    entry.async_on_unload(_remove_new_device_callback)
    _async_add_cameras(coordinator.devices)


class ImouCamera(ImouEntity, Camera):
    """Representation of an Imou camera stream."""

    def __init__(
        self,
        coordinator: ImouDataUpdateCoordinator,
        config_entry: ConfigEntry,
        entity_type: str,
        device: ImouHaDevice,
    ) -> None:
        """Initialize the camera entity."""
        Camera.__init__(self)
        ImouEntity.__init__(self, coordinator, config_entry, entity_type, device)

    async def stream_source(self) -> str | None:
        """Return the live stream URL from the Imou cloud."""
        try:
            return await self.coordinator.device_manager.async_get_device_stream(
                self.device,
                self._config_entry.options.get(PARAM_LIVE_RESOLUTION, "SD"),
                self._config_entry.options.get(PARAM_LIVE_PROTOCOL, "https"),
            )
        except ImouException as e:
            raise HomeAssistantError(e.message) from e

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return bytes of camera image."""
        try:
            return await self.coordinator.device_manager.async_get_device_image(
                self.device,
                self._config_entry.options.get(PARAM_DOWNLOAD_SNAP_WAIT_TIME, 3),
            )
        except ImouException as e:
            raise HomeAssistantError(e.message) from e

    @property
    def is_recording(self) -> bool:
        """Return True when storage reports usage and motion detection is enabled."""
        return (
            self.is_non_negative_number(
                self.device.sensors[PARAM_STORAGE_USED][PARAM_STATE]
                if self.device.sensors.get(PARAM_STORAGE_USED)
                else "-1"
            )
            and self.motion_detection_enabled
        )

    @property
    def is_streaming(self) -> bool:
        if self.stream is None:
            return False
        return self.stream._thread is not None and self.stream._thread.is_alive()

    @property
    def motion_detection_enabled(self) -> bool:
        """Return True when human and/or motion detection switch is on."""
        header = self.device.switches.get(PARAM_HEADER_DETECT)
        motion = self.device.switches.get(PARAM_MOTION_DETECT)
        header_on = bool(header[PARAM_STATE]) if header else False
        motion_on = bool(motion[PARAM_STATE]) if motion else False
        return header_on or motion_on

    @property
    def supported_features(self) -> CameraEntityFeature:
        """Flag streaming support."""
        return CameraEntityFeature.STREAM
