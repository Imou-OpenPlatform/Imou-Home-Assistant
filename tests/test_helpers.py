"""Tests for Imou Life shared helpers and device key helpers."""

import pytest
from custom_components.imou_life.const import DOMAIN, imou_life_device_key_from_ids
from custom_components.imou_life.helpers import (
    notify_service_selector_options,
    parse_notify_services,
    resolve_ha_device_name,
)
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry


def test_device_key_from_ids_prefers_channel_id() -> None:
    assert imou_life_device_key_from_ids("SN1", "0", "pid") == "SN1_0"
    assert imou_life_device_key_from_ids("SN1", 0, "pid") == "SN1_0"


def test_device_key_from_ids_uses_product_when_no_channel() -> None:
    assert imou_life_device_key_from_ids("SN1", None, "pidX") == "SN1_pidX"


def test_device_key_from_ids_incomplete_returns_none() -> None:
    assert imou_life_device_key_from_ids(None, "0", "pid") is None
    assert imou_life_device_key_from_ids("SN1", None, None) is None


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
    assert options == [
        {"value": "notify.send_message", "label": "notify.send_message"}
    ]


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
async def test_resolve_ha_device_name_missing_returns_none(
    hass: HomeAssistant,
) -> None:
    assert resolve_ha_device_name(hass, "missing", channel_id="0") is None
