"""Imou camera entity."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util
from pyimouapi.const import PARAM_STATE
from pyimouapi.exceptions import ImouException
from pyimouapi.ha_device import ImouHaDevice

from .const import (
    CONF_HD,
    CONF_HTTPS,
    DOMAIN,
    PARAM_DOWNLOAD_SNAP_WAIT_TIME,
    PARAM_HEADER_DETECT,
    PARAM_LIVE_RESOLUTION,
    PARAM_MOTION_DETECT,
)
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .entity import ImouEntity, async_add_imou_entities
from .helpers import camera_channel_devices

PARALLEL_UPDATES = 0

# An established pull keeps the cloud ticket valid. About 10s after the last
# viewer, drop the cached Stream so the next open fetches a new URL.
STREAM_IDLE_CHECK = timedelta(seconds=10)


def _iter_cameras(
    coordinator: ImouDataUpdateCoordinator,
) -> list[tuple[str, ImouHaDevice]]:
    """Return (entity_type, device) pairs for camera entities."""
    return [
        ("camera", device) for device in camera_channel_devices(coordinator.devices)
    ]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Imou camera entities."""
    async_add_imou_entities(entry, async_add_entities, ImouCamera, _iter_cameras)


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
        self._idle_unsub: Callable[[], None] | None = None

    async def async_added_to_hass(self) -> None:
        """Cancel the idle check when the entity is removed."""
        await super().async_added_to_hass()
        self.async_on_remove(self._cancel_idle_check)

    async def stream_source(self) -> str | None:
        """Return a live stream URL and watch for the last viewer leaving."""
        url = await self._async_fetch_stream_url()
        self._schedule_idle_check()
        return url

    async def _async_fetch_stream_url(self) -> str:
        """Ask the cloud for a getStreamUrl ticket."""
        try:
            return await self.coordinator.device_manager.async_get_device_stream(
                self.device,
                self._config_entry.options.get(PARAM_LIVE_RESOLUTION, CONF_HD),
                CONF_HTTPS,
            )
        except ImouException as e:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="stream_source_failed",
                translation_placeholders={"error": e.message},
            ) from e

    def _schedule_idle_check(self) -> None:
        """Check later whether anyone is still pulling this stream."""
        self._cancel_idle_check()
        self._idle_unsub = async_track_point_in_utc_time(
            self.hass,
            self._async_handle_idle_check,
            dt_util.utcnow() + STREAM_IDLE_CHECK,
        )

    @callback
    def _cancel_idle_check(self) -> None:
        """Drop a pending idle check, if any."""
        if self._idle_unsub is not None:
            self._idle_unsub()
            self._idle_unsub = None

    async def _async_handle_idle_check(self, _now: datetime) -> None:
        """Keep the URL while someone is watching; drop it when they leave."""
        self._idle_unsub = None
        stream = self.stream
        if stream is not None and stream.outputs():
            self._schedule_idle_check()
            return
        if stream is not None:
            await stream.stop()
            self.stream = None

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
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="camera_image_failed",
                translation_placeholders={"error": e.message},
            ) from e

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
