"""Last decrypted alarm still as a camera image entity."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from custom_components.imou_life.const import (
    DEFAULT_EVENT_PUSH_TYPES,
    DOMAIN,
    EVENT_IMOU_ALARM,
    EVENT_PUSH_TYPE_IOT,
    PARAM_ALARM_PICTURE,
    PARAM_ATTACH_DECRYPTED_THUMBNAIL,
    PARAM_ENABLE_EVENT_PUSH,
    PARAM_EVENT_PUSH_TYPES,
    PARAM_STATUS,
)
from custom_components.imou_life.image import ImouAlarmImage, _iter_alarm_images
from homeassistant.core import HomeAssistant
from pyimouapi.const import PARAM_STATE
from pyimouapi.ha_device import DeviceStatus, ImouHaDevice
from pytest_homeassistant_custom_component.common import MockConfigEntry

from . import USER_INPUT

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 10


def _device(*, channel_id: object | None, device_id: str = "SN1") -> MagicMock:
    device = MagicMock(spec=ImouHaDevice)
    device.device_id = device_id
    device.channel_id = channel_id
    device.product_id = None
    device.is_ipc = True
    device.device_ability = "WLAN"
    device.channel_ability = "WLAN"
    device.device_name = "Front"
    device.channel_name = "Front"
    device.manufacturer = "Imou"
    device.model = "IPC"
    device.swversion = "1.0"
    device.sensors = {}
    return device


def _coordinator(hass: HomeAssistant, devices: list[MagicMock]) -> MagicMock:
    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.devices = devices
    coordinator.devices_by_key = {}
    coordinator.last_update_success = True
    coordinator.device_manager.async_get_device_image = AsyncMock()
    return coordinator


def _online_alarm_image(
    hass: HomeAssistant,
    device: MagicMock,
    *,
    push: bool,
    attach: bool = True,
    types: list[str] | None = None,
) -> ImouAlarmImage:
    """Alarm-picture entity for an online camera with the given options."""
    coordinator = _coordinator(hass, [device])
    coordinator.devices_by_key = {f"{device.device_id}_{device.channel_id}": device}
    device.sensors = {PARAM_STATUS: {PARAM_STATE: DeviceStatus.ONLINE.value}}
    options: dict[str, object] = {
        PARAM_ENABLE_EVENT_PUSH: push,
        PARAM_ATTACH_DECRYPTED_THUMBNAIL: attach,
    }
    if types is not None:
        options[PARAM_EVENT_PUSH_TYPES] = types
    elif push:
        options[PARAM_EVENT_PUSH_TYPES] = list(DEFAULT_EVENT_PUSH_TYPES)
    return ImouAlarmImage(
        coordinator,
        MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options=options),
        PARAM_ALARM_PICTURE,
        device,
    )


@asynccontextmanager
async def _listening_image(
    hass: HomeAssistant, entity: ImouAlarmImage
) -> AsyncIterator[ImouAlarmImage]:
    """Subscribe the entity, then drop its bus listener."""
    entity.hass = hass
    entity.entity_id = "image.front_alarm_picture"
    await entity.async_added_to_hass()
    try:
        yield entity
    finally:
        if entity._on_remove is not None:
            while entity._on_remove:
                entity._on_remove.pop()()


def test_iter_alarm_images_cameras_only() -> None:
    """Every camera channel gets a still; plugs do not."""
    camera = _device(channel_id="0")
    lens = _device(channel_id="1", device_id="SN1")
    plug = _device(channel_id=None, device_id="PLUG1")
    coordinator = MagicMock()
    coordinator.devices = [camera, lens, plug]

    pairs = _iter_alarm_images(coordinator)

    assert pairs == [
        (PARAM_ALARM_PICTURE, camera),
        (PARAM_ALARM_PICTURE, lens),
    ]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_unavailable_when_alarm_push_is_off(hass: HomeAssistant) -> None:
    """No alarm push means this still cannot update."""
    device = _device(channel_id="0")
    assert _online_alarm_image(hass, device, push=False).available is False


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_unavailable_when_decrypt_is_off(hass: HomeAssistant) -> None:
    """Decrypt off means there is no still to show."""
    device = _device(channel_id="0")
    entity = _online_alarm_image(hass, device, push=True, attach=False)
    assert entity.available is False


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_unavailable_when_alarm_type_not_subscribed(
    hass: HomeAssistant,
) -> None:
    """iotProperty push does not carry alarm stills."""
    device = _device(channel_id="0")
    entity = _online_alarm_image(hass, device, push=True, types=[EVENT_PUSH_TYPE_IOT])
    assert entity.available is False


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_available_when_push_and_decrypt_are_on(hass: HomeAssistant) -> None:
    """An online camera with alarm push and decrypt can show a still."""
    device = _device(channel_id="0")
    assert _online_alarm_image(hass, device, push=True, attach=True).available is True


def _write_www_thumb(hass: HomeAssistant, name: str, jpeg: bytes) -> str:
    thumbs = Path(hass.config.path("www", "imou_life", "thumbs"))
    thumbs.mkdir(parents=True, exist_ok=True)
    (thumbs / name).write_bytes(jpeg)
    return f"/local/imou_life/thumbs/{name}"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_alarm_with_thumbnail_updates_image(hass: HomeAssistant) -> None:
    """A matching alarm with a decrypted still replaces the entity image."""
    device = _device(channel_id="0")
    entity = _online_alarm_image(hass, device, push=True)
    local_url = _write_www_thumb(hass, "alarm1.jpg", JPEG)
    async with _listening_image(hass, entity):
        hass.bus.async_fire(
            EVENT_IMOU_ALARM,
            {
                "device_id": "SN1",
                "channel_id": "0",
                "msg_type": "videoMotion",
                "thumbnail_path": local_url,
            },
        )
        await hass.async_block_till_done()
        assert entity.image() == JPEG
        assert entity.image_last_updated is not None
        last = Path(hass.config.path("imou_life", "last_alarm", "SN1_0.jpg"))
        assert await hass.async_add_executor_job(last.read_bytes) == JPEG
    entity.coordinator.device_manager.async_get_device_image.assert_not_called()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_other_camera_does_not_update(hass: HomeAssistant) -> None:
    """A still for another channel must not overwrite this lens."""
    device = _device(channel_id="0", device_id="SNOTHER")
    entity = _online_alarm_image(hass, device, push=True)
    local_url = _write_www_thumb(hass, "alarm2.jpg", JPEG)
    async with _listening_image(hass, entity):
        assert entity.image() is None
        hass.bus.async_fire(
            EVENT_IMOU_ALARM,
            {
                "device_id": "SNOTHER",
                "channel_id": "1",
                "msg_type": "videoMotion",
                "thumbnail_path": local_url,
            },
        )
        await hass.async_block_till_done()
        assert entity.image() is None


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_alarm_without_thumbnail_does_not_clear_image(
    hass: HomeAssistant,
) -> None:
    """Pushes with no picture keep the previous still."""
    device = _device(channel_id="0")
    entity = _online_alarm_image(hass, device, push=True)
    local_url = _write_www_thumb(hass, "alarm3.jpg", JPEG)
    async with _listening_image(hass, entity):
        hass.bus.async_fire(
            EVENT_IMOU_ALARM,
            {
                "device_id": "SN1",
                "channel_id": "0",
                "msg_type": "videoMotion",
                "thumbnail_path": local_url,
            },
        )
        await hass.async_block_till_done()
        hass.bus.async_fire(
            EVENT_IMOU_ALARM,
            {"device_id": "SN1", "channel_id": "0", "msg_type": "videoMotion"},
        )
        await hass.async_block_till_done()
        assert entity.image() == JPEG


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_loads_persisted_last_image_on_add(hass: HomeAssistant) -> None:
    """A still written before restart is shown again when the entity loads."""
    last = Path(hass.config.path("imou_life", "last_alarm", "SN1_0.jpg"))
    last.parent.mkdir(parents=True, exist_ok=True)
    await hass.async_add_executor_job(last.write_bytes, JPEG)
    device = _device(channel_id="0")
    entity = _online_alarm_image(hass, device, push=True)
    async with _listening_image(hass, entity):
        assert entity.image() == JPEG
        assert entity.image_last_updated is not None
