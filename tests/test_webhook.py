"""Tests for Imou Life webhook handling."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from custom_components.imou_life.const import (
    EVENT_IMOU_ALARM,
    EVENT_IMOU_EVENT,
)
from custom_components.imou_life.runtime_data import ImouRuntimeData
from custom_components.imou_life.webhook import (
    _async_build_notification_message,
    _is_alarm_msg_type,
    async_handle_imou_webhook,
)
from homeassistant.core import Event, HomeAssistant

from .conftest import setup_imou_runtime


class MockRequest:
    """Minimal aiohttp request mock for webhook tests."""

    def __init__(self, payload: Any, *, raises: Exception | None = None) -> None:
        """Initialize the request mock."""
        self._payload = payload
        self._raises = raises

    async def json(self) -> Any:
        """Return the configured JSON payload."""
        if self._raises is not None:
            raise self._raises
        return self._payload


@pytest.fixture(autouse=True)
def webhook_language(hass: HomeAssistant) -> None:
    """Use English translations for webhook notification tests."""
    hass.config.language = "en"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_respects_matching_config_entry_only(
    hass: HomeAssistant,
) -> None:
    """Push handling uses the config entry that owns the webhook_id."""
    events: list[Event] = []
    hass.bus.async_listen(EVENT_IMOU_EVENT, events.append)

    setup_imou_runtime(
        hass,
        webhook_id="wh-a",
        app_id="app_a",
        selected_devices=["dev-a"],
    )
    setup_imou_runtime(
        hass,
        webhook_id="wh-b",
        app_id="app_b",
        selected_devices=["dev-b"],
    )

    response = await async_handle_imou_webhook(
        hass,
        "wh-a",
        MockRequest({"msgType": "alarmLocal", "deviceId": "dev-b"}),
    )
    await hass.async_block_till_done()
    assert response.status == 200
    assert events == []

    response = await async_handle_imou_webhook(
        hass,
        "wh-a",
        MockRequest({"msgType": "alarmLocal", "deviceId": "dev-a"}),
    )
    await hass.async_block_till_done()
    assert response.status == 200
    assert len(events) == 1


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_unknown_webhook_id_returns_ok(hass: HomeAssistant) -> None:
    """Unknown webhook_id is acknowledged without firing events."""
    events: list[Event] = []
    hass.bus.async_listen(EVENT_IMOU_EVENT, events.append)
    setup_imou_runtime(hass, webhook_id="known-id")

    response = await async_handle_imou_webhook(
        hass,
        "unknown-id",
        MockRequest({"msgType": "alarmLocal", "deviceId": "device_1"}),
    )
    await hass.async_block_till_done()

    assert response.status == 200
    assert events == []


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_ignores_invalid_json(hass: HomeAssistant) -> None:
    """Invalid JSON is acknowledged so Imou keeps pushing later events."""
    events: list[Event] = []
    hass.bus.async_listen(EVENT_IMOU_EVENT, events.append)

    response = await async_handle_imou_webhook(
        hass, "webhook-id", MockRequest(None, raises=ValueError("bad json"))
    )
    await hass.async_block_till_done()

    assert response.status == 200
    assert events == []


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_filters_unselected_device(hass: HomeAssistant) -> None:
    """Events from unselected devices are ignored."""
    events: list[Event] = []
    hass.bus.async_listen(EVENT_IMOU_EVENT, events.append)
    setup_imou_runtime(
        hass,
        push_enabled=True,
        selected_devices=["selected_device"],
    )

    response = await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest({"msgType": "alarmLocal", "deviceId": "other_device"}),
    )
    await hass.async_block_till_done()

    assert response.status == 200
    assert events == []


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_iot_property_is_not_alarm(hass: HomeAssistant) -> None:
    """iotProperty push should fire generic event but not alarm event."""
    generic_events: list[Event] = []
    alarm_events: list[Event] = []
    hass.bus.async_listen(EVENT_IMOU_EVENT, generic_events.append)
    hass.bus.async_listen(EVENT_IMOU_ALARM, alarm_events.append)
    setup_imou_runtime(
        hass,
        push_enabled=True,
        selected_devices=["device_1"],
    )

    response = await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest({"msgType": "iotProperty", "deviceId": "device_1"}),
    )
    await hass.async_block_till_done()

    assert response.status == 200
    assert len(generic_events) == 1
    assert alarm_events == []


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_notification_uses_translations(hass: HomeAssistant) -> None:
    """Alarm notifications use webhook translation strings."""
    title, message = await _async_build_notification_message(
        hass,
        {"msg_type": "alarmLocal", "name": "Front Door"},
    )

    assert title == "Imou alarm: Local alarm"
    assert message == "Device: Front Door\nType: Local alarm"


@pytest.mark.parametrize(
    ("msg_type", "expected"),
    [
        ("closeCamera", False),
        ("openCamera", False),
        ("online", False),
        ("iotProperty", False),
        ("whiteLightOn", False),
        ("bindDevice", False),
        ("videoMotion", True),
        ("human", True),
        ("abAlarmSound", True),
        ("mobileDetect", True),
        ("alarmLocal", True),
        ("totallyUnknownType", True),
        (None, False),
    ],
)
def test_is_alarm_msg_type(msg_type: str | None, expected: bool) -> None:
    """Hybrid classification: denylist non-alarms; unknown types are alarms."""
    assert _is_alarm_msg_type(msg_type) is expected


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("msg_type", ["closeCamera", "openCamera"])
async def test_webhook_privacy_mask_is_not_alarm(
    hass: HomeAssistant, msg_type: str
) -> None:
    """Privacy mask open/close fires generic event only."""
    generic_events: list[Event] = []
    alarm_events: list[Event] = []
    hass.bus.async_listen(EVENT_IMOU_EVENT, generic_events.append)
    hass.bus.async_listen(EVENT_IMOU_ALARM, alarm_events.append)
    setup_imou_runtime(
        hass,
        push_enabled=True,
        selected_devices=["device_1"],
        notify_services=["notify.test"],
    )

    response = await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest(
            {
                "msgType": msg_type,
                "did": "device_1",
                "cid": 0,
                "dname": "Cam",
                "time": 1783528060,
            }
        ),
    )
    await hass.async_block_till_done()

    assert response.status == 200
    assert len(generic_events) == 1
    assert generic_events[0].data["msg_type"] == msg_type
    assert alarm_events == []


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("msg_type", ["videoMotion", "human", "abAlarmSound"])
async def test_webhook_security_alarms_fire_alarm_event(
    hass: HomeAssistant, msg_type: str
) -> None:
    """Security alarm msgTypes fire both generic and alarm events."""
    generic_events: list[Event] = []
    alarm_events: list[Event] = []
    hass.bus.async_listen(EVENT_IMOU_EVENT, generic_events.append)
    hass.bus.async_listen(EVENT_IMOU_ALARM, alarm_events.append)
    setup_imou_runtime(hass, push_enabled=True, selected_devices=["device_1"])

    response = await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest({"msgType": msg_type, "deviceId": "device_1"}),
    )
    await hass.async_block_till_done()

    assert response.status == 200
    assert len(generic_events) == 1
    assert len(alarm_events) == 1
    assert alarm_events[0].data["msg_type"] == msg_type


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_missing_msg_type_is_not_alarm(
    hass: HomeAssistant,
) -> None:
    """Payload without msgType still fires generic event, not alarm."""
    generic_events: list[Event] = []
    alarm_events: list[Event] = []
    hass.bus.async_listen(EVENT_IMOU_EVENT, generic_events.append)
    hass.bus.async_listen(EVENT_IMOU_ALARM, alarm_events.append)
    setup_imou_runtime(hass, push_enabled=True, selected_devices=["device_1"])

    response = await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest({"deviceId": "device_1"}),
    )
    await hass.async_block_till_done()

    assert response.status == 200
    assert len(generic_events) == 1
    assert alarm_events == []


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_records_msg_type_counts(hass: HomeAssistant) -> None:
    """Accepted pushes increment runtime msgType counters."""
    runtime = setup_imou_runtime(
        hass, push_enabled=True, selected_devices=["device_1"]
    )

    await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest({"msgType": "closeCamera", "deviceId": "device_1"}),
    )
    await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest({"msgType": "closeCamera", "deviceId": "device_1"}),
    )
    await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest({"msgType": "abAlarmSound", "deviceId": "device_1"}),
    )
    await hass.async_block_till_done()

    assert runtime.push_msg_type_counts["closeCamera"] == 2
    assert runtime.push_msg_type_counts["abAlarmSound"] == 1
    assert runtime.push_last_msg_type == "abAlarmSound"
    assert runtime.push_last_received_at is not None


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_filtered_device_does_not_count(
    hass: HomeAssistant,
) -> None:
    """Unselected devices do not update push counters."""
    runtime = setup_imou_runtime(
        hass,
        push_enabled=True,
        selected_devices=["selected_device"],
    )

    await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest({"msgType": "alarmLocal", "deviceId": "other_device"}),
    )
    await hass.async_block_till_done()

    assert runtime.push_msg_type_counts == {}
    assert runtime.push_last_msg_type is None


def test_record_push_msg_caps_distinct_keys() -> None:
    """More than 50 distinct msgTypes fold into _other."""
    runtime = ImouRuntimeData(coordinator=MagicMock())
    for i in range(50):
        runtime.record_push_msg(f"type_{i}")
    assert len(runtime.push_msg_type_counts) == 50
    runtime.record_push_msg("type_extra")
    assert runtime.push_msg_type_counts["_other"] == 1
    assert "type_extra" not in runtime.push_msg_type_counts
    assert runtime.push_last_msg_type == "type_extra"
