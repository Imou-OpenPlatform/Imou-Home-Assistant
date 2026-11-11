"""Poll cloud / AI Insight subscription state for camera channels."""

from __future__ import annotations

import asyncio
import inspect
import logging

from pyimouapi.exceptions import ImouException
from pyimouapi.ha_device import ImouHaDevice
from pyimouapi.insight.cloud import DeviceCloudService, parse_device_cloud_data

from .helpers import camera_channel_devices

_LOGGER = logging.getLogger(__name__)


async def async_refresh_cloud_service_snapshots(
    delegate,
    devices: list[ImouHaDevice],
    store: dict[str, DeviceCloudService],
) -> None:
    """Refresh getDeviceCloud for each camera channel (best-effort)."""
    cameras = camera_channel_devices(devices)
    if not cameras:
        return

    async def _fetch(device: ImouHaDevice) -> None:
        channel = device.channel_id
        if channel is None:
            return
        from .const import imou_life_device_key

        key = imou_life_device_key(device)
        fetch = getattr(delegate, "async_get_device_cloud", None)
        if fetch is None or not inspect.iscoroutinefunction(fetch):
            return
        try:
            data = await fetch(device.device_id, str(channel))
            store[key] = parse_device_cloud_data(data)
        except ImouException:
            _LOGGER.debug(
                "Cloud service lookup failed for %s ch%s",
                device.device_id,
                channel,
                exc_info=True,
            )
            store.pop(key, None)

    await asyncio.gather(*(_fetch(device) for device in cameras))
