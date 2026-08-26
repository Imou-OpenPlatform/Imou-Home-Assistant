"""Tests for Imou Life options flow."""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from custom_components.imou_life import config_flow
from custom_components.imou_life.config_flow import (
    SECTION_CAMERA_DEFAULTS,
    SECTION_EVENT_PUSH_NOTIFICATIONS,
)
from custom_components.imou_life.const import (
    CONF_HD,
    CONF_SD,
    DOMAIN,
    PARAM_ATTACH_DECRYPTED_THUMBNAIL,
    PARAM_DEFAULT_DEVICE_PASSWORD,
    PARAM_DEVICE_PASSWORDS,
    PARAM_DOWNLOAD_SNAP_WAIT_TIME,
    PARAM_ENABLE_EVENT_PUSH,
    PARAM_ENABLE_POLLING,
    PARAM_EVENT_PUSH_TYPES,
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
        },
    }
    for key in (PARAM_ENABLE_EVENT_PUSH, PARAM_WEBHOOK_URL, PARAM_EVENT_PUSH_TYPES):
        if key in overrides:
            payload[key] = overrides.pop(key)
    payload.update(overrides)
    return payload


async def _open_poll_devices(hass, flow_id):
    """Open the poll list straight from the options menu."""
    result = await hass.config_entries.options.async_configure(
        flow_id, {"next_step_id": "select_poll_devices"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "select_poll_devices"
    return result


def _assert_returned_to_menu(result) -> None:
    """A page that saved successfully hands control back to the options menu."""
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "init"


@contextmanager
def _decrypt_libs(present: bool = True):
    """Pin the native library probe; the testing config folder is shared."""
    from custom_components.imou_life import pic_thumbnail

    with patch.object(pic_thumbnail, "native_libs_present", return_value=present):
        yield


def _mock_device(serial: str, *, name: str = "", ability: str = "WLAN,TCM"):
    """Build a coordinator device stub for the password form."""
    device = MagicMock()
    device.device_id = serial
    device.device_name = name
    device.device_ability = ability
    return device


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
        "alarm_image_decrypt",
        "local_recording",
        "select_poll_devices",
        "bind_device",
        "finish",
    }
    placeholders = result.get("description_placeholders") or {}
    assert "native_hint" not in placeholders
    # The menu summarises what is on, so no page has to be opened to check.
    assert placeholders["push_state"] == "off"
    assert placeholders["decrypt_state"] == "off"
    assert placeholders["record_state"] == "off"
    assert placeholders["polling_state"] == "on"
    assert placeholders["device_state"] == "all"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_finish_closes_the_dialog(hass) -> None:
    """Done is the only exit now that each page returns to the menu."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={PARAM_UPDATE_INTERVAL: 60},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "finish"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][PARAM_UPDATE_INTERVAL] == 60


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("step", ["alarm_image_decrypt", "local_recording"])
async def test_options_alarm_pages_warn_when_push_is_off(hass, step) -> None:
    """Both pages run off the webhook, so they say nothing happens without it."""
    hass.config.language = "en"
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step}
    )
    assert "Alarm push is off" in result["description_placeholders"]["push_hint"]


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("step", ["alarm_image_decrypt", "local_recording"])
async def test_options_alarm_pages_drop_the_warning_when_push_is_on(hass, step) -> None:
    """With push on the warning has to disappear, not sit there as stale text."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={PARAM_ENABLE_EVENT_PUSH: True},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": step}
    )
    assert result["description_placeholders"]["push_hint"] == ""


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_alarm_image_decrypt_reports_unsupported_platform(hass) -> None:
    """The decrypt page states linux x86-64 is required when the host is not."""
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
            result["flow_id"], {"next_step_id": "alarm_image_decrypt"}
        )
    assert result["step_id"] == "alarm_image_decrypt"
    hint = result["description_placeholders"]["native_hint"]
    assert "not supported" in hint.lower()
    assert "linux aarch64" in hint


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_alarm_image_decrypt_hides_install_hint_when_ready(hass) -> None:
    """A host that already has both libraries gets a status line, not instructions."""
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
            result["flow_id"], {"next_step_id": "alarm_image_decrypt"}
        )
    hint = result["description_placeholders"]["native_hint"]
    assert hint == "decrypt libraries ready (linux x86-64)"
    assert NATIVE_SDK_SO not in hint
    assert str(native_dir) not in hint


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_alarm_image_decrypt_names_missing_libraries(hass) -> None:
    """A host that is missing a library is told which files go where."""
    from custom_components.imou_life import pic_thumbnail
    from custom_components.imou_life.pic_thumbnail import (
        NATIVE_CLIENT_SO,
        NATIVE_SDK_SO,
        native_lib_dir,
    )

    # The testing config folder is shared, so another test may have put the
    # placeholder libraries there already.
    for name in (NATIVE_CLIENT_SO, NATIVE_SDK_SO):
        (native_lib_dir(hass) / name).unlink(missing_ok=True)

    hass.config.language = "en"
    with patch.object(pic_thumbnail, "native_platform_supported", return_value=True):
        entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
        entry.add_to_hass(hass)
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "alarm_image_decrypt"}
        )
    hint = result["description_placeholders"]["native_hint"]
    assert "0/2" in hint
    assert NATIVE_SDK_SO in hint
    assert str(native_lib_dir(hass)) in hint


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_alarm_image_decrypt_saves_switch_and_default_password(
    hass,
) -> None:
    """The switch and the fallback password live on the decrypt page now."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "alarm_image_decrypt"}
    )
    with _decrypt_libs():
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                PARAM_ATTACH_DECRYPTED_THUMBNAIL: True,
                PARAM_DEFAULT_DEVICE_PASSWORD: "secret-pw",
            },
        )
    _assert_returned_to_menu(result)
    assert entry.options[PARAM_ATTACH_DECRYPTED_THUMBNAIL] is True
    assert entry.options[PARAM_DEFAULT_DEVICE_PASSWORD] == "secret-pw"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_alarm_image_decrypt_rejects_switch_without_libraries(
    hass,
) -> None:
    """Turning the switch on where it cannot decrypt would do nothing silently."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "alarm_image_decrypt"}
    )
    with _decrypt_libs(False):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                PARAM_ATTACH_DECRYPTED_THUMBNAIL: True,
                PARAM_DEFAULT_DEVICE_PASSWORD: "typed-pw",
            },
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"][PARAM_ATTACH_DECRYPTED_THUMBNAIL] == "decrypt_libs_missing"
    assert PARAM_ATTACH_DECRYPTED_THUMBNAIL not in entry.options
    # A rejected submit keeps what was typed rather than clearing the form.
    schema = result.get("data_schema") or result.get("schema")
    assert _schema_suggested(schema, PARAM_DEFAULT_DEVICE_PASSWORD) == "typed-pw"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_alarm_image_decrypt_edits_with_libraries_gone(hass) -> None:
    """Libraries lost after the fact must not lock the passwords on this page."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={
            PARAM_ATTACH_DECRYPTED_THUMBNAIL: True,
            PARAM_DEVICE_PASSWORDS: {"SN1": "old-pw"},
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "alarm_image_decrypt"}
    )
    with _decrypt_libs(False):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {PARAM_ATTACH_DECRYPTED_THUMBNAIL: True, "SN1": "new-pw"},
        )

    _assert_returned_to_menu(result)
    assert entry.options[PARAM_DEVICE_PASSWORDS] == {"SN1": "new-pw"}
    # The menu is where that half-configured state gets reported.
    assert result["description_placeholders"]["decrypt_state"] == (
        "on, but libraries are missing"
    )


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_alarm_image_decrypt_lists_only_tcm_devices(hass) -> None:
    """Non-TCM devices decrypt from their serial, so they get no password field."""
    from custom_components.imou_life.runtime_data import ImouRuntimeData

    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.devices_by_key = {
        "SN-TCM_0": _mock_device("SN-TCM", name="Hallway"),
        "SN-PLAIN_0": _mock_device("SN-PLAIN", name="Garden", ability="WLAN"),
    }
    entry.runtime_data = ImouRuntimeData(coordinator=coordinator)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "alarm_image_decrypt"}
    )
    fields = _schema_field_names(result.get("data_schema") or result.get("schema"))
    assert "Hallway (SN-TCM)" in fields
    assert not any("SN-PLAIN" in str(field) for field in fields)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_alarm_image_decrypt_add_and_delete(hass) -> None:
    """A per-device field adds a password; remove serials deletes it."""
    from custom_components.imou_life.runtime_data import ImouRuntimeData

    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.devices_by_key = {"SN1_0": _mock_device("SN1", name="Hallway")}
    entry.runtime_data = ImouRuntimeData(coordinator=coordinator)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "alarm_image_decrypt"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "alarm_image_decrypt"
    assert result["description_placeholders"]["password_count"] == "0"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"Hallway (SN1)": "pw"},
    )
    _assert_returned_to_menu(result)
    assert entry.options[PARAM_DEVICE_PASSWORDS] == {"SN1": "pw"}

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "alarm_image_decrypt"}
    )
    assert result["description_placeholders"]["password_count"] == "1"
    assert "SN1" in result["description_placeholders"]["configured_serials"]
    schema = result.get("data_schema") or result.get("schema")
    assert _schema_suggested(schema, "Hallway (SN1)") == "pw"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"remove_device_passwords": ["SN1"]},
    )
    _assert_returned_to_menu(result)
    assert entry.options[PARAM_DEVICE_PASSWORDS] == {}


def _schema_suggested(schema: vol.Schema, field: str) -> object:
    """Return suggested_value for a voluptuous schema field."""
    for key in schema.schema:
        if getattr(key, "schema", key) == field:
            return (key.description or {}).get("suggested_value")
    return None


def _schema_field_names(schema: vol.Schema) -> set[object]:
    """Return schema marker names."""
    return {getattr(key, "schema", key) for key in schema.schema}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_alarm_image_password_empty_keeps_stored_value(hass) -> None:
    """Empty per-device fields must not wipe passwords that were already saved."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={PARAM_DEVICE_PASSWORDS: {"SN1": "old-pw"}},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "alarm_image_decrypt"}
    )
    schema = result.get("data_schema") or result.get("schema")
    assert _schema_suggested(schema, "SN1") == "old-pw"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"SN1": ""},
    )
    _assert_returned_to_menu(result)
    assert entry.options[PARAM_DEVICE_PASSWORDS] == {"SN1": "old-pw"}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_alarm_image_default_password_empty_keeps_stored(hass) -> None:
    """Leaving the default password blank must keep the previously saved value."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={PARAM_DEFAULT_DEVICE_PASSWORD: "secret-pw"},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "alarm_image_decrypt"}
    )
    schema = result.get("data_schema") or result.get("schema")
    assert _schema_suggested(schema, PARAM_DEFAULT_DEVICE_PASSWORD) == "secret-pw"
    assert "clear_default_device_password" in _schema_field_names(schema)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {PARAM_DEFAULT_DEVICE_PASSWORD: ""},
    )
    _assert_returned_to_menu(result)
    assert entry.options[PARAM_DEFAULT_DEVICE_PASSWORD] == "secret-pw"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_alarm_image_clear_default_password(hass) -> None:
    """The clear checkbox removes the stored default without touching per-device."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={
            PARAM_DEFAULT_DEVICE_PASSWORD: "secret-pw",
            PARAM_DEVICE_PASSWORDS: {"SN1": "device-pw"},
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "alarm_image_decrypt"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            PARAM_DEFAULT_DEVICE_PASSWORD: "",
            "clear_default_device_password": True,
        },
    )
    _assert_returned_to_menu(result)
    assert entry.options[PARAM_DEFAULT_DEVICE_PASSWORD] == ""
    assert entry.options[PARAM_DEVICE_PASSWORDS] == {"SN1": "device-pw"}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_alarm_image_passwords_saves_multiple_serials(hass) -> None:
    """One submit can set passwords for every TCM device."""
    from custom_components.imou_life.runtime_data import ImouRuntimeData

    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.devices_by_key = {
        "SN-A_0": _mock_device("SN-A"),
        "SN-B_0": _mock_device("SN-B"),
    }
    entry.runtime_data = ImouRuntimeData(coordinator=coordinator)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "alarm_image_decrypt"}
    )
    assert result["type"] is FlowResultType.FORM
    schema = result.get("data_schema") or result.get("schema")
    assert _schema_field_names(schema) >= {"SN-A", "SN-B"}
    assert "extra_device_id" not in _schema_field_names(schema)

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"SN-A": "pw-a", "SN-B": "pw-b"},
    )
    _assert_returned_to_menu(result)
    assert entry.options[PARAM_DEVICE_PASSWORDS] == {"SN-A": "pw-a", "SN-B": "pw-b"}


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
                PARAM_ROTATION_DURATION: 500,
            },
        },
    )
    _assert_returned_to_menu(result)
    assert entry.options[PARAM_UPDATE_INTERVAL] == 120
    assert entry.options[PARAM_ENABLE_EVENT_PUSH] is True
    assert entry.options[PARAM_SELECTED_DEVICES] == ["device_1"]


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
    _assert_returned_to_menu(result)
    assert entry.options[PARAM_UPDATE_INTERVAL] == 180
    assert entry.options[PARAM_LIVE_RESOLUTION] == CONF_SD
    assert entry.options[PARAM_ROTATION_DURATION] == 800


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow_with_devices")
async def test_options_local_recording_saves_shared_settings(hass) -> None:
    """Shared folder and duration save on their own page."""
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
        {"next_step_id": "local_recording"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "local_recording"

    with patch.object(hass.config, "is_allowed_path", return_value=True):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                PARAM_LOCAL_RECORD_PATH: " /media/imou ",
                PARAM_LOCAL_RECORD_DURATION: 45,
            },
        )

    _assert_returned_to_menu(result)
    assert entry.options[PARAM_LOCAL_RECORD_PATH] == "/media/imou"
    assert entry.options[PARAM_LOCAL_RECORD_DURATION] == 45
    assert entry.options[PARAM_ENABLE_EVENT_PUSH] is True
    assert entry.options[PARAM_SELECTED_DEVICES] == ["device_1"]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_local_recording_rejects_path_not_allowlisted(hass) -> None:
    """A folder outside allowlist_external_dirs must not be saved."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": "local_recording"},
    )

    with patch.object(hass.config, "is_allowed_path", return_value=False):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                PARAM_LOCAL_RECORD_PATH: "/media/imou",
                PARAM_LOCAL_RECORD_DURATION: 60,
            },
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"][PARAM_LOCAL_RECORD_PATH] == "record_path_not_allowed"


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
        {"next_step_id": "local_recording"},
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {PARAM_LOCAL_RECORD_PATH: "", PARAM_LOCAL_RECORD_DURATION: 60},
    )

    _assert_returned_to_menu(result)
    assert entry.options[PARAM_LOCAL_RECORD_PATH] == ""
    assert entry.options[PARAM_LOCAL_RECORD_DURATION] == 60


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow_with_devices")
async def test_options_flow_event_push_step_shows_all_sections(hass) -> None:
    """Event push step shows the callback, the subscriptions, and notify targets."""
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
    ]
    notifications = schema.schema[SECTION_EVENT_PUSH_NOTIFICATIONS]
    assert notifications.options.get("collapsed") is False
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
    _assert_returned_to_menu(result)
    assert entry.options[PARAM_ENABLE_EVENT_PUSH] is True
    assert entry.options[PARAM_WEBHOOK_URL] == "https://example.test/hook"
    assert entry.options[PARAM_EVENT_PUSH_TYPES] == ["alarm"]


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
    _assert_returned_to_menu(result)
    assert entry.options[PARAM_NOTIFY_SERVICES] == ["notify.mobile_app_phone"]


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
    _assert_returned_to_menu(result)
    assert entry.options[PARAM_NOTIFY_SERVICES] == [
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
    _assert_returned_to_menu(result)
    assert entry.options[PARAM_WEBHOOK_URL] == "https://example.test/kept"


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow_with_devices")
async def test_options_event_push_preserves_decrypt_settings(hass) -> None:
    """Saving event push must not disturb anything the decrypt page owns."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={
            PARAM_DEVICE_PASSWORDS: {"SN1": "x"},
            PARAM_ATTACH_DECRYPTED_THUMBNAIL: True,
            PARAM_DEFAULT_DEVICE_PASSWORD: "secret-pw",
        },
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
    _assert_returned_to_menu(result)
    assert entry.options[PARAM_DEVICE_PASSWORDS] == {"SN1": "x"}
    assert entry.options[PARAM_ATTACH_DECRYPTED_THUMBNAIL] is True
    assert entry.options[PARAM_DEFAULT_DEVICE_PASSWORD] == "secret-pw"


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
    _assert_returned_to_menu(result)
    assert entry.options[PARAM_ENABLE_EVENT_PUSH] is True
    assert entry.options[PARAM_WEBHOOK_URL] == "https://example.test/hook"
    assert entry.options[PARAM_UPDATE_INTERVAL] == 90
    assert entry.options[PARAM_SELECTED_DEVICES] == ["device_1"]


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
async def test_options_event_push_prefills_generated_callback_url(
    hass,
) -> None:
    """The generated address lands in the field, not just in the description.

    Turning push on with an empty field only ever produced a validation error
    the user had to fix by copying the address out of the text above it.
    """
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
    assert _schema_suggested(schema, PARAM_WEBHOOK_URL) == (
        "http://192.168.1.2:8123/api/webhook/hook-id"
    )


@pytest.mark.parametrize(
    ("url", "public"),
    [
        ("https://ha.example.com/api/webhook/x", True),
        ("http://8.8.8.8:8123/api/webhook/x", True),
        # RFC 5737 documentation space is not routable either.
        ("http://203.0.113.9:8123/api/webhook/x", False),
        # get_url falls back to the internal address without saying so, and
        # these are exactly what that fallback produces.
        ("http://192.168.1.2:8123/api/webhook/x", False),
        ("http://10.0.0.4:8123/api/webhook/x", False),
        ("http://172.16.3.4:8123/api/webhook/x", False),
        ("http://127.0.0.1:8123/api/webhook/x", False),
        ("http://[::1]:8123/api/webhook/x", False),
        ("http://169.254.7.7:8123/api/webhook/x", False),
        ("http://localhost:8123/api/webhook/x", False),
        ("http://homeassistant.local:8123/api/webhook/x", False),
        ("http://ha.lan:8123/api/webhook/x", False),
        # A bare hostname only resolves on the LAN.
        ("http://homeassistant:8123/api/webhook/x", False),
        ("", False),
    ],
)
def test_looks_publicly_reachable(url: str, public: bool) -> None:
    """The Imou cloud POSTs from the internet, so LAN addresses never work."""
    assert config_flow._looks_publicly_reachable(url) is public


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_event_push_flags_a_lan_callback_url(hass) -> None:
    """A LAN callback address must be called out, not silently offered."""
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

    assert "local network" in result["description_placeholders"]["lan_hint"]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_event_push_does_not_flag_a_public_callback_url(hass) -> None:
    """A properly exposed instance must not be nagged."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**USER_INPUT, PARAM_WEBHOOK_ID: "hook-id"},
        options={PARAM_WEBHOOK_URL: "https://ha.example.com/api/webhook/hook-id"},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "event_push"}
    )

    assert result["description_placeholders"]["lan_hint"] == ""


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_event_push_prefers_the_stored_callback_url(hass) -> None:
    """A saved address the user edited must win over the generated one."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**USER_INPUT, PARAM_WEBHOOK_ID: "hook-id"},
        options={PARAM_WEBHOOK_URL: "https://public.example/api/webhook/hook-id"},
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

    schema = result.get("data_schema") or result.get("schema")
    assert _schema_suggested(schema, PARAM_WEBHOOK_URL) == (
        "https://public.example/api/webhook/hook-id"
    )


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
    _assert_returned_to_menu(result)
    assert entry.options[PARAM_SELECTED_DEVICES] == ["device_1"]
    assert entry.options[PARAM_UPDATE_INTERVAL] == 60


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow")
async def test_options_devices_empty_shows_menu(hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": "select_poll_devices"},
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
        {"next_step_id": "select_poll_devices"},
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
        result["flow_id"], {"next_step_id": "select_poll_devices"}
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
        result["flow_id"], {"next_step_id": "select_poll_devices"}
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
            result["flow_id"], {"next_step_id": "select_poll_devices"}
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
            result["flow_id"], {"next_step_id": "select_poll_devices"}
        )
    assert result["step_id"] == "devices_unavailable"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "select_poll_devices"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "select_poll_devices"


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
    _assert_returned_to_menu(result)
    assert entry.options[PARAM_SELECTED_DEVICES] == ["device_1", "SN001"]


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow")
async def test_options_bind_device_success_merges_selection(hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "select_poll_devices"}
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
    _assert_returned_to_menu(result)
    # Binding from Devices records the new device for polling.
    assert entry.options[PARAM_SELECTED_DEVICES] == ["SN001"]


@pytest.mark.usefixtures("enable_custom_integrations", "imou_config_flow")
async def test_options_bind_device_failure_stays_on_form(hass) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"next_step_id": "select_poll_devices"},
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
