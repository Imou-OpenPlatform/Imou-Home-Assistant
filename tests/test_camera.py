"""Tests for dropping an idle cloud live stream so the next open gets a new URL."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from custom_components.imou_life.camera import STREAM_IDLE_CHECK, ImouCamera
from custom_components.imou_life.const import (
    DOMAIN,
    PARAM_HEADER_DETECT,
    PARAM_MOTION_DETECT,
    imou_life_device_key,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pyimouapi.const import PARAM_STATE
from pyimouapi.ha_device import ImouHaDevice
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from . import USER_INPUT

RTSP = "rtsp://rtspproxy.example.com:8554/abc?expire=1787888972&digest=x"


def _device() -> MagicMock:
    device = MagicMock(spec=ImouHaDevice)
    device.device_id = "dev1"
    device.channel_id = "0"
    device.product_id = "BF5W2WL4"
    device.device_name = "Front"
    device.channel_name = "Front"
    device.manufacturer = "Imou"
    device.model = "IPC"
    device.swversion = "1.0"
    device.sensors = {}
    device.switches = {}
    return device


def _camera(hass: HomeAssistant, device: MagicMock) -> ImouCamera:
    coordinator = MagicMock()
    coordinator.devices_by_key = {imou_life_device_key(device): device}
    coordinator.last_update_success = True
    coordinator.device_manager.async_get_device_stream = AsyncMock(return_value=RTSP)
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    camera = ImouCamera(coordinator, entry, "camera", device)
    camera.hass = hass
    camera.entity_id = "camera.front"
    return camera


def _cleanup_camera(camera: ImouCamera) -> None:
    """Drop the idle check the way unload does."""
    camera._cancel_idle_check()
    if camera._on_remove:
        camera._call_on_remove_callbacks()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_watching_does_not_replace_the_url(hass: HomeAssistant) -> None:
    """An active pull keeps the ticket valid; swapping it would cut the picture."""
    device = _device()
    camera = _camera(hass, device)
    fetch = camera.coordinator.device_manager.async_get_device_stream
    stream = MagicMock()
    stream.outputs.return_value = {"hls": object()}
    stream.update_source = MagicMock()
    stream.stop = AsyncMock()
    camera.stream = stream

    await camera.async_added_to_hass()
    try:
        assert await camera.stream_source() == RTSP
        async_fire_time_changed(
            hass, dt_util.utcnow() + STREAM_IDLE_CHECK + timedelta(seconds=2)
        )
        await hass.async_block_till_done()

        fetch.assert_awaited_once()
        stream.update_source.assert_not_called()
        stream.stop.assert_not_awaited()
        assert camera.stream is stream
    finally:
        _cleanup_camera(camera)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_idle_stream_is_dropped_so_the_next_open_fetches_again(
    hass: HomeAssistant,
) -> None:
    """After nobody is pulling, drop the Stream so the next open is a new ticket."""
    device = _device()
    camera = _camera(hass, device)
    stream = MagicMock()
    stream.outputs.return_value = {}
    stream.update_source = MagicMock()
    stream.stop = AsyncMock()
    camera.stream = stream

    await camera.async_added_to_hass()
    try:
        await camera.stream_source()
        async_fire_time_changed(
            hass, dt_util.utcnow() + STREAM_IDLE_CHECK + timedelta(seconds=2)
        )
        await hass.async_block_till_done()

        stream.stop.assert_awaited_once()
        stream.update_source.assert_not_called()
        assert camera.stream is None
    finally:
        _cleanup_camera(camera)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_idle_check_is_cancelled_when_the_entity_is_removed(
    hass: HomeAssistant,
) -> None:
    """Unloading the camera must not keep a timer around."""
    device = _device()
    camera = _camera(hass, device)
    await camera.async_added_to_hass()
    await camera.stream_source()
    _cleanup_camera(camera)
    camera.coordinator.device_manager.async_get_device_stream.reset_mock()

    async_fire_time_changed(
        hass, dt_util.utcnow() + STREAM_IDLE_CHECK + timedelta(seconds=2)
    )
    await hass.async_block_till_done()

    camera.coordinator.device_manager.async_get_device_stream.assert_not_awaited()


def _flip_switch(device: MagicMock, switch_type: str, enable: bool) -> None:
    """Mimic the library writing the new switch state locally."""
    device.switches[switch_type][PARAM_STATE] = enable


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_enable_motion_detection_turns_on_picture_change(
    hass: HomeAssistant,
) -> None:
    """The camera attribute is on when either detect switch is; picture change first."""
    device = _device()
    device.switches = {
        PARAM_MOTION_DETECT: {PARAM_STATE: False},
        PARAM_HEADER_DETECT: {PARAM_STATE: False},
    }
    camera = _camera(hass, device)
    camera.async_write_ha_state = MagicMock()
    camera.coordinator.device_manager.async_switch_operation = AsyncMock(
        side_effect=_flip_switch
    )

    await camera.async_enable_motion_detection()

    camera.coordinator.device_manager.async_switch_operation.assert_awaited_once_with(
        device, PARAM_MOTION_DETECT, True
    )
    assert camera.motion_detection_enabled is True


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_enable_motion_detection_uses_human_detect_when_that_is_all(
    hass: HomeAssistant,
) -> None:
    """A camera without picture-change still has a switch to write."""
    device = _device()
    device.switches = {PARAM_HEADER_DETECT: {PARAM_STATE: False}}
    camera = _camera(hass, device)
    camera.async_write_ha_state = MagicMock()
    camera.coordinator.device_manager.async_switch_operation = AsyncMock(
        side_effect=_flip_switch
    )

    await camera.async_enable_motion_detection()

    camera.coordinator.device_manager.async_switch_operation.assert_awaited_once_with(
        device, PARAM_HEADER_DETECT, True
    )
    assert camera.motion_detection_enabled is True


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_disable_motion_detection_clears_both_detect_switches(
    hass: HomeAssistant,
) -> None:
    """The attribute is on if either switch is, so both have to go off."""
    device = _device()
    device.switches = {
        PARAM_MOTION_DETECT: {PARAM_STATE: True},
        PARAM_HEADER_DETECT: {PARAM_STATE: True},
    }
    camera = _camera(hass, device)
    camera.async_write_ha_state = MagicMock()
    camera.coordinator.device_manager.async_switch_operation = AsyncMock(
        side_effect=_flip_switch
    )

    await camera.async_disable_motion_detection()

    assert camera.coordinator.device_manager.async_switch_operation.await_count == 2
    camera.coordinator.device_manager.async_switch_operation.assert_any_await(
        device, PARAM_MOTION_DETECT, False
    )
    camera.coordinator.device_manager.async_switch_operation.assert_any_await(
        device, PARAM_HEADER_DETECT, False
    )
    assert camera.motion_detection_enabled is False


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_motion_detection_raises_when_the_camera_has_no_detect_switch(
    hass: HomeAssistant,
) -> None:
    """Do not pretend the attribute can be written on a camera with no detect."""
    device = _device()
    camera = _camera(hass, device)
    camera.async_write_ha_state = MagicMock()
    camera.coordinator.device_manager.async_switch_operation = AsyncMock()

    with pytest.raises(HomeAssistantError):
        await camera.async_enable_motion_detection()
    camera.coordinator.device_manager.async_switch_operation.assert_not_awaited()
