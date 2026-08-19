"""Local event recording: per-camera switch, shared path/duration."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from custom_components.imou_life.const import (
    DOMAIN,
    PARAM_LOCAL_EVENT_RECORD,
    PARAM_LOCAL_RECORD_DURATION,
    PARAM_LOCAL_RECORD_PATH,
)
from custom_components.imou_life.local_record import async_maybe_record_from_alarm
from custom_components.imou_life.webhook import async_handle_imou_webhook
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import async_mock_service

from .conftest import setup_imou_runtime
from .test_webhook import MockRequest


def _register_camera_and_switch(
    hass: HomeAssistant, *, device_key: str, switch_on: bool
) -> None:
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "camera",
        DOMAIN,
        f"{device_key}$camera",
        suggested_object_id="front_live",
    )
    switch = registry.async_get_or_create(
        "switch",
        DOMAIN,
        f"{device_key}${PARAM_LOCAL_EVENT_RECORD}",
        suggested_object_id="front_local_event_record",
    )
    hass.states.async_set(switch.entity_id, STATE_ON if switch_on else STATE_OFF)
    hass.states.async_set("camera.front_live", "idle")


async def _alarm(
    hass: HomeAssistant, entry, *, channel_id: str = "0"
) -> list[ServiceCall]:
    """Fire one human-detect alarm and return camera.record calls."""
    calls = async_mock_service(hass, "camera", "record")
    await async_maybe_record_from_alarm(
        hass,
        entry,
        {"device_id": "SN1", "channel_id": channel_id, "msg_type": "human"},
    )
    await hass.async_block_till_done()
    return calls


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_alarm_does_not_record_when_switch_is_off(hass: HomeAssistant) -> None:
    """Alarms on a camera with the local-record switch off must not pull a stream."""
    setup_imou_runtime(hass, selected_devices=["SN1"])
    entry = next(iter(hass.config_entries.async_entries(DOMAIN)))
    hass.config_entries.async_update_entry(
        entry,
        options={
            PARAM_LOCAL_RECORD_PATH: "/media/imou",
            PARAM_LOCAL_RECORD_DURATION: 30,
        },
    )
    _register_camera_and_switch(hass, device_key="SN1_0", switch_on=False)

    with patch.object(hass.config, "is_allowed_path", return_value=True):
        calls = await _alarm(hass, entry)

    assert calls == []


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_alarm_does_not_record_when_folder_is_empty(hass: HomeAssistant) -> None:
    """A camera switch on with no shared folder must not pull a stream."""
    setup_imou_runtime(hass, selected_devices=["SN1"])
    entry = next(iter(hass.config_entries.async_entries(DOMAIN)))
    hass.config_entries.async_update_entry(
        entry,
        options={PARAM_LOCAL_RECORD_DURATION: 30},
    )
    _register_camera_and_switch(hass, device_key="SN1_0", switch_on=True)

    calls = await _alarm(hass, entry)

    assert calls == []


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_alarm_does_not_record_when_path_not_allowed(
    hass: HomeAssistant,
) -> None:
    """A folder outside allowlist_external_dirs must not start camera.record."""
    setup_imou_runtime(hass, selected_devices=["SN1"])
    entry = next(iter(hass.config_entries.async_entries(DOMAIN)))
    hass.config_entries.async_update_entry(
        entry,
        options={
            PARAM_LOCAL_RECORD_PATH: "/media/imou",
            PARAM_LOCAL_RECORD_DURATION: 30,
        },
    )
    _register_camera_and_switch(hass, device_key="SN1_0", switch_on=True)

    with patch.object(hass.config, "is_allowed_path", return_value=False):
        calls = await _alarm(hass, entry)

    assert calls == []


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_overlapping_record_is_skipped(hass: HomeAssistant) -> None:
    """A second alarm during an in-progress clip must not start another pull."""
    setup_imou_runtime(hass, selected_devices=["SN1"])
    entry = next(iter(hass.config_entries.async_entries(DOMAIN)))
    hass.config_entries.async_update_entry(
        entry,
        options={
            PARAM_LOCAL_RECORD_PATH: "/media/imou",
            PARAM_LOCAL_RECORD_DURATION: 30,
        },
    )
    _register_camera_and_switch(hass, device_key="SN1_0", switch_on=True)

    calls = async_mock_service(hass, "camera", "record")
    with patch.object(hass.config, "is_allowed_path", return_value=True):
        await async_maybe_record_from_alarm(
            hass,
            entry,
            {"device_id": "SN1", "channel_id": "0", "msg_type": "human"},
        )
        await async_maybe_record_from_alarm(
            hass,
            entry,
            {"device_id": "SN1", "channel_id": "0", "msg_type": "human"},
        )
        await hass.async_block_till_done()

    assert len(calls) == 1


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_alarm_records_only_the_armed_camera(hass: HomeAssistant) -> None:
    """Only the camera whose local-record switch is on is recorded."""
    setup_imou_runtime(hass, selected_devices=["SN1"])
    entry = next(iter(hass.config_entries.async_entries(DOMAIN)))
    hass.config_entries.async_update_entry(
        entry,
        options={
            PARAM_LOCAL_RECORD_PATH: "/media/imou",
            PARAM_LOCAL_RECORD_DURATION: 45,
        },
    )
    _register_camera_and_switch(hass, device_key="SN1_0", switch_on=True)

    calls = async_mock_service(hass, "camera", "record")
    with patch.object(hass.config, "is_allowed_path", return_value=True):
        await async_maybe_record_from_alarm(
            hass,
            entry,
            {"device_id": "SN1", "channel_id": "0", "msg_type": "human"},
        )
        await async_maybe_record_from_alarm(
            hass,
            entry,
            {"device_id": "SN1", "channel_id": "1", "msg_type": "human"},
        )
        await hass.async_block_till_done()

    assert len(calls) == 1
    data = calls[0].data
    assert data["entity_id"] == "camera.front_live"
    assert data["duration"] == 45
    assert data["lookback"] == 0
    assert data["filename"].startswith("/media/imou/")
    assert data["filename"].endswith(".mp4")


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_alarm_uses_local_record_helper(hass: HomeAssistant) -> None:
    """A security alarm from the webhook records when the camera switch is on."""
    setup_imou_runtime(hass, selected_devices=["SN1"])
    entry = next(iter(hass.config_entries.async_entries(DOMAIN)))
    hass.config_entries.async_update_entry(
        entry,
        options={
            PARAM_LOCAL_RECORD_PATH: "/media/imou",
            PARAM_LOCAL_RECORD_DURATION: 30,
        },
    )
    _register_camera_and_switch(hass, device_key="SN1_0", switch_on=True)
    calls = async_mock_service(hass, "camera", "record")

    with patch.object(hass.config, "is_allowed_path", return_value=True):
        response = await async_handle_imou_webhook(
            hass,
            "webhook-id",
            MockRequest(
                {
                    "msgType": "human",
                    "deviceId": "SN1",
                    "channelId": "0",
                }
            ),
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    assert response.status == 200
    assert len(calls) == 1
    assert calls[0].service == "record"
