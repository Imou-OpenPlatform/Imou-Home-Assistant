"""Tests for Imou Life webhook handling."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.imou_life.const import (
    DOMAIN,
    EVENT_IMOU_ALARM,
    EVENT_IMOU_EVENT,
    PARAM_ATTACH_DECRYPTED_THUMBNAIL,
    PARAM_NOTIFY_ON_ALARM,
)
from custom_components.imou_life.runtime_data import ImouRuntimeData, get_runtime_data
from custom_components.imou_life.webhook import (
    _async_build_notification_message,
    _format_notification_time,
    _redacted_push_for_log,
    async_handle_imou_webhook,
)
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import label_registry as lr
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_get_persistent_notifications,
    async_mock_service,
)

from .conftest import register_imou_ha_device, setup_imou_runtime


def test_redacted_push_log_keeps_raw_body_without_token() -> None:
    """Debug logs must show the original push body, minus the push token."""
    event_data = {
        "msg_type": "iotEvent",
        "token": "secret-token",
        "raw": {
            "msgType": "iotEvent",
            "token": "secret-token",
            "did": "SN1",
            "content": {"event": "33000", "monitor": {"channel": 0}},
        },
    }

    safe = _redacted_push_for_log(event_data)

    assert safe["token"] == "***"
    assert safe["raw"]["msgType"] == "iotEvent"
    assert safe["raw"]["did"] == "SN1"
    assert safe["raw"]["content"]["event"] == "33000"
    assert safe["raw"]["token"] == "***"
    assert event_data["raw"]["token"] == "secret-token"


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
    entry_a = next(
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.data["app_id"] == "app_a"
    )
    entry_b = next(
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.data["app_id"] == "app_b"
    )
    register_imou_ha_device(hass, entry_a, "dev-a")
    register_imou_ha_device(hass, entry_b, "dev-b")

    response = await async_handle_imou_webhook(
        hass,
        "wh-a",
        MockRequest({"msgType": "alarmLocal", "deviceId": "dev-b"}),
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert response.status == 200
    assert events == []

    response = await async_handle_imou_webhook(
        hass,
        "wh-a",
        MockRequest({"msgType": "alarmLocal", "deviceId": "dev-a"}),
    )
    await hass.async_block_till_done(wait_background_tasks=True)
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
    await hass.async_block_till_done(wait_background_tasks=True)

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
    await hass.async_block_till_done(wait_background_tasks=True)

    assert response.status == 200
    assert events == []


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_respects_entry_options_over_runtime_push_flag(
    hass: HomeAssistant,
) -> None:
    """Options are authoritative while reload is still catching runtime up."""
    events: list[Event] = []
    hass.bus.async_listen(EVENT_IMOU_EVENT, events.append)
    runtime = setup_imou_runtime(
        hass,
        push_enabled=False,
        selected_devices=["device_1"],
        options={"enable_event_push": True},
    )
    assert runtime.push_enabled is False

    response = await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest({"msgType": "alarmLocal", "deviceId": "device_1"}),
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert response.status == 200
    assert len(events) == 1


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_respects_entry_options_over_runtime_selected_devices(
    hass: HomeAssistant,
) -> None:
    """A just-saved device selection must apply before reload finishes."""
    events: list[Event] = []
    hass.bus.async_listen(EVENT_IMOU_EVENT, events.append)
    runtime = setup_imou_runtime(
        hass,
        push_enabled=True,
        selected_devices=["device_1", "device_2"],
        options={"selected_devices": ["device_2"]},
    )
    assert runtime.selected_devices == ["device_1", "device_2"]

    response = await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest({"msgType": "alarmLocal", "deviceId": "device_1"}),
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert response.status == 200
    assert events == []

    response = await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest({"msgType": "alarmLocal", "deviceId": "device_2"}),
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert response.status == 200
    assert len(events) == 1


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_ignores_push_without_ha_device(hass: HomeAssistant) -> None:
    """Pushes with no matching device registry row are ACKed but not dispatched."""
    events: list[Event] = []
    alarm_events: list[Event] = []
    hass.bus.async_listen(EVENT_IMOU_EVENT, events.append)
    hass.bus.async_listen(EVENT_IMOU_ALARM, alarm_events.append)
    notify_calls = async_mock_service(hass, "notify", "persistent_notification")
    setup_imou_runtime(
        hass,
        push_enabled=True,
        selected_devices=["ghost"],
        notify_services=["notify.persistent_notification"],
        register_ha_devices=False,
    )

    response = await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest({"msgType": "alarmLocal", "deviceId": "ghost"}),
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert response.status == 200
    assert events == []
    assert alarm_events == []
    assert notify_calls == []


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
    await hass.async_block_till_done(wait_background_tasks=True)

    assert response.status == 200
    assert events == []


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_empty_selection_rejects_all(hass: HomeAssistant) -> None:
    """Explicit empty selected_devices must not fall back to accepting all."""
    events: list[Event] = []
    hass.bus.async_listen(EVENT_IMOU_EVENT, events.append)
    setup_imou_runtime(hass, push_enabled=True, selected_devices=[])

    response = await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest({"msgType": "alarmLocal", "deviceId": "device_1"}),
    )
    await hass.async_block_till_done(wait_background_tasks=True)

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
    await hass.async_block_till_done(wait_background_tasks=True)

    assert response.status == 200
    assert len(generic_events) == 1
    assert alarm_events == []


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_iot_property_with_pid_is_not_alarm(
    hass: HomeAssistant,
) -> None:
    """IoT iotProperty is accepted, fires the generic event, never alarm."""
    generic_events: list[Event] = []
    alarm_events: list[Event] = []
    hass.bus.async_listen(EVENT_IMOU_EVENT, generic_events.append)
    hass.bus.async_listen(EVENT_IMOU_ALARM, alarm_events.append)
    setup_imou_runtime(
        hass,
        push_enabled=True,
        selected_devices=["device_1"],
    )
    register_imou_ha_device(
        hass,
        hass.config_entries.async_entries(DOMAIN)[0],
        "device_1",
        product_id="pid1",
        channel_id="0",
    )

    response = await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest(
            {
                "msgType": "iotProperty",
                "pid": "pid1",
                "did": "device_1",
                "cid": 0,
                "content": {"properties": {"10001": 1}},
            }
        ),
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert response.status == 200
    assert len(generic_events) == 1
    assert alarm_events == []
    assert generic_events[0].data["msg_type"] == "iotProperty"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_iot_property_applies_switch_state(
    hass: HomeAssistant,
) -> None:
    """Matching refs update the coordinator device before the bus event."""
    from pyimouapi.const import PARAM_REF, PARAM_STATE
    from pyimouapi.ha_device import ImouHaDevice, ImouHaDeviceManager

    events: list[Event] = []
    hass.bus.async_listen(EVENT_IMOU_EVENT, events.append)
    runtime = setup_imou_runtime(hass, push_enabled=True, selected_devices=["dev1"])
    device = ImouHaDevice("dev1", "Plug", "Imou", "Plug", "1.0")
    device.set_product_id("pid1")
    device.set_channel_id("0")
    device.switches["relay"] = {PARAM_REF: "10001", PARAM_STATE: False}
    runtime.coordinator.devices = [device]
    runtime.coordinator.device_manager = ImouHaDeviceManager(MagicMock())

    response = await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest(
            {
                "msgType": "iotProperty",
                "pid": "pid1",
                "did": "dev1",
                "cid": 0,
                "content": {"properties": {"10001": 1}},
            }
        ),
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert response.status == 200
    assert device.switches["relay"][PARAM_STATE] is True
    runtime.coordinator.async_update_listeners.assert_called_once()
    assert events[0].data["msg_type"] == "iotProperty"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_iot_property_applies_to_accessory_not_parent(
    hass: HomeAssistant,
) -> None:
    """Pid push with cid=0 updates the accessory, not the parent camera."""
    from custom_components.imou_life.const import imou_life_device_key
    from pyimouapi.const import PARAM_REF, PARAM_STATE
    from pyimouapi.ha_device import ImouHaDevice, ImouHaDeviceManager

    events: list[Event] = []
    hass.bus.async_listen(EVENT_IMOU_EVENT, events.append)
    runtime = setup_imou_runtime(hass, push_enabled=True, selected_devices=["dev1"])
    parent = ImouHaDevice("dev1", "Camera", "Imou", "IPC", "1.0")
    parent.set_channel_id("0")
    parent.switches["motion"] = {PARAM_REF: "10001", PARAM_STATE: False}

    accessory = ImouHaDevice("dev1", "Plug", "Imou", "Plug", "1.0")
    accessory.set_product_id("pid1")
    accessory.switches["relay"] = {PARAM_REF: "10001", PARAM_STATE: False}

    runtime.coordinator.devices_by_key = {
        imou_life_device_key(parent): parent,
        imou_life_device_key(accessory): accessory,
    }
    runtime.coordinator.device_manager = ImouHaDeviceManager(MagicMock())

    response = await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest(
            {
                "msgType": "iotProperty",
                "pid": "pid1",
                "did": "dev1",
                "cid": 0,
                "content": {"properties": {"10001": 1}},
            }
        ),
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert response.status == 200
    assert accessory.switches["relay"][PARAM_STATE] is True
    assert parent.switches["motion"][PARAM_STATE] is False
    runtime.coordinator.async_update_listeners.assert_called_once()
    assert events[0].data["msg_type"] == "iotProperty"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_notification_uses_translations(hass: HomeAssistant) -> None:
    """Alarm notifications use webhook translation strings."""
    title, message = await _async_build_notification_message(
        hass,
        {"msg_type": "alarmLocal", "device_name": "Front Door"},
    )

    assert title == "Imou Life · Local alarm"
    assert message == "Device: Front Door"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_iot_and_paas_motion_are_scene_change(
    hass: HomeAssistant,
) -> None:
    """IoT e_videoMotion and PaaS videoMotion share the same motion copy."""
    iot_title, _ = await _async_build_notification_message(
        hass, {"msg_type": "e_videoMotion", "device_name": "Cam"}
    )
    paas_title, _ = await _async_build_notification_message(
        hass, {"msg_type": "videoMotion", "device_name": "Cam"}
    )
    assert iot_title == paas_title == "Imou Life · Motion detected"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_e_ab_alarm_sound_uses_localized_copy(
    hass: HomeAssistant,
) -> None:
    """IoT e_abAlarmSound maps to the same copy as PaaS abAlarmSound."""
    await hass.config.async_set_time_zone("UTC")
    title, message = await _async_build_notification_message(
        hass,
        {
            "msg_type": "e_abAlarmSound",
            "device_name": "Hall",
            "time": "2026-08-17T14:30:05",
        },
    )
    assert title == "Imou Life · Unusual sound detected"
    assert "Type:" not in message
    assert "Time: 2026-08-17 14:30:05" in message
    assert "Device: Hall" in message


@pytest.mark.parametrize(
    ("msg_type", "expected_fragment"),
    [
        ("e_multiVideoAiPerArea", "Person entered the area"),
        ("e_multiVideoAiPetAlarm", "Pet entered the area"),
        ("e_multiVideoAiVehAreaAlarm", "Vehicle entered the area"),
        ("e_multiVideoAreaDetect", "Intrusion detected"),
        ("e_multiOrdinaryEvent", "Ordinary recording event"),
        ("e_std_aorAlarm", "AOR recording event"),
        ("e_hxSmokeAlarm", "Smoke detected"),
        ("e_clearSmokeAlarm", "Smoke alarm cleared"),
        ("e_gasAlarm", "Gas leak detected"),
        ("e_clearGasAlarm", "Gas alarm cleared"),
        ("e_faultAlarm", "Device fault"),
        ("e_sensorAbnormal", "Tamper detected"),
        ("smoke_alarm", "Smoke detected"),
        ("fault_remind", "Fault warning"),
        ("smoke_alarm_restore", "Alarm restored"),
        ("sos_alarm", "Emergency call"),
        ("e_btnOnceAction", "Emergency button pressed"),
        ("watersensor_alarm", "Water leak detected"),
        ("high_temperature_alarm", "Temperature too high"),
        ("low_temperature_alarm", "Temperature too low"),
        ("high_humidity_alarm", "Humidity too high"),
        ("low_humidity_alarm", "Humidity too low"),
        ("pir_alarm", "PIR motion detected"),
        ("pir_cleared", "PIR alarm cleared"),
        ("e_alarmPIR", "PIR motion detected"),
        ("e_clearAlarmPIR", "PIR alarm cleared"),
        ("temper_alarm", "Tamper detected"),
        ("unlock_alarm", "Door opened"),
        ("lock_alarm", "Door closed"),
        ("e_unlockAlarm", "Door opened"),
        ("e_lockAlarm", "Door closed"),
        ("siren_warning", "Siren alarm"),
        ("siren_alarm_cleared", "Siren cleared"),
        ("siren_alarm_failed", "Siren alarm failed"),
        ("siren_alarm_clear_fail", "Siren clear failed"),
        ("e_sirenAlarm", "Siren alarm"),
        ("e_clearSirenAlarm", "Siren cleared"),
        ("e_sirenAlarmFail", "Siren alarm failed"),
        ("e_clearSirenAlarmFail", "Siren clear failed"),
    ],
)
@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_product_model_events_localized(
    hass: HomeAssistant, msg_type: str, expected_fragment: str
) -> None:
    """Product-model IoT identifiers from sample models have notify copy."""
    title, _ = await _async_build_notification_message(
        hass, {"msg_type": msg_type, "device_name": "Cam"}
    )
    assert title == f"Imou Life · {expected_fragment}"


@pytest.mark.parametrize(
    ("raw", "tz_name", "expected"),
    [
        (1723888205, "UTC", "2024-08-17 09:50:05"),
        (1723888205, "Asia/Shanghai", "2024-08-17 17:50:05"),
        ("2026-08-17T14:30:05", "UTC", "2026-08-17 14:30:05"),
        ("2026-08-17T14:30:05Z", "Asia/Shanghai", "2026-08-17 22:30:05"),
        ("20260817T143005", "UTC", "2026-08-17 14:30:05"),
        ("20260817T143005Z", "Asia/Shanghai", "2026-08-17 22:30:05"),
        ("14:30:05", "UTC", "14:30:05"),
        (None, "UTC", ""),
    ],
)
def test_format_notification_time_normalizes_iot_and_iso(
    raw, tz_name, expected
) -> None:
    """Normalize full datetimes to HA local time; time-only stays as-is."""
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(tz_name)
    assert _format_notification_time(raw, tz) == expected


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_notify_ignores_cloud_device_name(hass: HomeAssistant) -> None:
    """Notify body never falls back to cloud dname/cname."""
    _title, message = await _async_build_notification_message(
        hass,
        {
            "msg_type": "alarmLocal",
            "name": "Cloud Dname",
            "device_id": "SN1",
        },
    )
    assert "SN1" in message
    assert "Cloud Dname" not in message


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize(
    ("msg_type", "expected"),
    [
        ("human", "Person detected"),
        ("Doorbell", "Doorbell pressed"),
        ("alarmPIR", "PIR motion detected"),
        ("e_aiVehArea", "Vehicle entered the area"),
    ],
)
async def test_webhook_alarm_copy_is_an_event_sentence(
    hass: HomeAssistant, msg_type: str, expected: str
) -> None:
    """English titles follow common security-notification phrasing."""
    title, _ = await _async_build_notification_message(
        hass, {"msg_type": msg_type, "device_name": "Cam"}
    )
    assert title == f"Imou Life · {expected}"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_unknown_msg_type_uses_unknown_alarm(
    hass: HomeAssistant,
) -> None:
    """Unmapped ids do not crash; the title uses the unknown-alarm label."""
    from custom_components.imou_life.const import DOMAIN
    from homeassistant.helpers import translation

    await translation.async_get_translations(
        hass, "en", "selector", integrations={DOMAIN}
    )
    title, message = await _async_build_notification_message(
        hass, {"msg_type": "totallyUnknownType", "device_name": "Cam"}
    )
    assert title == "Imou Life · Alarm"
    assert "Type:" not in message
    assert "Device: Cam" in message


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_notification_includes_area_when_assigned(
    hass: HomeAssistant,
) -> None:
    """HA area becomes a location line; no empty location when unset."""
    await hass.config.async_set_time_zone("UTC")
    config_entry = MockConfigEntry(domain=DOMAIN, data={})
    config_entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, "SN1_0")},
        name="Front Door Cam",
    )
    area = ar.async_get(hass).async_get_or_create("Front yard")
    registry.async_update_device(device.id, area_id=area.id)

    _title, message = await _async_build_notification_message(
        hass,
        {
            "msg_type": "human",
            "device_id": "SN1",
            "channel_id": "0",
            "device_name": "Front Door Cam",
        },
    )
    assert "Device: Front Door Cam" in message
    assert "Location: Front yard" in message

    registry.async_update_device(device.id, area_id=None)
    _title, message = await _async_build_notification_message(
        hass,
        {
            "msg_type": "human",
            "device_id": "SN1",
            "channel_id": "0",
            "device_name": "Front Door Cam",
        },
    )
    assert "Location:" not in message


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_notification_includes_labels_when_assigned(
    hass: HomeAssistant,
) -> None:
    """HA device labels become a labels line; omit when none."""
    await hass.config.async_set_time_zone("UTC")
    config_entry = MockConfigEntry(domain=DOMAIN, data={})
    config_entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, "SN1_0")},
        name="Front Door Cam",
    )
    label_reg = lr.async_get(hass)
    outdoor = label_reg.async_create("Outdoor")
    priority = label_reg.async_create("Priority")
    registry.async_update_device(
        device.id, labels={outdoor.label_id, priority.label_id}
    )

    _title, message = await _async_build_notification_message(
        hass,
        {
            "msg_type": "human",
            "device_id": "SN1",
            "channel_id": "0",
            "device_name": "Front Door Cam",
        },
    )
    assert "Device: Front Door Cam" in message
    assert "Labels: Outdoor / Priority" in message

    registry.async_update_device(device.id, labels=set())
    _title, message = await _async_build_notification_message(
        hass,
        {
            "msg_type": "human",
            "device_id": "SN1",
            "channel_id": "0",
            "device_name": "Front Door Cam",
        },
    )
    assert "Labels:" not in message


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_notify_prefers_ha_device_name(hass: HomeAssistant) -> None:
    """Notify body uses HA device_name over push name."""
    _title, message = await _async_build_notification_message(
        hass,
        {
            "msg_type": "alarmLocal",
            "device_name": "HA Front",
            "name": "Cloud Dname",
            "device_id": "SN1",
        },
    )
    assert "HA Front" in message
    assert "Cloud Dname" not in message


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_event_includes_ha_device_name(hass: HomeAssistant) -> None:
    """Bus events expose registry display name as device_name."""
    events: list[Event] = []
    hass.bus.async_listen(EVENT_IMOU_ALARM, events.append)
    setup_imou_runtime(hass, push_enabled=True, selected_devices=["SN1"])
    entry = next(iter(hass.config_entries.async_entries(DOMAIN)))
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "SN1_0")},
        name="Cloud",
    )
    registry.async_update_device(device.id, name_by_user="HA Front")

    response = await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest(
            {
                "msgType": "alarmLocal",
                "deviceId": "SN1",
                "channelId": "0",
                "dname": "Cloud Dname",
            }
        ),
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert response.status == 200
    assert len(events) == 1
    assert events[0].data["device_name"] == "HA Front"
    assert events[0].data["name"] == "Cloud Dname"
    assert events[0].data["device_id"] == "SN1"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_iot_uses_product_key_ha_name(hass: HomeAssistant) -> None:
    """IoT iotEvent with monitor.channel still resolves the accessory HA name."""
    notify_calls = async_mock_service(hass, "notify", "persistent_notification")
    setup_imou_runtime(
        hass,
        push_enabled=True,
        selected_devices=["ACC1"],
        notify_services=["notify.persistent_notification"],
        register_ha_devices=False,
    )
    entry = next(iter(hass.config_entries.async_entries(DOMAIN)))
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "ACC1_pidSmoke")},
        name="Cloud Smoke",
    )
    registry.async_update_device(device.id, name_by_user="厨房烟感")
    runtime = get_runtime_data(entry)
    assert runtime is not None
    runtime.coordinator.device_manager.delegate.async_resolve_event_identifier = (
        AsyncMock(return_value="e_hxSmokeAlarm")
    )

    response = await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest(
            {
                "msgType": "iotEvent",
                "pid": "pidSmoke",
                "did": "ACC1",
                "dname": "Cloud Smoke",
                "localTime": "2026-08-17T14:30:05",
                "content": {
                    "event": "303200",
                    "monitor": {"channel": 0, "action": 1},
                },
            }
        ),
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert response.status == 200
    assert len(notify_calls) == 1
    assert "data" not in notify_calls[0].data
    assert notify_calls[0].data["title"] == "Imou Life · Smoke detected"
    assert "厨房烟感" in notify_calls[0].data["message"]
    assert "ACC1" not in notify_calls[0].data["message"]
    assert "Type:" not in notify_calls[0].data["message"]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_acks_before_identifier_resolve(hass: HomeAssistant) -> None:
    """HTTP 200 must not wait on cold getProductModel resolve."""
    events: list[Event] = []
    hass.bus.async_listen(EVENT_IMOU_EVENT, events.append)
    runtime = setup_imou_runtime(hass, push_enabled=True, selected_devices=["SN1"])
    gate = asyncio.Event()

    async def _slow_resolve(_product_id: str, _key: str) -> str | None:
        await gate.wait()
        return "human"

    runtime.coordinator.device_manager.delegate.async_resolve_event_identifier = (
        AsyncMock(side_effect=_slow_resolve)
    )

    response = await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest(
            {
                "msgType": "iotEvent",
                "pid": "mhpf7Dsz",
                "deviceId": "SN1",
                "content": {"event": "33000"},
            }
        ),
    )

    assert response.status == 200
    assert events == []
    assert runtime.push_msg_type_counts == {}

    gate.set()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(events) == 1
    assert events[0].data["msg_type"] == "human"
    assert runtime.push_last_msg_type == "human"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_iot_event_fires_alarm(hass: HomeAssistant) -> None:
    """Top-level iotEvent fires imou_life_alarm with pid/outputData."""
    generic_events: list[Event] = []
    alarm_events: list[Event] = []
    hass.bus.async_listen(EVENT_IMOU_EVENT, generic_events.append)
    hass.bus.async_listen(EVENT_IMOU_ALARM, alarm_events.append)
    setup_imou_runtime(hass, push_enabled=True, selected_devices=["TESTQWERXXXX"])

    response = await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest(
            {
                "msgType": "iotEvent",
                "pid": "mhpf7Dsz",
                "did": "TESTQWERXXXX",
                "dname": "Gate",
                "content": {"event": "33000", "outputData": {"bar": 2}},
            }
        ),
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert response.status == 200
    assert len(generic_events) == 1
    assert len(alarm_events) == 1
    assert alarm_events[0].data["msg_type"] == "iotEvent"
    assert alarm_events[0].data["product_id"] == "mhpf7Dsz"
    assert alarm_events[0].data["outputData"] == {"bar": 2}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_electricity_is_not_alarm(hass: HomeAssistant) -> None:
    """electricity pushes fire generic event only."""
    generic_events: list[Event] = []
    alarm_events: list[Event] = []
    hass.bus.async_listen(EVENT_IMOU_EVENT, generic_events.append)
    hass.bus.async_listen(EVENT_IMOU_ALARM, alarm_events.append)
    setup_imou_runtime(hass, push_enabled=True, selected_devices=["device_1"])

    response = await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest({"msgType": "electricity", "deviceId": "device_1"}),
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert response.status == 200
    assert len(generic_events) == 1
    assert len(alarm_events) == 0
    assert generic_events[0].data["msg_type"] == "electricity"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_iot_ignores_non_iot_event_msg_type(hass: HomeAssistant) -> None:
    """IoT devices (pid present) only accept the iotEvent envelope."""
    generic_events: list[Event] = []
    alarm_events: list[Event] = []
    hass.bus.async_listen(EVENT_IMOU_EVENT, generic_events.append)
    hass.bus.async_listen(EVENT_IMOU_ALARM, alarm_events.append)
    runtime = setup_imou_runtime(hass, push_enabled=True, selected_devices=["device_1"])
    runtime.coordinator.device_manager.delegate.async_resolve_event_identifier = (
        AsyncMock(return_value="e_abAlarmSound")
    )

    response = await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest(
            {
                "msgType": "e_abAlarmSound",
                "pid": "pid1",
                "did": "device_1",
                "localTime": "20260817T143005",
            }
        ),
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert response.status == 200
    assert generic_events == []
    assert alarm_events == []
    runtime.coordinator.device_manager.delegate.async_resolve_event_identifier.assert_not_awaited()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_resolves_numeric_msg_type_via_iot_event(
    hass: HomeAssistant,
) -> None:
    """Numeric content.event refs under iotEvent are rewritten to identifiers."""
    generic_events: list[Event] = []
    alarm_events: list[Event] = []
    hass.bus.async_listen(EVENT_IMOU_EVENT, generic_events.append)
    hass.bus.async_listen(EVENT_IMOU_ALARM, alarm_events.append)

    runtime = setup_imou_runtime(hass, push_enabled=True, selected_devices=["device_1"])
    runtime.coordinator.device_manager.delegate.async_resolve_event_identifier = (
        AsyncMock(return_value="doorOpen")
    )

    response = await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest(
            {
                "msgType": "iotEvent",
                "pid": "pid1",
                "deviceId": "device_1",
                "content": {"event": "123900"},
            }
        ),
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert response.status == 200
    assert len(generic_events) == 1
    assert len(alarm_events) == 1
    assert generic_events[0].data["msg_type"] == "doorOpen"
    runtime.coordinator.device_manager.delegate.async_resolve_event_identifier.assert_awaited_once_with(
        "pid1", "123900"
    )


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_resolves_iot_event_content_event(hass: HomeAssistant) -> None:
    """iotEvent content.event is rewritten; classification uses the identifier."""
    generic_events: list[Event] = []
    alarm_events: list[Event] = []
    hass.bus.async_listen(EVENT_IMOU_EVENT, generic_events.append)
    hass.bus.async_listen(EVENT_IMOU_ALARM, alarm_events.append)

    runtime = setup_imou_runtime(
        hass, push_enabled=True, selected_devices=["TESTQWERXXXX"]
    )
    runtime.coordinator.device_manager.delegate.async_resolve_event_identifier = (
        AsyncMock(return_value="doorOpen")
    )

    response = await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest(
            {
                "msgType": "iotEvent",
                "pid": "mhpf7Dsz",
                "did": "TESTQWERXXXX",
                "content": {"event": "33000"},
            }
        ),
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert response.status == 200
    assert len(alarm_events) == 1
    assert generic_events[0].data["msg_type"] == "doorOpen"
    runtime.coordinator.device_manager.delegate.async_resolve_event_identifier.assert_awaited_once_with(
        "mhpf7Dsz", "33000"
    )


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize(
    ("identifier", "expect_alarm"),
    [
        ("e_storageEmpty", False),
        ("e_storageAbnormal", False),
        ("e_upgradeSuccess", False),
        ("e_upgradeFail", False),
        ("upgrading", False),
        ("home", False),
        ("e_matchApSucc", False),
        ("e_videoMotion", True),
        ("e_multiVideoAiPerArea", True),
        ("e_std_aorAlarm", True),
    ],
)
async def test_webhook_classifies_after_iot_identifier_rewrite(
    hass: HomeAssistant, identifier: str, expect_alarm: bool
) -> None:
    """IoT ops identifiers match PaaS denylist; real IoT alarms still notify."""
    generic_events: list[Event] = []
    alarm_events: list[Event] = []
    hass.bus.async_listen(EVENT_IMOU_EVENT, generic_events.append)
    hass.bus.async_listen(EVENT_IMOU_ALARM, alarm_events.append)
    runtime = setup_imou_runtime(hass, push_enabled=True, selected_devices=["SN1"])
    runtime.coordinator.device_manager.delegate.async_resolve_event_identifier = (
        AsyncMock(return_value=identifier)
    )

    response = await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest(
            {
                "msgType": "iotEvent",
                "pid": "pid1",
                "did": "SN1",
                "content": {"event": "15200"},
            }
        ),
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert response.status == 200
    assert len(generic_events) == 1
    assert generic_events[0].data["msg_type"] == identifier
    assert len(alarm_events) == (1 if expect_alarm else 0)
    assert runtime.push_last_msg_type == identifier
    assert runtime.push_msg_type_counts[identifier] == 1


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_skips_resolve_for_paas_string_msg_type(
    hass: HomeAssistant,
) -> None:
    """PaaS string msgTypes without pid are not resolved via product model."""
    generic_events: list[Event] = []
    hass.bus.async_listen(EVENT_IMOU_EVENT, generic_events.append)
    runtime = setup_imou_runtime(hass, push_enabled=True, selected_devices=["device_1"])
    runtime.coordinator.device_manager.delegate.async_resolve_event_identifier = (
        AsyncMock(return_value="shouldNotMatter")
    )

    await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest({"msgType": "videoMotion", "deviceId": "device_1"}),
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert generic_events[0].data["msg_type"] == "videoMotion"
    runtime.coordinator.device_manager.delegate.async_resolve_event_identifier.assert_not_awaited()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_resolve_failure_keeps_original_msg_type(
    hass: HomeAssistant,
) -> None:
    """Resolve errors keep original msg_type and still fire events."""
    generic_events: list[Event] = []
    hass.bus.async_listen(EVENT_IMOU_EVENT, generic_events.append)
    runtime = setup_imou_runtime(hass, push_enabled=True, selected_devices=["device_1"])
    runtime.coordinator.device_manager.delegate.async_resolve_event_identifier = (
        AsyncMock(side_effect=RuntimeError("api down"))
    )

    await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest(
            {
                "msgType": "iotEvent",
                "pid": "pid1",
                "deviceId": "device_1",
                "content": {"event": "123900"},
            }
        ),
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert generic_events[0].data["msg_type"] == "iotEvent"


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
    await hass.async_block_till_done(wait_background_tasks=True)

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
    await hass.async_block_till_done(wait_background_tasks=True)

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
    await hass.async_block_till_done(wait_background_tasks=True)

    assert response.status == 200
    assert len(generic_events) == 1
    assert alarm_events == []


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_records_msg_type_counts(hass: HomeAssistant) -> None:
    """Accepted pushes increment runtime msgType counters."""
    runtime = setup_imou_runtime(hass, push_enabled=True, selected_devices=["device_1"])

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
    await hass.async_block_till_done(wait_background_tasks=True)

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
    await hass.async_block_till_done(wait_background_tasks=True)

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


def _register_notify_on_alarm_switch(
    hass: HomeAssistant,
    *,
    device_key: str,
    switch_on: bool | None = True,
    state: str | None = None,
) -> None:
    registry = er.async_get(hass)
    switch = registry.async_get_or_create(
        "switch",
        DOMAIN,
        f"{device_key}${PARAM_NOTIFY_ON_ALARM}",
        suggested_object_id="front_notify_on_alarm",
    )
    hass.states.async_set(
        switch.entity_id,
        state if state is not None else (STATE_ON if switch_on else STATE_OFF),
    )


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_skips_notify_when_device_switch_off(
    hass: HomeAssistant,
) -> None:
    """A per-device notify switch off still fires alarm events."""
    alarm_events: list[Event] = []
    hass.bus.async_listen(EVENT_IMOU_ALARM, alarm_events.append)
    setup_imou_runtime(
        hass,
        push_enabled=True,
        selected_devices=["SN1"],
        notify_services=["notify.test"],
    )
    _register_notify_on_alarm_switch(hass, device_key="SN1_0", switch_on=False)
    calls = async_mock_service(hass, "notify", "test")

    await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest({"msgType": "human", "deviceId": "SN1", "channelId": "0"}),
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(alarm_events) == 1
    assert calls == []


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_notifies_when_device_switch_on(hass: HomeAssistant) -> None:
    """Account targets plus an on switch send the alarm notification."""
    setup_imou_runtime(
        hass,
        push_enabled=True,
        selected_devices=["SN1"],
        notify_services=["notify.test"],
    )
    _register_notify_on_alarm_switch(hass, device_key="SN1_0", switch_on=True)
    calls = async_mock_service(hass, "notify", "test")

    await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest({"msgType": "human", "deviceId": "SN1", "channelId": "0"}),
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(calls) == 1


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_notifies_when_device_switch_missing(
    hass: HomeAssistant,
) -> None:
    """No switch entity means notify stays on, matching the default."""
    setup_imou_runtime(
        hass,
        push_enabled=True,
        selected_devices=["SN1"],
        notify_services=["notify.test"],
    )
    calls = async_mock_service(hass, "notify", "test")

    await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest({"msgType": "human", "deviceId": "SN1", "channelId": "0"}),
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(calls) == 1


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("switch_state", [STATE_UNAVAILABLE, STATE_UNKNOWN])
async def test_webhook_notifies_when_device_switch_not_off(
    hass: HomeAssistant, switch_state: str
) -> None:
    """Unavailable/unknown switch keeps the default-on notify behavior."""
    setup_imou_runtime(
        hass,
        push_enabled=True,
        selected_devices=["SN1"],
        notify_services=["notify.test"],
    )
    _register_notify_on_alarm_switch(hass, device_key="SN1_0", state=switch_state)
    calls = async_mock_service(hass, "notify", "test")

    await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest({"msgType": "human", "deviceId": "SN1", "channelId": "0"}),
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(calls) == 1


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_iot_notify_uses_accessory_not_camera(
    hass: HomeAssistant,
) -> None:
    """Accessory iotEvent must not inherit the parent camera notify switch."""
    setup_imou_runtime(
        hass,
        push_enabled=True,
        selected_devices=["ACC1"],
        notify_services=["notify.test"],
        register_ha_devices=False,
    )
    entry = next(iter(hass.config_entries.async_entries(DOMAIN)))
    register_imou_ha_device(hass, entry, "ACC1", channel_id="0", name="Garden Cam")
    register_imou_ha_device(
        hass,
        entry,
        "ACC1",
        channel_id=None,
        product_id="pidSmoke",
        name="Kitchen Smoke",
    )
    _register_notify_on_alarm_switch(hass, device_key="ACC1_0", switch_on=False)
    _register_notify_on_alarm_switch(hass, device_key="ACC1_pidSmoke", switch_on=True)
    calls = async_mock_service(hass, "notify", "test")
    runtime = get_runtime_data(entry)
    assert runtime is not None
    runtime.coordinator.device_manager.delegate.async_resolve_event_identifier = (
        AsyncMock(return_value="e_hxSmokeAlarm")
    )

    await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest(
            {
                "msgType": "iotEvent",
                "pid": "pidSmoke",
                "did": "ACC1",
                "content": {"monitor": {"channel": 0, "action": 1}},
            }
        ),
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(calls) == 1
    assert "Kitchen Smoke" in calls[0].data["message"]
    assert "Garden Cam" not in calls[0].data["message"]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_iot_missing_accessory_switch_does_not_use_camera(
    hass: HomeAssistant,
) -> None:
    """Missing accessory switch defaults on; do not inherit the camera off."""
    setup_imou_runtime(
        hass,
        push_enabled=True,
        selected_devices=["ACC1"],
        notify_services=["notify.test"],
        register_ha_devices=False,
    )
    entry = next(iter(hass.config_entries.async_entries(DOMAIN)))
    register_imou_ha_device(hass, entry, "ACC1", channel_id="0", name="Garden Cam")
    register_imou_ha_device(
        hass,
        entry,
        "ACC1",
        channel_id=None,
        product_id="pidSmoke",
        name="Kitchen Smoke",
    )
    _register_notify_on_alarm_switch(hass, device_key="ACC1_0", switch_on=False)
    calls = async_mock_service(hass, "notify", "test")
    runtime = get_runtime_data(entry)
    assert runtime is not None
    runtime.coordinator.device_manager.delegate.async_resolve_event_identifier = (
        AsyncMock(return_value="e_hxSmokeAlarm")
    )

    await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest(
            {
                "msgType": "iotEvent",
                "pid": "pidSmoke",
                "did": "ACC1",
                "content": {"monitor": {"channel": 0, "action": 1}},
            }
        ),
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(calls) == 1


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_companion_notify_opens_device_page(
    hass: HomeAssistant,
) -> None:
    """notify.mobile_app_* gets url and clickAction to the HA device page."""
    setup_imou_runtime(
        hass,
        push_enabled=True,
        selected_devices=["SN1"],
        notify_services=["notify.mobile_app_phone"],
    )
    registry = dr.async_get(hass)
    device = registry.async_get_device(identifiers={(DOMAIN, "SN1_0")})
    assert device is not None
    calls = async_mock_service(hass, "notify", "mobile_app_phone")

    await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest({"msgType": "human", "deviceId": "SN1", "channelId": "0"}),
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(calls) == 1
    path = f"/config/devices/device/{device.id}"
    assert calls[0].data["title"].startswith("Imou Life ·")
    assert calls[0].data["data"]["url"] == path
    assert calls[0].data["data"]["clickAction"] == path


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_non_companion_notify_has_no_click_data(
    hass: HomeAssistant,
) -> None:
    """qiyewechat.send only receives title and message."""
    setup_imou_runtime(
        hass,
        push_enabled=True,
        selected_devices=["SN1"],
        notify_services=["qiyewechat.send"],
    )
    calls = async_mock_service(hass, "qiyewechat", "send")

    await async_handle_imou_webhook(
        hass,
        "webhook-id",
        MockRequest({"msgType": "human", "deviceId": "SN1", "channelId": "0"}),
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert len(calls) == 1
    assert "data" not in calls[0].data
    assert "url" not in calls[0].data


def _setup_thumbnail_notify(
    hass: HomeAssistant,
    *,
    attach: bool,
    notify_services: list[str],
    device_id: str = "SN1",
) -> tuple[ImouRuntimeData, MockConfigEntry]:
    runtime = setup_imou_runtime(
        hass,
        push_enabled=True,
        selected_devices=[device_id],
        notify_services=notify_services,
        options={PARAM_ATTACH_DECRYPTED_THUMBNAIL: attach},
    )
    entry = next(iter(hass.config_entries.async_entries(DOMAIN)))
    return runtime, entry


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_companion_notify_includes_decrypted_thumb(
    hass: HomeAssistant,
) -> None:
    """Companion notify merges image and attachment when decrypt returns a URL."""
    _runtime, _entry = _setup_thumbnail_notify(
        hass,
        attach=True,
        notify_services=["notify.mobile_app_phone"],
    )
    registry = dr.async_get(hass)
    device = registry.async_get_device(identifiers={(DOMAIN, "SN1_0")})
    assert device is not None
    hass.config.external_url = "https://ha.example.com"
    calls = async_mock_service(hass, "notify", "mobile_app_phone")
    thumb_url = "/local/imou_life/thumbs/1.jpg"
    public_url = "https://ha.example.com/local/imou_life/thumbs/1.jpg"

    async def _inject_thumb(
        _hass: HomeAssistant,
        _entry: MockConfigEntry,
        _runtime: ImouRuntimeData,
        _event_data: dict[str, Any],
    ) -> str:
        return thumb_url

    with patch(
        "custom_components.imou_life.webhook.async_maybe_decrypt_thumbnail",
        AsyncMock(side_effect=_inject_thumb),
    ) as mock_decrypt:
        await async_handle_imou_webhook(
            hass,
            "webhook-id",
            MockRequest(
                {
                    "msgType": "human",
                    "deviceId": "SN1",
                    "channelId": "0",
                    "id": "1",
                    "picUrlArray": ["https://a/big", "https://a/small"],
                }
            ),
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    mock_decrypt.assert_awaited_once()
    assert len(calls) == 1
    path = f"/config/devices/device/{device.id}"
    data = calls[0].data["data"]
    assert data["url"] == path
    assert data["clickAction"] == path
    assert data["image"] == public_url
    assert data["attachment"]["url"] == public_url
    # iOS wants a file extension here, not a MIME type: an unrecognised value
    # makes it drop the attachment, and attachment.url overrides "image", so
    # the notification arrives with no picture at all.
    assert data["attachment"]["content-type"] == "jpg"
    assert "icon_url" not in data


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_thumb_option_off_skips_image(hass: HomeAssistant) -> None:
    """When attach_decrypted_thumbnail is off, Companion data has no image."""
    _setup_thumbnail_notify(
        hass,
        attach=False,
        notify_services=["notify.mobile_app_phone"],
    )
    calls = async_mock_service(hass, "notify", "mobile_app_phone")

    with patch(
        "custom_components.imou_life.pic_thumbnail.LCOpenPicDecoder",
    ) as mock_decoder_cls:
        await async_handle_imou_webhook(
            hass,
            "webhook-id",
            MockRequest(
                {
                    "msgType": "human",
                    "deviceId": "SN1",
                    "channelId": "0",
                    "picUrlArray": ["https://a/big", "https://a/small"],
                }
            ),
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    mock_decoder_cls.assert_not_called()
    assert len(calls) == 1
    assert "image" not in calls[0].data.get("data", {})


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_qiyewechat_thumb_option_on_has_no_data(
    hass: HomeAssistant,
) -> None:
    """qiyewechat.send never receives a data payload, even when thumbs are on."""
    _setup_thumbnail_notify(
        hass,
        attach=True,
        notify_services=["qiyewechat.send"],
    )
    calls = async_mock_service(hass, "qiyewechat", "send")

    with patch(
        "custom_components.imou_life.webhook.async_maybe_decrypt_thumbnail",
        AsyncMock(return_value="/local/imou_life/thumbs/1.jpg"),
    ):
        await async_handle_imou_webhook(
            hass,
            "webhook-id",
            MockRequest(
                {
                    "msgType": "human",
                    "deviceId": "SN1",
                    "channelId": "0",
                    "picUrlArray": ["https://a/big"],
                }
            ),
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    assert len(calls) == 1
    assert "data" not in calls[0].data


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_no_pic_url_array_skips_decrypt(hass: HomeAssistant) -> None:
    """Push without picUrlArray must not invoke decrypt."""
    _setup_thumbnail_notify(
        hass,
        attach=True,
        notify_services=["notify.mobile_app_phone"],
    )
    async_mock_service(hass, "notify", "mobile_app_phone")

    with patch(
        "custom_components.imou_life.pic_thumbnail.LCOpenPicDecoder",
    ) as mock_decoder_cls:
        await async_handle_imou_webhook(
            hass,
            "webhook-id",
            MockRequest({"msgType": "human", "deviceId": "SN1", "channelId": "0"}),
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    mock_decoder_cls.assert_not_called()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_tcm_without_password_skips_decrypt(hass: HomeAssistant) -> None:
    """TCM devices without a password must not call the native decoder."""
    runtime, _entry = _setup_thumbnail_notify(
        hass,
        attach=True,
        notify_services=["notify.mobile_app_phone"],
    )
    device = MagicMock()
    device.device_id = "SN1"
    device.device_ability = "WLAN,TCM"
    runtime.coordinator.devices_by_key = {"SN1_0": device}
    async_mock_service(hass, "notify", "mobile_app_phone")

    with patch(
        "custom_components.imou_life.pic_thumbnail.LCOpenPicDecoder",
    ) as mock_decoder_cls:
        await async_handle_imou_webhook(
            hass,
            "webhook-id",
            MockRequest(
                {
                    "msgType": "human",
                    "deviceId": "SN1",
                    "channelId": "0",
                    "picUrlArray": ["https://a/big", "https://a/small"],
                }
            ),
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    mock_decoder_cls.assert_not_called()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_notify_never_calls_device_image(hass: HomeAssistant) -> None:
    """Alarm notify path must not snapshot via async_get_device_image."""
    runtime = setup_imou_runtime(
        hass,
        push_enabled=True,
        selected_devices=["SN1"],
        notify_services=["notify.mobile_app_phone"],
        options={PARAM_ATTACH_DECRYPTED_THUMBNAIL: True},
    )
    runtime.coordinator.device_manager.async_get_device_image = AsyncMock()
    async_mock_service(hass, "notify", "mobile_app_phone")

    with patch(
        "custom_components.imou_life.webhook.async_maybe_decrypt_thumbnail",
        AsyncMock(return_value=None),
    ):
        await async_handle_imou_webhook(
            hass,
            "webhook-id",
            MockRequest(
                {
                    "msgType": "human",
                    "deviceId": "SN1",
                    "channelId": "0",
                    "picUrlArray": ["https://a/big"],
                }
            ),
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    runtime.coordinator.device_manager.async_get_device_image.assert_not_awaited()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_decrypts_without_notify_and_sets_event_path(
    hass: HomeAssistant,
) -> None:
    """Decrypt still runs with no notify targets so the camera still can update."""
    _setup_thumbnail_notify(hass, attach=True, notify_services=[])
    events: list[Event] = []
    hass.bus.async_listen(EVENT_IMOU_ALARM, events.append)
    thumb_url = "/local/imou_life/thumbs/1.jpg"

    with patch(
        "custom_components.imou_life.webhook.async_maybe_decrypt_thumbnail",
        AsyncMock(return_value=thumb_url),
    ) as mock_decrypt:
        await async_handle_imou_webhook(
            hass,
            "webhook-id",
            MockRequest(
                {
                    "msgType": "human",
                    "deviceId": "SN1",
                    "channelId": "0",
                    "picUrlArray": ["https://a/big"],
                }
            ),
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    mock_decrypt.assert_awaited_once()
    assert len(events) == 1
    assert events[0].data["thumbnail_path"] == thumb_url


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_web_notification_embeds_thumb(hass: HomeAssistant) -> None:
    """A drawer target gets one entry carrying the decrypted still as markdown."""
    _setup_thumbnail_notify(
        hass,
        attach=True,
        notify_services=["notify.persistent_notification"],
    )
    calls = async_mock_service(hass, "notify", "persistent_notification")
    thumb_url = "/local/imou_life/thumbs/1.jpg"

    with patch(
        "custom_components.imou_life.webhook.async_maybe_decrypt_thumbnail",
        AsyncMock(return_value=thumb_url),
    ):
        await async_handle_imou_webhook(
            hass,
            "webhook-id",
            MockRequest(
                {
                    "msgType": "human",
                    "deviceId": "SN1",
                    "channelId": "0",
                    "picUrlArray": ["https://a/big"],
                }
            ),
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    # The image entry stands in for the target, so it must not fire as well.
    assert calls == []
    notifications = async_get_persistent_notifications(hass)
    notification = notifications["imou_life_alarm_SN1_0"]
    assert notification["message"].startswith(f"![]({thumb_url})")
    assert notification["title"].startswith("Imou Life ·")


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_no_web_notification_without_drawer_target(
    hass: HomeAssistant,
) -> None:
    """A decrypted still alone must not push into the drawer; it is opt-in."""
    _setup_thumbnail_notify(
        hass,
        attach=True,
        notify_services=["notify.mobile_app_phone"],
    )
    async_mock_service(hass, "notify", "mobile_app_phone")

    with patch(
        "custom_components.imou_life.webhook.async_maybe_decrypt_thumbnail",
        AsyncMock(return_value="/local/imou_life/thumbs/1.jpg"),
    ):
        await async_handle_imou_webhook(
            hass,
            "webhook-id",
            MockRequest(
                {
                    "msgType": "human",
                    "deviceId": "SN1",
                    "channelId": "0",
                    "picUrlArray": ["https://a/big"],
                }
            ),
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    assert async_get_persistent_notifications(hass) == {}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_drawer_target_still_fires_without_thumb(
    hass: HomeAssistant,
) -> None:
    """Text-only alarms leave the drawer target as the only web notification."""
    _setup_thumbnail_notify(
        hass,
        attach=True,
        notify_services=["notify.persistent_notification"],
    )
    calls = async_mock_service(hass, "notify", "persistent_notification")

    with patch(
        "custom_components.imou_life.webhook.async_maybe_decrypt_thumbnail",
        AsyncMock(return_value=None),
    ):
        await async_handle_imou_webhook(
            hass,
            "webhook-id",
            MockRequest({"msgType": "human", "deviceId": "SN1", "channelId": "0"}),
        )
        await hass.async_block_till_done(wait_background_tasks=True)

    assert len(calls) == 1
    assert "imou_life_alarm_SN1_0" not in async_get_persistent_notifications(hass)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_bounds_concurrent_dispatch(hass: HomeAssistant) -> None:
    """Accepted pushes must not pile unbounded background work."""
    from custom_components.imou_life.const import PUSH_DISPATCH_LIMIT

    setup_imou_runtime(hass, selected_devices=["SN1"])
    started = 0
    max_seen = 0
    release = asyncio.Event()

    async def _slow_dispatch(*_args: object, **_kwargs: object) -> None:
        nonlocal started, max_seen
        started += 1
        max_seen = max(max_seen, started)
        await release.wait()
        started -= 1

    with patch(
        "custom_components.imou_life.webhook._async_dispatch_imou_push",
        side_effect=_slow_dispatch,
    ):
        for _ in range(PUSH_DISPATCH_LIMIT + 4):
            await async_handle_imou_webhook(
                hass,
                "webhook-id",
                MockRequest({"msgType": "human", "deviceId": "SN1", "channelId": "0"}),
            )
            for _ in range(20):
                await asyncio.sleep(0)
                if max_seen >= PUSH_DISPATCH_LIMIT:
                    break
        assert max_seen == PUSH_DISPATCH_LIMIT
        release.set()
        await hass.async_block_till_done(wait_background_tasks=True)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_zh_cn_uses_simplified_chinese_copy(
    hass: HomeAssistant,
) -> None:
    """zh-CN must resolve to zh-Hans notify templates."""
    from custom_components.imou_life.const import DOMAIN
    from homeassistant.helpers import translation

    hass.config.language = "zh-CN"
    await translation.async_get_translations(
        hass, "zh-Hans", "selector", integrations={DOMAIN}
    )
    title, message = await _async_build_notification_message(
        hass, {"msg_type": "videoMotion", "device_name": "Cam"}
    )
    assert title.startswith("Imou Life ·")
    assert "设备\uff1aCam" in message


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_german_device_line(hass: HomeAssistant) -> None:
    """de.json must drive the notify body, not the English fallback."""
    from custom_components.imou_life.const import DOMAIN
    from homeassistant.helpers import translation

    hass.config.language = "de"
    await translation.async_get_translations(
        hass, "de", "selector", integrations={DOMAIN}
    )
    _title, message = await _async_build_notification_message(
        hass, {"msg_type": "videoMotion", "device_name": "Cam"}
    )
    assert "Gerät: Cam" in message
    assert "Device: Cam" not in message
