"""Tests for Imou Life shared helpers and device key helpers."""

from types import SimpleNamespace

import pytest
from custom_components.imou_life.const import (
    DOMAIN,
    EVENT_PUSH_TYPE_CAPABILITY_EVENT,
    EVENT_PUSH_TYPE_DEVICE_STATUS,
    EVENT_PUSH_TYPE_NUMBERSTAT,
    EVENT_PUSH_TYPES_ALL,
    PARAM_ATTACH_DECRYPTED_THUMBNAIL,
    PARAM_ENABLE_EVENT_PUSH,
    PARAM_EVENT_PUSH_TYPES,
    event_push_selector_options,
    imou_life_device_key_from_ids,
    imou_life_device_keys_from_ids,
    sanitize_event_push_types,
)
from custom_components.imou_life.helpers import (
    alarm_push_active,
    alarm_type_option_key,
    async_deliver_message_to_targets,
    camera_channel_devices,
    decrypt_pictures_active,
    default_notify_service_targets,
    display_language,
    fill_template,
    iot_property_push_active,
    notify_service_selector_options,
    notify_services_from_entry_options,
    parse_notify_services,
    resolve_ha_device_entry,
    resolve_ha_device_name,
    resolve_ui_language,
    translated_name_override,
)
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry


def test_device_key_from_ids_prefers_channel_id() -> None:
    assert imou_life_device_key_from_ids("SN1", "0", "pid") == "SN1_0"
    assert imou_life_device_key_from_ids("SN1", 0, "pid") == "SN1_0"


def test_device_keys_from_ids_includes_product_fallback() -> None:
    """IoT lookup tries the product registry key before the camera channel."""
    assert imou_life_device_keys_from_ids("SN1", 0, "pidX") == ["SN1_pidX", "SN1_0"]
    assert imou_life_device_keys_from_ids("SN1", None, "pidX") == [
        "SN1_pidX",
        "SN1_0",
    ]


def test_device_keys_from_ids_omitted_channel_tries_primary() -> None:
    """Multi-lens pushes without channel still try did_0 after did_pid."""
    assert imou_life_device_keys_from_ids("SN1", None, None) == ["SN1_0"]
    assert imou_life_device_keys_from_ids("SN1", None, "multiPid") == [
        "SN1_multiPid",
        "SN1_0",
    ]


def test_device_key_from_ids_uses_product_when_no_channel() -> None:
    assert imou_life_device_key_from_ids("SN1", None, "pidX") == "SN1_pidX"


def test_device_key_from_ids_incomplete_returns_none() -> None:
    assert imou_life_device_key_from_ids(None, "0", "pid") is None
    # device_id alone still yields primary-channel fallback for multi-lens.
    assert imou_life_device_key_from_ids("SN1", None, None) == "SN1_0"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, []),
        ("", []),
        ([], []),
        ("notify.mobile_app_phone", ["notify.mobile_app_phone"]),
        (
            "notify.mobile_app_phone, qiyewechat.send",
            ["notify.mobile_app_phone", "qiyewechat.send"],
        ),
        (
            ["notify.mobile_app_phone", " qiyewechat.send "],
            ["notify.mobile_app_phone", "qiyewechat.send"],
        ),
    ],
)
def test_parse_notify_services_normalizes_legacy_and_list(raw, expected) -> None:
    """Stored comma strings and lists both become a clean service id list."""
    assert parse_notify_services(raw) == expected


@pytest.mark.usefixtures("enable_custom_integrations")
def test_default_notify_targets_include_persistent_and_mobile(
    hass: HomeAssistant,
) -> None:
    """Unset notify options default to the web drawer and companion app services."""
    from homeassistant.core import ServiceCall

    async def _notify(_call: ServiceCall) -> None:
        return None

    hass.services.async_register("notify", "mobile_app_phone", _notify)
    phone = er.async_get(hass).async_get_or_create(
        "notify",
        "mobile_app",
        "phone-default",
        suggested_object_id="pixel_default",
        original_name="Pixel",
    )
    targets = default_notify_service_targets(hass)
    assert "notify.persistent_notification" in targets
    assert "notify.mobile_app_phone" in targets
    assert phone.entity_id not in targets


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({}, "default"),
        ({"notify_services": []}, []),
        ({"notify_services": ["notify.custom"]}, ["notify.custom"]),
    ],
)
@pytest.mark.usefixtures("enable_custom_integrations")
def test_notify_services_from_entry_options(
    hass: HomeAssistant, options: dict, expected: list[str] | str
) -> None:
    """Missing key uses defaults; empty list stays explicit."""

    if expected == "default":
        assert notify_services_from_entry_options(hass, options) == (
            default_notify_service_targets(hass)
        )
        return
    assert notify_services_from_entry_options(hass, options) == expected


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_notify_selector_options_keep_saved_non_notify(
    hass: HomeAssistant,
) -> None:
    """Legacy non-notify actions stay selectable after upgrading to a picker."""

    async def _notify(_call: ServiceCall) -> None:
        return None

    hass.services.async_register("notify", "mobile_app_phone", _notify)
    hass.services.async_register("notify", "send_message", _notify)
    hass.services.async_register("notify", "persistent_notification", _notify)
    options = notify_service_selector_options(
        hass, ["notify.mobile_app_phone", "qiyewechat.send"]
    )
    values = [item["value"] for item in options]
    assert options == [
        {"value": item, "label": item}
        for item in [
            "notify.mobile_app_phone",
            "notify.persistent_notification",
            "qiyewechat.send",
        ]
    ]
    assert "notify.send_message" not in values


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_notify_selector_options_keep_stored_send_message(
    hass: HomeAssistant,
) -> None:
    """A previously saved send_message entry stays so the user can clear it."""

    async def _notify(_call: ServiceCall) -> None:
        return None

    hass.services.async_register("notify", "send_message", _notify)
    options = notify_service_selector_options(hass, ["notify.send_message"])
    assert options == [{"value": "notify.send_message", "label": "notify.send_message"}]


@pytest.mark.usefixtures("enable_custom_integrations")
def test_notify_selector_lists_notify_entities(hass: HomeAssistant) -> None:
    """Logged-in apps are notify entities, so the picker lists those."""
    registry = er.async_get(hass)
    phone = registry.async_get_or_create(
        "notify",
        "mobile_app",
        "phone-1",
        suggested_object_id="pixel",
        original_name="Pixel",
    )
    hass.states.async_set(phone.entity_id, "unknown", {"friendly_name": "Pixel"})
    other = registry.async_get_or_create(
        "notify",
        "telegram",
        "bot",
        suggested_object_id="telegram",
        original_name="Telegram",
    )

    options = notify_service_selector_options(hass, [])
    by_value = {item["value"]: item["label"] for item in options}
    assert by_value[phone.entity_id] == "Pixel"
    assert by_value[other.entity_id] == "Telegram"
    # Companion app entities duplicate their notify services, so they are not
    # part of the default targets.
    assert default_notify_service_targets(hass) == ["notify.persistent_notification"]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_deliver_message_uses_notify_entity(
    hass: HomeAssistant,
) -> None:
    """A notify entity is sent through notify.send_message."""
    registry = er.async_get(hass)
    phone = registry.async_get_or_create(
        "notify",
        "mobile_app",
        "phone-2",
        suggested_object_id="pixel_2",
    )
    calls = []

    async def _send(call: ServiceCall) -> None:
        calls.append(call)

    hass.services.async_register("notify", "send_message", _send)
    await async_deliver_message_to_targets(hass, "Alarm", "Motion", [phone.entity_id])
    assert len(calls) == 1
    assert calls[0].data["entity_id"] == phone.entity_id
    assert calls[0].data["message"] == "Motion"
    assert calls[0].data["title"] == "Alarm"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_resolve_ha_device_entry_returns_registry_row(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "SN1_0")},
        name="Front Door Cam",
    )

    found = resolve_ha_device_entry(hass, "SN1", channel_id="0")
    assert found is not None
    assert found.id == device.id


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_resolve_ha_device_name_prefers_name_by_user(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "SN1_0")},
        name="Cloud Name",
    )
    registry.async_update_device(device.id, name_by_user="Front Door Cam")

    assert (
        resolve_ha_device_name(hass, "SN1", channel_id="0", product_id="pid")
        == "Front Door Cam"
    )


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_resolve_ha_device_name_falls_back_to_name(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "SN1_pidX")},
        name="Plug",
    )

    assert (
        resolve_ha_device_name(hass, "SN1", channel_id=None, product_id="pidX")
        == "Plug"
    )


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_resolve_ha_device_name_falls_back_to_product_key(
    hass: HomeAssistant,
) -> None:
    """Spurious monitor.channel must not hide the IoT accessory registry name."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "SN1_pidX")},
        name="Smoke Sensor",
    )

    assert (
        resolve_ha_device_name(hass, "SN1", channel_id=0, product_id="pidX")
        == "Smoke Sensor"
    )


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_resolve_ha_device_name_prefers_product_when_camera_also_registered(
    hass: HomeAssistant,
) -> None:
    """Accessory pid wins over the parent camera channel when both exist."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "SN1_0")},
        name="Garden Cam",
    )
    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "SN1_pidX")},
        name="Smoke Sensor",
    )

    assert (
        resolve_ha_device_name(hass, "SN1", channel_id=0, product_id="pidX")
        == "Smoke Sensor"
    )


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_resolve_ha_device_name_multi_lens_without_channel(
    hass: HomeAssistant,
) -> None:
    """Multi-lens IoT with no channel_id resolves to the primary channel name."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "SN1_0")},
        name="Front Lens",
    )
    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "SN1_1")},
        name="Side Lens",
    )

    assert (
        resolve_ha_device_name(hass, "SN1", channel_id=None, product_id="multiPid")
        == "Front Lens"
    )


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_resolve_ha_device_name_missing_returns_none(
    hass: HomeAssistant,
) -> None:
    assert resolve_ha_device_name(hass, "missing", channel_id="0") is None


def test_iot_property_push_active_is_always_false() -> None:
    """iotProperty is dropped in 2.0; the helper always returns False."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={
            PARAM_ENABLE_EVENT_PUSH: True,
            PARAM_EVENT_PUSH_TYPES: list(EVENT_PUSH_TYPES_ALL),
        },
    )
    assert iot_property_push_active(entry) is False


def test_alarm_push_active_follows_capability_event() -> None:
    """Motion / doorbell / stills listen when capability events are active."""
    on = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={
            PARAM_ENABLE_EVENT_PUSH: True,
            PARAM_EVENT_PUSH_TYPES: [EVENT_PUSH_TYPE_CAPABILITY_EVENT],
        },
    )
    assert alarm_push_active(on) is True

    push_off = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={PARAM_ENABLE_EVENT_PUSH: False},
    )
    assert alarm_push_active(push_off) is False

    status_only = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={
            PARAM_ENABLE_EVENT_PUSH: True,
            PARAM_EVENT_PUSH_TYPES: [EVENT_PUSH_TYPE_DEVICE_STATUS],
        },
    )
    assert alarm_push_active(status_only) is False


def test_decrypt_pictures_active_needs_alarm_push() -> None:
    """Alarm stills are decrypted whenever capability event push is on."""
    on = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={
            PARAM_ENABLE_EVENT_PUSH: True,
            PARAM_EVENT_PUSH_TYPES: [EVENT_PUSH_TYPE_CAPABILITY_EVENT],
            PARAM_ATTACH_DECRYPTED_THUMBNAIL: True,
        },
    )
    assert decrypt_pictures_active(on) is True

    decrypt_off = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={
            PARAM_ENABLE_EVENT_PUSH: True,
            PARAM_EVENT_PUSH_TYPES: [EVENT_PUSH_TYPE_CAPABILITY_EVENT],
            PARAM_ATTACH_DECRYPTED_THUMBNAIL: False,
        },
    )
    assert decrypt_pictures_active(decrypt_off) is True

    push_off = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={
            PARAM_ENABLE_EVENT_PUSH: False,
            PARAM_ATTACH_DECRYPTED_THUMBNAIL: True,
        },
    )
    assert decrypt_pictures_active(push_off) is False


def test_camera_channel_devices_skips_plugs() -> None:
    """Live view, alarm pictures, and record-on-alarm are camera channels."""
    camera = SimpleNamespace(channel_id="0")
    plug = SimpleNamespace(channel_id=None)
    assert camera_channel_devices([camera, plug]) == [camera]


def test_resolve_ui_language_maps_zh_and_defaults() -> None:
    assert resolve_ui_language("en") == "en"
    assert resolve_ui_language("de") == "de"
    assert resolve_ui_language("zh-Hans") == "zh-Hans"
    assert resolve_ui_language("zh-CN") == "zh-Hans"
    assert resolve_ui_language("ZH") == "zh-Hans"
    assert resolve_ui_language(None) == "en"
    assert resolve_ui_language("") == "en"
    assert resolve_ui_language("   ") == "en"


def test_display_language_prefers_the_user_then_the_system() -> None:
    """A profile language wins. With none set, the system language is used."""
    assert display_language("en", "zh-Hans") == "zh-Hans"
    assert display_language("zh-Hans", None) == "zh-Hans"
    assert display_language("zh-Hans", "   ") == "zh-Hans"
    assert display_language(None, None) == "en"
    assert translated_name_override("Briefing date", "Briefing date") is None
    assert translated_name_override("Briefing date", "智见简报日期") == "智见简报日期"


def test_fill_template_falls_back_when_placeholders_break() -> None:
    assert fill_template("Hi {name}", "Hi {name}", name="Ada") == "Hi Ada"
    assert fill_template("Hi {missing}", "Hi {name}", name="Ada") == "Hi Ada"
    assert fill_template("Hi {broken", "plain", name="Ada") == "plain"


def test_selector_offers_only_the_two_ha_flags() -> None:
    from custom_components.imou_life.const import (
        EVENT_PUSH_TYPE_CAPABILITY_EVENT,
        EVENT_PUSH_TYPE_DEVICE_STATUS,
    )

    assert event_push_selector_options({"alarm", "iot"}) == [
        EVENT_PUSH_TYPE_CAPABILITY_EVENT,
        EVENT_PUSH_TYPE_DEVICE_STATUS,
    ]
    assert event_push_selector_options(set()) == [
        EVENT_PUSH_TYPE_CAPABILITY_EVENT,
        EVENT_PUSH_TYPE_DEVICE_STATUS,
    ]


def test_disabled_push_checks_both_boxes() -> None:
    from custom_components.imou_life.const import (
        EVENT_PUSH_TYPE_CAPABILITY_EVENT,
        EVENT_PUSH_TYPE_DEVICE_STATUS,
        checked_ha_push_types,
    )

    assert checked_ha_push_types(push_enabled=False, account_flags=set()) == [
        EVENT_PUSH_TYPE_CAPABILITY_EVENT,
        EVENT_PUSH_TYPE_DEVICE_STATUS,
    ]


def test_enabled_push_checks_only_flags_present_on_the_account() -> None:
    from custom_components.imou_life.const import (
        EVENT_PUSH_TYPE_CAPABILITY_EVENT,
        checked_ha_push_types,
    )

    assert checked_ha_push_types(
        push_enabled=True, account_flags={"capabilityEvent", "alarm"}
    ) == [EVENT_PUSH_TYPE_CAPABILITY_EVENT]


def test_merge_keeps_unrelated_flags_and_applies_the_two_boxes() -> None:
    from custom_components.imou_life.const import merge_callback_flags

    merged = merge_callback_flags(
        {"alarm", "iot"},
        ["capability_event", "device_status"],
    )
    assert merged == {"alarm", "iot", "capabilityEvent", "deviceStatus"}


def test_merge_drops_an_unchecked_ha_flag() -> None:
    from custom_components.imou_life.const import merge_callback_flags

    merged = merge_callback_flags(
        {"alarm", "iot", "capabilityEvent", "deviceStatus"},
        ["capability_event"],
    )
    assert merged == {"alarm", "iot", "capabilityEvent"}


def test_sanitize_event_push_types_drops_legacy() -> None:
    """客流/人脸 keys and removed v1 types are stripped from stored options."""
    assert sanitize_event_push_types(
        ["alarm", EVENT_PUSH_TYPE_NUMBERSTAT, "device_status", "face_analysis"]
    ) == ["device_status"]


def test_alarm_type_option_key_is_hassfest_safe() -> None:
    """Selector option keys must be [a-z0-9-_]+ for hassfest."""
    assert alarm_type_option_key("videoMotion") == "videomotion"
    assert alarm_type_option_key("e_abAlarmSound") == "e_abalarmsound"
    assert alarm_type_option_key("pm2.5Abnormal") == "pm2-5abnormal"
    assert alarm_type_option_key("UpgradeSuccess") == "upgradesuccess"
