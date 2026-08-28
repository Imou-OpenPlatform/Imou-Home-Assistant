"""Tests for Imou Life shared helpers and device key helpers."""

import pytest
from custom_components.imou_life.const import (
    DEFAULT_EVENT_PUSH_TYPES,
    DOMAIN,
    EVENT_PUSH_TYPE_ALARM,
    PARAM_ENABLE_EVENT_PUSH,
    PARAM_EVENT_PUSH_TYPES,
    imou_life_device_key_from_ids,
    imou_life_device_keys_from_ids,
)
from custom_components.imou_life.helpers import (
    fill_template,
    iot_property_push_active,
    notify_service_selector_options,
    parse_notify_services,
    resolve_ha_device_entry,
    resolve_ha_device_name,
    resolve_ui_language,
    selector_option_label,
)
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr
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


def test_iot_property_push_active_needs_push_and_iot_type() -> None:
    """Property-push mode is event push plus the iot subscribe type."""
    on = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={
            PARAM_ENABLE_EVENT_PUSH: True,
            PARAM_EVENT_PUSH_TYPES: list(DEFAULT_EVENT_PUSH_TYPES),
        },
    )
    assert iot_property_push_active(on) is True

    push_off = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={PARAM_ENABLE_EVENT_PUSH: False},
    )
    assert iot_property_push_active(push_off) is False

    no_iot = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={
            PARAM_ENABLE_EVENT_PUSH: True,
            PARAM_EVENT_PUSH_TYPES: [EVENT_PUSH_TYPE_ALARM],
        },
    )
    assert iot_property_push_active(no_iot) is False


def test_resolve_ui_language_maps_zh_and_defaults() -> None:
    assert resolve_ui_language("en") == "en"
    assert resolve_ui_language("de") == "de"
    assert resolve_ui_language("zh-Hans") == "zh-Hans"
    assert resolve_ui_language("zh-CN") == "zh-Hans"
    assert resolve_ui_language("ZH") == "zh-Hans"
    assert resolve_ui_language(None) == "en"
    assert resolve_ui_language("") == "en"
    assert resolve_ui_language("   ") == "en"


def test_fill_template_falls_back_when_placeholders_break() -> None:
    assert fill_template("Hi {name}", "Hi {name}", name="Ada") == "Hi Ada"
    assert (
        fill_template("Hi {missing}", "Hi {name}", name="Ada") == "Hi Ada"
    )
    assert fill_template("Hi {broken", "plain", name="Ada") == "plain"
