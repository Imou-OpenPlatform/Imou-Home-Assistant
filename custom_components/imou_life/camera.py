"""Imou camera entity."""

from __future__ import annotations

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
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
)
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .entity import ImouEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ImouConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Imou camera entities."""
    coordinator = entry.runtime_data
    entities: list[ImouCamera] = []
    for device in coordinator.devices:
        if device.channel_id is not None:
            entities.append(ImouCamera(coordinator, entry, "camera", device))
    if entities:
        async_add_entities(entities)


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
            return await self._coordinator.device_manager.async_get_device_stream(
                self._device,
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
            return await self._coordinator.device_manager.async_get_device_image(
                self._device,
                self._config_entry.options.get(PARAM_DOWNLOAD_SNAP_WAIT_TIME, 3),
            )
        except ImouException as e:
            raise HomeAssistantError(e.message) from e

    @property
    def is_recording(self) -> bool:
        """Return True when storage reports usage and motion detection is enabled."""
        return (
            self.is_non_negative_number(
                self._device.sensors[PARAM_STORAGE_USED][PARAM_STATE]
                if self._device.sensors.get(PARAM_STORAGE_USED)
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
        header = self._device.switches.get(PARAM_HEADER_DETECT)
        motion = self._device.switches.get(PARAM_MOTION_DETECT)
        header_on = bool(header[PARAM_STATE]) if header else False
        motion_on = bool(motion[PARAM_STATE]) if motion else False
        return header_on or motion_on

    @property
    def supported_features(self) -> CameraEntityFeature:
        """Flag streaming support."""
        return CameraEntityFeature.STREAM
