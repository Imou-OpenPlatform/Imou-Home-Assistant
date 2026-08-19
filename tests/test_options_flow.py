"""Tests for Imou Life options flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from custom_components.imou_life.config_flow import (
    SECTION_CAMERA_DEFAULTS,
    SECTION_EVENT_PUSH_LOCAL_RECORDING,
    SECTION_EVENT_PUSH_NOTIFICATIONS,
)
from custom_components.imou_life.const import (
    CONF_HD,
    CONF_HTTPS,
    CONF_SD,
    DOMAIN,
    PARAM_ATTACH_DECRYPTED_THUMBNAIL,
    PARAM_DEFAULT_DEVICE_PASSWORD,
    PARAM_DEVICE_PASSWORDS,
    PARAM_DOWNLOAD_SNAP_WAIT_TIME,
    PARAM_ENABLE_EVENT_PUSH,
    PARAM_ENABLE_POLLING,
    PARAM_EVENT_PUSH_TYPES,
    PARAM_LIVE_PROTOCOL,
    PARAM_LIVE_RESOLUTION,
    PARAM_LOCAL_RECORD_DURATION,
    PARAM_LOCAL_RECORD_PATH,
    PARAM_NOTIFY_SERVICES,
    PARAM_ROTATION_DURATION,
    PARAM_SELECTED_DEVICES,
    PARAM_UPDATE_INTERVAL,
    PARAM_WEBHOOK_ID,
    PARAM_WEBHOOK_URL,
)
from homeassistant.core import ServiceCall
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorMode,
    TextSelector,
)
from pyimouapi.exceptions import RequestFailedException
from pytest_homeassistant_custom_component.common import MockConfigEntry

from . import USER_INPUT


def _notify_selector_options(form_schema) -> list[str]:
    """Return the notify_services SelectSelector option values."""
    section_marker = form_schema.schema[SECTION_EVENT_PUSH_NOTIFICATIONS]
    for key, validator in section_marker.schema.schema.items():
        if getattr(key, "schema", None) == PARAM_NOTIFY_SERVICES:
            assert isinstance(validator, SelectSelector)
            assert validator.config["multiple"] is True
            assert validator.config["mode"] == SelectSelectorMode.DROPDOWN
            values: list[str] = []
            for item in validator.config["options"]:
                if isinstance(item, dict):
                    assert item["label"] == item["value"]
                    values.append(item["value"])
                else:
                    values.append(item)
            return values
    raise AssertionError("notify_services selector missing")


async def _register_notify(hass, name: str) -> None:
    async def _handler(_call: ServiceCall) -> None:
        return None

    hass.services.async_register("notify", name, _handler)


def _event_push_input(**overrides) -> dict:
    """Required alarm-page fields; optional overrides for top-level keys."""
    payload = {
        PARAM_ENABLE_EVENT_PUSH: False,
        PARAM_EVENT_PUSH_TYPES: ["alarm"],
        SECTION_EVENT_PUSH_NOTIFICATIONS: {
            PARAM_NOTIFY_SERVICES: [],
            PARAM_ATTACH_DECRYPTED_THUMBNAIL: False,
            PARAM_DEFAULT_DEVICE_PASSWORD: "",
        },
        SECTION_EVENT_PUSH_LOCAL_RECORDING: {
            PARAM_LOCAL_RECORD_PATH: "",
            PARAM_LOCAL_RECORD_DURATION: 60,
        },
    }
    for key in (PARAM_ENABLE_EVENT_PUSH, PARAM_WEBHOOK_URL, PARAM_EVENT_PUSH_TYPES):
        if key in overrides:
            payload[key] = overrides.pop(key)
    payload.update(overrides)
    return payload


async def _open_poll_devices(hass, flow_id):
    """Open Devices → choose poll list (after the account list is fetched)."""
    result = await hass.config_entries.options.async_configure(
        flow_id, {"next_step_id": "devices"}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "devices_menu"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "select_poll_devices"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "select_poll_devices"
    return result


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_flow_init_shows_menu(hass) -> None:
    """Options init is a menu to pick which settings to edit."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "init"
    assert set(result["menu_options"]) == {
        "general_settings",
        "event_push",
        "alarm_image_passwords",
        "devices",
    }
    placeholders = result.get("description_placeholders") or {}
    assert "native_dir" not in placeholders
    assert "native_support" not in placeholders


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_alarm_image_decrypt_reports_unsupported_platform(hass) -> None:
    """The decrypt menu states linux x86-64 is required when the host is not."""
    from custom_components.imou_life import pic_thumbnail

    hass.config.language = "en"
    with (
        patch.object(pic_thumbnail, "native_platform_supported", return_value=False),
        patch.object(
            pic_thumbnail, "native_platform_label", return_value="linux aarch64"
        ),
    ):
        entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
        entry.add_to_hass(hass)
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "alarm_image_passwords"}
        )
    assert result["step_id"] == "alarm_image_passwords"
    placeholders = result["description_placeholders"]
    assert placeholders["native_dir"].endswith("imou_life/native")
    assert placeholders["native_platform"] == "linux aarch64"
    assert "not supported" in placeholders["native_support"].lower()
    assert "linux x86-64" in placeholders["native_support"]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_alarm_image_decrypt_reports_libs_when_supported(hass) -> None:
    """When the host is supported, the decrypt menu shows the folder and file count."""
    from custom_components.imou_life import pic_thumbnail
    from custom_components.imou_life.pic_thumbnail import (
        NATIVE_CLIENT_SO,
        NATIVE_SDK_SO,
        native_lib_dir,
    )

    native_dir = native_lib_dir(hass)
    native_dir.mkdir(parents=True, exist_ok=True)
    (native_dir / NATIVE_CLIENT_SO).write_bytes(b"")
    (native_dir / NATIVE_SDK_SO).write_bytes(b"")

    hass.config.language = "en"
    with patch.object(pic_thumbnail, "native_platform_supported", return_value=True):
        entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
        entry.add_to_hass(hass)
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "alarm_image_passwords"}
        )
    placeholders = result["description_placeholders"]
    assert placeholders["native_dir"] == str(native_dir)
    assert placeholders["native_libs_found"] == "2"
    assert "supported" in placeholders["native_support"].lower()
    assert "not supported" not in placeholders["native_support"].lower()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_alarm_image_passwords_add_and_delete(hass) -> None:
    """Per-serial passwords save mid-flow; empty password removes the SN."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "alarm_image_passwords"}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "alarm_image_passwords"
    assert result["description_placeholders"]["password_count"] == "0"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_device_password"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "add_device_password"
    schema = result.get("data_schema") or result.get("schema")
    assert schema is not None
    device_field = schema.schema[vol.Required("device_id")]
    assert isinstance(device_field, TextSelector)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"device_id": "SN1", "password": "pw"},
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "alarm_image_passwords"
    assert entry.options[PARAM_DEVICE_PASSWORDS] == {"SN1": "pw"}
    assert result["description_placeholders"]["password_count"] == "1"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_device_password"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"device_id": "SN1", "password": ""},
    )
    assert entry.options[PARAM_DEVICE_PASSWORDS] == {}
    assert result["description_placeholders"]["password_count"] == "0"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish_passwords"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][PARAM_DEVICE_PASSWORDS] == {}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_alarm_image_passwords_uses_device_selector_with_runtime(
    hass,
) -> None:
    """When runtime exists, pick serial numbers from coordinator devices."""
    from custom_components.imou_life.runtime_data import ImouRuntimeData

    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)
    device_a = MagicMock()
    device_a.device_id = "SN-A"
    device_b = MagicMock()
    device_b.device_id = "SN-B"
    coordinator = MagicMock()
    coordinator.devices_by_key = {"SN-A_0": device_a, "SN-B_0": device_b}
    entry.runtime_data = ImouRuntimeData(coordinator=coordinator)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "alarm_image_passwords"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "add_device_password"}
    )
    schema = result.get("data_schema") or result.get("schema")
    device_field = schema.schema[vol.Required("device_id")]
    assert isinstance(device_field, SelectSelector)
    assert device_field.config["options"] == ["SN-A", "SN-B"]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_general_settings_saves_without_devices(hass) -> None:
    """Editing general settings alone must not require the devices steps."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={
            PARAM_ENABLE_EVENT_PUSH: True,
            PARAM_SELECTED_DEVICES: ["device_1"],
            PARAM_UPDATE_INTERVAL: 60,
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": "general_settings"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "general_settings"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            PARAM_ENABLE_POLLING: True,
            PARAM_UPDATE_INTERVAL: 120,
            SECTION_CAMERA_DEFAULTS: {
                PARAM_DOWNLOAD_SNAP_WAIT_TIME: 3,
                PARAM_LIVE_RESOLUTION: CONF_HD,
                PARAM_LIVE_PROTOCOL: CONF_HTTPS,
                PARAM_ROTATION_DURATION: 500,
            },
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][PARAM_UPDATE_INTERVAL] == 120
    assert result["data"][PARAM_ENABLE_EVENT_PUSH] is True
    assert result["data"][PARAM_SELECTED_DEVICES] == ["device_1"]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_general_omitting_camera_defaults_keeps_stored(hass) -> None:
    """Collapsed camera defaults omitted on submit must keep saved values."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={
            PARAM_LIVE_RESOLUTION: CONF_SD,
            PARAM_ROTATION_DURATION: 800,
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": "general_settings"},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            PARAM_ENABLE_POLLING: True,
            PARAM_UPDATE_INTERVAL: 180,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][PARAM_UPDATE_INTERVAL] == 180
    assert result["data"][PARAM_LIVE_RESOLUTION] == CONF_SD
    assert result["data"][PARAM_ROTATION_DURATION] == 800


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow_with_devices")
async def test_options_local_recording_saves_shared_settings(hass) -> None:
    """Shared folder and duration save on the event push form."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={
            PARAM_ENABLE_EVENT_PUSH: True,
            PARAM_SELECTED_DEVICES: ["device_1"],
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": "event_push"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "event_push"

    with patch.object(hass.config, "is_allowed_path", return_value=True):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            _event_push_input(
                **{
                    PARAM_ENABLE_EVENT_PUSH: True,
                    PARAM_WEBHOOK_URL: "https://example.test/hook",
                    SECTION_EVENT_PUSH_LOCAL_RECORDING: {
                        PARAM_LOCAL_RECORD_PATH: " /media/imou ",
                        PARAM_LOCAL_RECORD_DURATION: 45,
                    },
                }
            ),
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][PARAM_LOCAL_RECORD_PATH] == "/media/imou"
    assert result["data"][PARAM_LOCAL_RECORD_DURATION] == 45
    assert result["data"][PARAM_ENABLE_EVENT_PUSH] is True
    assert result["data"][PARAM_SELECTED_DEVICES] == ["device_1"]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_local_recording_rejects_path_not_allowlisted(hass) -> None:
    """A folder outside allowlist_external_dirs must not be saved."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": "event_push"},
    )

    with patch.object(hass.config, "is_allowed_path", return_value=False):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            _event_push_input(
                **{
                    PARAM_WEBHOOK_URL: "",
                    SECTION_EVENT_PUSH_LOCAL_RECORDING: {
                        PARAM_LOCAL_RECORD_PATH: "/media/imou",
                        PARAM_LOCAL_RECORD_DURATION: 60,
                    },
                }
            ),
        )

    assert result["type"] is FlowResultType.FORM
    assert (
        result["errors"][SECTION_EVENT_PUSH_LOCAL_RECORDING][PARAM_LOCAL_RECORD_PATH]
        == "record_path_not_allowed"
    )


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_local_recording_empty_path_is_allowed(hass) -> None:
    """Clearing the folder must save even when no path is allowlisted yet."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={PARAM_LOCAL_RECORD_PATH: "/media/imou"},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": "event_push"},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        _event_push_input(**{PARAM_WEBHOOK_URL: ""}),
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][PARAM_LOCAL_RECORD_PATH] == ""
    assert result["data"][PARAM_LOCAL_RECORD_DURATION] == 60


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow_with_devices")
async def test_options_flow_event_push_step_shows_all_sections(hass) -> None:
    """Event push step shows callback, subscriptions, notifications, and recording."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": "event_push"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "event_push"
    schema = result.get("data_schema") or result.get("schema")
    assert schema is not None
    schema_keys = [getattr(key, "schema", key) for key in schema.schema]
    assert schema_keys == [
        PARAM_ENABLE_EVENT_PUSH,
        PARAM_WEBHOOK_URL,
        PARAM_EVENT_PUSH_TYPES,
        SECTION_EVENT_PUSH_NOTIFICATIONS,
        SECTION_EVENT_PUSH_LOCAL_RECORDING,
    ]
    recording_section = schema.schema[SECTION_EVENT_PUSH_LOCAL_RECORDING]
    assert recording_section.options.get("collapsed") is False
    placeholders = result["description_placeholders"]
    assert placeholders["suggested_url"]


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow_with_devices")
async def test_options_flow_event_push_step_when_enabled(hass) -> None:
    """Event push step keeps subscription defaults when push is already enabled."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={PARAM_ENABLE_EVENT_PUSH: True},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": "event_push"},
    )
    schema = result.get("data_schema") or result.get("schema")
    assert schema is not None
    assert PARAM_EVENT_PUSH_TYPES in schema.schema


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow_with_devices")
async def test_options_flow_event_push_flattens_sections(hass) -> None:
    """Section-based event push input is stored as flat options."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": "event_push"},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        _event_push_input(
            **{
                PARAM_ENABLE_EVENT_PUSH: True,
                PARAM_WEBHOOK_URL: "https://example.test/hook",
            }
        ),
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][PARAM_ENABLE_EVENT_PUSH] is True
    assert result["data"][PARAM_WEBHOOK_URL] == "https://example.test/hook"
    assert result["data"][PARAM_EVENT_PUSH_TYPES] == ["alarm"]


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow_with_devices")
async def test_options_event_push_saves_notify_services_as_list(hass) -> None:
    """Alarm notify picker stores selected notify.* services as a list."""
    await _register_notify(hass, "mobile_app_phone")
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "event_push"}
    )
    assert "notify.mobile_app_phone" in _notify_selector_options(
        result.get("data_schema") or result.get("schema")
    )

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        _event_push_input(
            **{
                PARAM_WEBHOOK_URL: "",
                SECTION_EVENT_PUSH_NOTIFICATIONS: {
                    PARAM_NOTIFY_SERVICES: ["notify.mobile_app_phone"],
                },
            }
        ),
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][PARAM_NOTIFY_SERVICES] == ["notify.mobile_app_phone"]


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow_with_devices")
async def test_options_event_push_migrates_comma_notify_string(hass) -> None:
    """Opening and saving converts a legacy comma-separated string to a list."""
    await _register_notify(hass, "mobile_app_phone")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={
            PARAM_NOTIFY_SERVICES: "notify.mobile_app_phone, qiyewechat.send",
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "event_push"}
    )
    options = _notify_selector_options(
        result.get("data_schema") or result.get("schema")
    )
    assert "notify.mobile_app_phone" in options
    assert "qiyewechat.send" in options

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        _event_push_input(
            **{
                PARAM_WEBHOOK_URL: "",
                SECTION_EVENT_PUSH_NOTIFICATIONS: {
                    PARAM_NOTIFY_SERVICES: [
                        "notify.mobile_app_phone",
                        "qiyewechat.send",
                    ],
                },
            }
        ),
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][PARAM_NOTIFY_SERVICES] == [
        "notify.mobile_app_phone",
        "qiyewechat.send",
    ]


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow_with_devices")
async def test_options_event_push_keeps_custom_webhook_url(hass) -> None:
    """Expanded callback saves the custom URL shown on the form."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={PARAM_WEBHOOK_URL: "https://example.test/kept"},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "event_push"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        _event_push_input(
            **{
                PARAM_WEBHOOK_URL: "https://example.test/kept",
            }
        ),
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][PARAM_WEBHOOK_URL] == "https://example.test/kept"


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow_with_devices")
async def test_options_event_push_saves_decrypted_thumbnail_settings(hass) -> None:
    """Attach-decrypted toggle and default password save on the event push form."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "event_push"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        _event_push_input(
            **{
                PARAM_WEBHOOK_URL: "",
                SECTION_EVENT_PUSH_NOTIFICATIONS: {
                    PARAM_NOTIFY_SERVICES: [],
                    PARAM_ATTACH_DECRYPTED_THUMBNAIL: True,
                    PARAM_DEFAULT_DEVICE_PASSWORD: "secret-pw",
                },
            }
        ),
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][PARAM_ATTACH_DECRYPTED_THUMBNAIL] is True
    assert result["data"][PARAM_DEFAULT_DEVICE_PASSWORD] == "secret-pw"


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow_with_devices")
async def test_options_event_push_preserves_device_passwords_map(hass) -> None:
    """Saving event push must not drop per-device passwords from options."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={PARAM_DEVICE_PASSWORDS: {"SN1": "x"}},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "event_push"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        _event_push_input(**{PARAM_WEBHOOK_URL: ""}),
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][PARAM_DEVICE_PASSWORDS] == {"SN1": "x"}


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow_with_devices")
async def test_options_event_push_preserves_general_and_devices(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={
            PARAM_UPDATE_INTERVAL: 90,
            PARAM_SELECTED_DEVICES: ["device_1"],
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "event_push"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        _event_push_input(
            **{
                PARAM_ENABLE_EVENT_PUSH: True,
                PARAM_WEBHOOK_URL: "https://example.test/hook",
            }
        ),
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][PARAM_ENABLE_EVENT_PUSH] is True
    assert result["data"][PARAM_WEBHOOK_URL] == "https://example.test/hook"
    assert result["data"][PARAM_UPDATE_INTERVAL] == 90
    assert result["data"][PARAM_SELECTED_DEVICES] == ["device_1"]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_event_push_callback_failure_stays_on_form(
    hass, imou_config_flow_with_devices
) -> None:
    """A failed setMessageCallback must not save as success with no error."""
    client = imou_config_flow_with_devices.return_value
    client.async_set_message_callback = AsyncMock(
        side_effect=RequestFailedException("OP1013 quota exceeded")
    )
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "event_push"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        _event_push_input(
            **{
                PARAM_ENABLE_EVENT_PUSH: True,
                PARAM_WEBHOOK_URL: "https://example.test/hook",
            }
        ),
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "event_push"
    assert result["errors"] == {"base": "callback_failed"}
    assert "OP1013" in result["description_placeholders"]["error"]
    assert entry.options.get(PARAM_ENABLE_EVENT_PUSH) is not True
    client.async_set_message_callback.assert_awaited_once()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_event_push_requires_callback_url_when_enabled(hass) -> None:
    """Enabling event push with a blank callback URL must stay on the form."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "event_push"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        _event_push_input(
            **{
                PARAM_ENABLE_EVENT_PUSH: True,
                PARAM_WEBHOOK_URL: "",
            }
        ),
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "event_push"
    assert result["errors"] == {PARAM_WEBHOOK_URL: "callback_url_missing"}
    assert entry.options.get(PARAM_ENABLE_EVENT_PUSH) is not True


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_event_push_does_not_prefill_generated_callback_url(
    hass,
) -> None:
    """The callback field stays empty; the suggested URL is only in the description."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**USER_INPUT, PARAM_WEBHOOK_ID: "hook-id"},
        options={},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    with patch(
        "custom_components.imou_life.config_flow.webhook.async_generate_url",
        return_value="http://192.168.1.2:8123/api/webhook/hook-id",
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "event_push"}
        )

    assert result["description_placeholders"]["suggested_url"] == (
        "http://192.168.1.2:8123/api/webhook/hook-id"
    )
    schema = result.get("data_schema") or result.get("schema")
    suggested = None
    for key in schema.schema:
        if getattr(key, "schema", key) == PARAM_WEBHOOK_URL:
            suggested = (key.description or {}).get("suggested_value")
            break
    assert suggested in (None, "")


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow_with_devices")
async def test_options_select_poll_submits_and_saves(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={PARAM_UPDATE_INTERVAL: 60},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await _open_poll_devices(hass, result["flow_id"])

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {PARAM_SELECTED_DEVICES: ["device_1"]},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][PARAM_SELECTED_DEVICES] == ["device_1"]
    assert result["data"][PARAM_UPDATE_INTERVAL] == 60


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow")
async def test_options_devices_empty_shows_menu(hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": "devices"},
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "no_devices_menu"
    assert set(result["menu_options"]) >= {"bind_device", "finish_without_bind"}


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow")
async def test_options_finish_without_bind_saves_options(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={
            PARAM_UPDATE_INTERVAL: 120,
            PARAM_ENABLE_EVENT_PUSH: False,
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": "devices"},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "finish_without_bind"},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][PARAM_UPDATE_INTERVAL] == 120
    assert result["data"][PARAM_ENABLE_EVENT_PUSH] is False
    # Empty list: poll nothing until the user picks devices in Devices.
    assert result["data"][PARAM_SELECTED_DEVICES] == []


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow")
async def test_saving_with_no_devices_clears_a_stale_selection(hass) -> None:
    """Account listed empty: replace a stale list with poll-nothing.

    Old ids would leave ghost devices after save/reload. An empty list means
    a device bound later from the Imou app is not polled until chosen under
    Devices. Cloud failures still preserve the selection via
    save_without_devices.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={PARAM_SELECTED_DEVICES: ["device_1"]},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "devices"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "finish_without_bind"}
    )

    assert result["data"][PARAM_SELECTED_DEVICES] == []


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow")
async def test_saving_with_no_devices_clears_selection_stored_in_data(hass) -> None:
    """Setup stores the whitelist in entry.data; options must clear that too."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**USER_INPUT, PARAM_SELECTED_DEVICES: ["device_1"]},
        options={},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "devices"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "finish_without_bind"}
    )

    assert result["data"][PARAM_SELECTED_DEVICES] == []
    assert PARAM_SELECTED_DEVICES not in entry.data


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow")
async def test_unreachable_cloud_still_lets_the_other_options_be_saved(hass) -> None:
    """Unreachable cloud in Devices must still allow save without devices.

    Listing failure must not trap the user; existing general options stay intact
    when they choose save without fetching the device list.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={
            PARAM_UPDATE_INTERVAL: 120,
            PARAM_SELECTED_DEVICES: ["device_1"],
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    with patch(
        "custom_components.imou_life.config_flow.async_build_device_map",
        AsyncMock(side_effect=RequestFailedException("cloud down")),
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "devices"}
        )
        assert result["type"] is FlowResultType.MENU
        assert result["step_id"] == "devices_unavailable"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"], user_input={"next_step_id": "save_without_devices"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][PARAM_UPDATE_INTERVAL] == 120
    # Nothing was learned about the account, so the selection is left as it was.
    assert result["data"][PARAM_SELECTED_DEVICES] == ["device_1"]


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow_with_devices")
async def test_retrying_after_an_unreachable_cloud_reaches_the_devices(hass) -> None:
    """The retry option has to actually go back and list the account."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    with patch(
        "custom_components.imou_life.config_flow.async_build_device_map",
        AsyncMock(side_effect=RequestFailedException("cloud down")),
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "devices"}
        )
    assert result["step_id"] == "devices_unavailable"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "devices"}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "devices_menu"


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow_with_devices")
async def test_options_bind_from_devices_form(hass) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={PARAM_SELECTED_DEVICES: ["device_1"]},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "devices"}
    )
    assert result["step_id"] == "devices_menu"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "bind_device"}
    )
    assert result["step_id"] == "bind_device"
    with (
        patch(
            "custom_components.imou_life.config_flow.ImouDeviceManager",
        ) as mock_mgr_cls,
        patch(
            "custom_components.imou_life.config_flow.async_build_device_map",
            AsyncMock(
                return_value={
                    "device_1": "Front Door (IPC) [Online]",
                    "SN001": "Camera SN001 [Online]",
                }
            ),
        ),
    ):
        mock_mgr_cls.return_value.async_bind_device = AsyncMock()
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={"device_id": "SN001", "code": "123456"},
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][PARAM_SELECTED_DEVICES] == ["device_1", "SN001"]


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow")
async def test_options_bind_device_success_merges_selection(hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "devices"}
    )
    assert result["step_id"] == "no_devices_menu"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "bind_device"},
    )
    assert result["step_id"] == "bind_device"
    with (
        patch(
            "custom_components.imou_life.config_flow.ImouDeviceManager",
        ) as mock_mgr_cls,
        patch(
            "custom_components.imou_life.config_flow.async_build_device_map",
            AsyncMock(return_value={"SN001": "Camera SN001 [Online]"}),
        ),
    ):
        mock_mgr_cls.return_value.async_bind_device = AsyncMock()
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={"device_id": "SN001", "code": "123456"},
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    # Binding from Devices records the new device for polling.
    assert result["data"][PARAM_SELECTED_DEVICES] == ["SN001"]


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow")
async def test_options_bind_device_failure_stays_on_form(hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": "devices"},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        user_input={"next_step_id": "bind_device"},
    )
    with patch(
        "custom_components.imou_life.config_flow.ImouDeviceManager",
    ) as mock_mgr_cls:
        mock_mgr_cls.return_value.async_bind_device = AsyncMock(
            side_effect=RequestFailedException("OP1009:device already bound")
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={"device_id": "SN001", "code": "bad"},
        )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bind_device"
    assert result["errors"]["base"] == "bind_failed"
