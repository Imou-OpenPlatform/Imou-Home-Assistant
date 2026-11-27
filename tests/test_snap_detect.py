"""Tests for deviceSnapDetect service."""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from custom_components.imou_life.const import (
    DOMAIN,
    EVENT_AI_INSIGHT_TRIAL,
    SERVICE_DEVICE_SNAP_DETECT,
)
from custom_components.imou_life.snap_detect import async_handle_device_snap_detect
from homeassistant.components import persistent_notification
from homeassistant.core import Event, HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from PIL import Image
from pyimouapi.const import ERROR_CODE_OPEN_API_QUOTA_EXCEEDED
from pyimouapi.exceptions import ImouException
from pytest_homeassistant_custom_component.common import async_mock_service

from .conftest import setup_imou_runtime


def _drawer_message(hass: HomeAssistant) -> str:
    notes = hass.data[persistent_notification.DOMAIN]
    note = next(iter(notes.values()))
    return note["message"]


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
                {"targetType": "human", "confidence": 92},
                {"targetType": "vehicle", "confidence": 81},
            ],
        }
    )
    registry = er.async_get(hass)
    entity_entry = registry.async_get_or_create(
        "camera",
        DOMAIN,
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
    message = _drawer_message(hass)
    assert "cdn.example/shot.jpg" in message
    assert "**Seen** 🚶 Person" in message
    assert "**Confidence**" in message
    assert "Sure (92)" in message
    assert "Vehicle" not in message
    assert "车辆" not in message


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_device_snap_detect_quota_opens_trial_dialog(
    hass: HomeAssistant,
) -> None:
    """OP1013 opens the shared trial dialog with both sales contacts."""
    runtime = setup_imou_runtime(hass)
    entry = next(iter(hass.config_entries.async_entries(DOMAIN)))
    runtime.coordinator.devices_by_key = {
        "SN1_0": SimpleNamespace(device_id="SN1", channel_id="0")
    }
    runtime.coordinator.device_manager.delegate.async_device_snap_detect = AsyncMock(
        side_effect=ImouException(
            "feature unavailable", api_code=ERROR_CODE_OPEN_API_QUOTA_EXCEEDED
        )
    )
    entity_entry = er.async_get(hass).async_get_or_create(
        "button",
        DOMAIN,
        "SN1_0$snap_detect",
        config_entry=entry,
        suggested_object_id="front_snap_detect",
    )
    events: list[Event[dict[str, str]]] = []
    hass.bus.async_listen(EVENT_AI_INSIGHT_TRIAL, events.append)
    call = ServiceCall(
        hass,
        DOMAIN,
        SERVICE_DEVICE_SNAP_DETECT,
        {"entity_id": entity_entry.entity_id},
    )

    await async_handle_device_snap_detect(hass, call)
    await hass.async_block_till_done()

    assert len(events) == 1
    assert "17799860785" in events[0].data["message"]
    assert "+86 19106884129" in events[0].data["message"]
    assert persistent_notification.DOMAIN not in hass.data


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
                    "confidence": 95,
                    "targetRegion": {"x": 10, "y": 20, "width": 30, "height": 15},
                }
            ],
        }
    )
    registry = er.async_get(hass)
    entity_entry = registry.async_get_or_create(
        "camera",
        DOMAIN,
        "SN1_0",
        config_entry=entry,
        suggested_object_id="imou_cam_boxes",
    )
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
    message = _drawer_message(hass)
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
    assert "cdn.example/shot.jpg" in _drawer_message(hass)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_device_snap_detect_posts_when_alarm_targets_are_empty(
    hass: HomeAssistant,
) -> None:
    """An empty alarm-notification list still shows the picture and the result."""
    runtime = setup_imou_runtime(hass, notify_services=[])
    entry = next(iter(hass.config_entries.async_entries(DOMAIN)))
    device = SimpleNamespace(device_id="SN1", channel_id="0")
    runtime.coordinator.devices_by_key = {"SN1_0": device}
    runtime.coordinator.device_manager.delegate.async_device_snap_detect = AsyncMock(
        return_value={
            "url": "http://cdn.example/shot.jpg",
            "detectResult": True,
            "targets": [{"targetType": "human", "confidence": 92}],
        }
    )
    registry = er.async_get(hass)
    entity_entry = registry.async_get_or_create(
        "camera",
        DOMAIN,
        "SN1_0",
        config_entry=entry,
        suggested_object_id="imou_cam_empty_notify",
    )
    call = ServiceCall(
        hass,
        DOMAIN,
        SERVICE_DEVICE_SNAP_DETECT,
        {"entity_id": entity_entry.entity_id},
    )
    await async_handle_device_snap_detect(hass, call)
    message = _drawer_message(hass)
    assert "http://cdn.example/shot.jpg" in message
    assert "Person" in message or "人" in message


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_device_snap_detect_ignores_results_below_85(
    hass: HomeAssistant,
) -> None:
    """A result below 85% is not named and is not drawn on the picture."""
    runtime = setup_imou_runtime(hass, notify_services=[])
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
                    "confidence": 84,
                    "targetRegion": {"x": 0.5, "y": 0.4, "width": 0.2, "height": 0.3},
                }
            ],
        }
    )
    registry = er.async_get(hass)
    entity_entry = registry.async_get_or_create(
        "camera",
        DOMAIN,
        "SN1_0",
        config_entry=entry,
        suggested_object_id="imou_cam_low_confidence",
    )
    call = ServiceCall(
        hass,
        DOMAIN,
        SERVICE_DEVICE_SNAP_DETECT,
        {"entity_id": entity_entry.entity_id},
    )
    with patch(
        "custom_components.imou_life.snap_detect._async_download_snap",
        AsyncMock(return_value=_white_jpeg()),
    ) as download:
        await async_handle_device_snap_detect(hass, call)
    download.assert_not_awaited()
    message = _drawer_message(hass)
    assert "cdn.example/shot.jpg" in message
    assert "imou_life/snap/" not in message
    assert "Person" not in message
    assert "人" not in message
    assert "No target detected." in message or "未检测到目标" in message
