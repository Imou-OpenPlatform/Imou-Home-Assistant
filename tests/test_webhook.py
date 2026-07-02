"""Tests for Imou Life webhook handling."""

from __future__ import annotations

from typing import Any

import pytest
from custom_components.imou_life.const import (
    DOMAIN,
    EVENT_IMOU_ALARM,
    EVENT_IMOU_EVENT,
)
from custom_components.imou_life.webhook import (
    _async_build_notification_message,
    async_handle_imou_webhook,
)
from homeassistant.core import Event, HomeAssistant


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
    hass.data[DOMAIN] = {
        "push_enabled": True,
        "selected_devices": ["selected_device"],
        "notify_services": [],
    }

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
    hass.data[DOMAIN] = {
        "push_enabled": True,
        "selected_devices": ["device_1"],
        "notify_services": [],
    }

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
