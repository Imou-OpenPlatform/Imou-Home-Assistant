"""Tests for deviceSnapDetect service."""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from custom_components.imou_life.const import DOMAIN, SERVICE_DEVICE_SNAP_DETECT
from custom_components.imou_life.snap_detect import async_handle_device_snap_detect
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from PIL import Image
from pytest_homeassistant_custom_component.common import async_mock_service

from .conftest import setup_imou_runtime


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_device_snap_detect_notifies(hass: HomeAssistant) -> None:
    """Successful detect pushes a message to configured notify targets."""
    runtime = setup_imou_runtime(
        hass, notify_services=["notify.persistent_notification"]
    )
    entry = next(iter(hass.config_entries.async_entries(DOMAIN)))
    device = SimpleNamespace(device_id="SN1", channel_id="0")
    runtime.coordinator.devices_by_key = {"SN1_0": device}
    runtime.coordinator.device_manager.delegate.async_device_snap_detect = AsyncMock(
        return_value={
            "url": "https://cdn.example/shot.jpg",
            "detectResult": True,
            "targets": [
                {"targetType": "human", "confidence": "0.92"},
                {"targetType": "vehicle", "confidence": "0.81"},
            ],
        }
    )
    registry = er.async_get(hass)
    entity_entry = registry.async_get_or_create(
        DOMAIN,
        "camera",
        "SN1_0",
        config_entry=entry,
        suggested_object_id="imou_cam",
    )
    async_mock_service(hass, "notify", "persistent_notification")
    call = ServiceCall(
        hass,
        DOMAIN,
        SERVICE_DEVICE_SNAP_DETECT,
        {"entity_id": entity_entry.entity_id},
    )
    await async_handle_device_snap_detect(hass, call)
    runtime.coordinator.device_manager.delegate.async_device_snap_detect.assert_awaited_once_with(
        "SN1", "0"
    )


def _white_jpeg() -> bytes:
    image = Image.new("RGB", (100, 80), "white")
    buffer = BytesIO()
    image.save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_device_snap_detect_sends_the_picture_with_boxes(
    hass: HomeAssistant,
) -> None:
    """The notification picture is the snapshot with detection boxes drawn on."""
    runtime = setup_imou_runtime(
        hass, notify_services=["notify.persistent_notification"]
    )
    entry = next(iter(hass.config_entries.async_entries(DOMAIN)))
    device = SimpleNamespace(device_id="SN1", channel_id="0")
    runtime.coordinator.devices_by_key = {"SN1_0": device}
    runtime.coordinator.device_manager.delegate.async_device_snap_detect = AsyncMock(
        return_value={
            "url": "https://cdn.example/shot.jpg",
            "detectResult": True,
            "targets": [
                {
                    "targetType": "human",
                    "confidence": "0.92",
                    "targetRegion": {"x": 10, "y": 20, "width": 30, "height": 15},
                }
            ],
        }
    )
    registry = er.async_get(hass)
    entity_entry = registry.async_get_or_create(
        DOMAIN,
        "camera",
        "SN1_0",
        config_entry=entry,
        suggested_object_id="imou_cam_boxes",
    )
    calls = async_mock_service(hass, "notify", "persistent_notification")
    call = ServiceCall(
        hass,
        DOMAIN,
        SERVICE_DEVICE_SNAP_DETECT,
        {"entity_id": entity_entry.entity_id},
    )
    with patch(
        "custom_components.imou_life.snap_detect._async_download_snap",
        AsyncMock(return_value=_white_jpeg()),
    ):
        await async_handle_device_snap_detect(hass, call)
    message = calls[0].data["message"]
    assert "imou_life/snap/" in message
    assert "cdn.example" not in message


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_device_snap_detect_uses_the_device(hass: HomeAssistant) -> None:
    """A device target is enough; the service does not ask for an entity."""
    runtime = setup_imou_runtime(
        hass, notify_services=["notify.persistent_notification"]
    )
    entry = next(iter(hass.config_entries.async_entries(DOMAIN)))
    device = SimpleNamespace(device_id="SN1", channel_id="0")
    runtime.coordinator.devices_by_key = {"SN1_0": device}
    runtime.coordinator.device_manager.delegate.async_device_snap_detect = AsyncMock(
        return_value={"url": "https://cdn.example/shot.jpg", "detectResult": False}
    )
    device_entry = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "SN1_0")},
        name="移动镜头",
    )
    async_mock_service(hass, "notify", "persistent_notification")
    call = ServiceCall(
        hass,
        DOMAIN,
        SERVICE_DEVICE_SNAP_DETECT,
        {"device_id": device_entry.id},
    )
    await async_handle_device_snap_detect(hass, call)
    runtime.coordinator.device_manager.delegate.async_device_snap_detect.assert_awaited_once_with(
        "SN1", "0"
    )
