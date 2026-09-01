"""Last decrypted alarm still as a camera image entity."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.image import ImageEntity
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util
from pyimouapi.ha_device import ImouHaDevice

from .const import EVENT_IMOU_ALARM, PARAM_ALARM_PICTURE
from .coordinator import ImouConfigEntry, ImouDataUpdateCoordinator
from .entity import ImouEntity, async_add_imou_entities
from .helpers import camera_channel_devices, decrypt_pictures_active
from .pic_thumbnail import adopt_local_thumb, read_last_alarm_image

PARALLEL_UPDATES = 0


def _iter_alarm_images(
    coordinator: ImouDataUpdateCoordinator,
) -> list[tuple[str, ImouHaDevice]]:
    """One last-still image per camera channel, not plugs."""
    return [
        (PARAM_ALARM_PICTURE, device)
        for device in camera_channel_devices(coordinator.devices)
    ]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ImouConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Imou alarm picture entities."""
    async_add_imou_entities(
        entry, async_add_entities, ImouAlarmImage, _iter_alarm_images
    )


class ImouAlarmImage(ImouEntity, ImageEntity):
    """Last decrypted alarm still for a camera channel."""

    _attr_content_type = "image/jpeg"

    def __init__(
        self,
        coordinator: ImouDataUpdateCoordinator,
        config_entry: ImouConfigEntry,
        entity_type: str,
        device: ImouHaDevice,
    ) -> None:
        """Initialize the alarm picture entity."""
        ImouEntity.__init__(self, coordinator, config_entry, entity_type, device)
        ImageEntity.__init__(self, coordinator.hass)
        self._image_bytes: bytes | None = None

    @property
    def available(self) -> bool:
        """Unavailable when alarm push or local decrypt is off."""
        return super().available and decrypt_pictures_active(self._config_entry)

    def image(self) -> bytes | None:
        """Return the last decrypted jpeg, if any."""
        return self._image_bytes

    async def async_added_to_hass(self) -> None:
        """Load a persisted still and listen for new decrypted alarm pictures."""
        await super().async_added_to_hass()
        stored = await self.hass.async_add_executor_job(
            read_last_alarm_image, self.hass, self._device_key
        )
        if stored:
            jpeg, last_updated = stored
            self._apply_image(jpeg, last_updated=last_updated)
        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_IMOU_ALARM, self._async_handle_alarm)
        )

    def _apply_image(
        self, jpeg: bytes, *, last_updated: datetime | None = None
    ) -> None:
        """Replace the cached still."""
        self._image_bytes = jpeg
        self._cached_image = None
        self._attr_image_last_updated = last_updated or dt_util.utcnow()

    async def _async_handle_alarm(self, event: Event[dict[str, Any]]) -> None:
        """Load a newly decrypted still for this camera."""
        event_data = event.data
        if not self._event_matches_this_device(event_data):
            return
        thumbnail_path = event_data.get("thumbnail_path")
        if not isinstance(thumbnail_path, str):
            return
        jpeg = await self.hass.async_add_executor_job(
            adopt_local_thumb, self.hass, self._device_key, thumbnail_path
        )
        if not jpeg:
            return
        self._apply_image(jpeg)
        if self.platform is not None:
            self.async_write_ha_state()
