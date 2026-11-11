"""Tests for deviceSnapDetect service."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from custom_components.imou_life.const import DOMAIN, SERVICE_DEVICE_SNAP_DETECT
from custom_components.imou_life.snap_detect import async_handle_device_snap_detect
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import entity_registry as er
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

