"""Config flow for Imou Life."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components import webhook
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import translation
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from pyimouapi.device import ImouDeviceManager
from pyimouapi.exceptions import (
    ConnectFailedException,
    ImouException,
    InvalidAppIdOrSecretException,
    RequestFailedException,
)
from pyimouapi.openapi import ImouOpenApiClient

from . import pic_thumbnail
from .const import (
    API_URL_REGIONS,
    BASE_PUSH_ALWAYS,
    CONF_HD,
    CONF_HTTP,
    CONF_HTTPS,
    CONF_SD,
    DEFAULT_API_URL_REGION,
    DEFAULT_EVENT_PUSH_TYPES,
    DEFAULT_LOCAL_RECORD_DURATION,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    EVENT_PUSH_TYPE_OPTIONS,
    PARAM_API_URL,
    PARAM_APP_ID,
    PARAM_APP_SECRET,
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
    api_url_from_region,
    api_url_region_from_value,
    callback_flags_to_event_push_types,
    event_push_types_to_callback_flags,
)
from .helpers import (
    async_build_device_map,
    notify_service_selector_options,
    parse_notify_services,
)
from .runtime_data import get_runtime_data

_LOGGER = logging.getLogger(__name__)

_ENTRY_NAME = "Imou Life Official"

_GENERAL_OPTION_KEYS = (
    PARAM_ENABLE_POLLING,
    PARAM_UPDATE_INTERVAL,
    PARAM_DOWNLOAD_SNAP_WAIT_TIME,
    PARAM_LIVE_RESOLUTION,
    PARAM_LIVE_PROTOCOL,
    PARAM_ROTATION_DURATION,
)

_EVENT_PUSH_OPTION_KEYS = (
    PARAM_ENABLE_EVENT_PUSH,
    PARAM_WEBHOOK_URL,
    PARAM_EVENT_PUSH_TYPES,
    PARAM_NOTIFY_SERVICES,
    PARAM_ATTACH_DECRYPTED_THUMBNAIL,
    PARAM_DEFAULT_DEVICE_PASSWORD,
    PARAM_LOCAL_RECORD_PATH,
    PARAM_LOCAL_RECORD_DURATION,
)

SECTION_EVENT_PUSH_NOTIFICATIONS = "notifications"
SECTION_EVENT_PUSH_LOCAL_RECORDING = "local_recording"
SECTION_CAMERA_DEFAULTS = "camera_defaults"


def _entry_title(app_id: str) -> str:
    """Build a readable config entry title."""
    suffix = app_id if len(app_id) <= 12 else f"{app_id[:8]}…"
    return f"{_ENTRY_NAME} ({suffix})"


def _config_flow_error_key(exception: ImouException) -> str:
    """Map pyimouapi exceptions to config flow error translation keys."""
    if isinstance(exception, InvalidAppIdOrSecretException):
        return "invalid_auth"
    if isinstance(exception, ConnectFailedException):
        return "cannot_connect"
    if isinstance(exception, RequestFailedException):
        return "request_failed"
    return "unknown"


def _api_error_placeholder(exception: BaseException) -> str:
    """Return the Imou API / exception message for UI placeholders."""
    if isinstance(exception, ImouException) and exception.message:
        return exception.message
    text = str(exception).strip()
    return text or exception.__class__.__name__


_BIND_DEVICE_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): str,
        vol.Optional("code", default=""): str,
    }
)


async def _async_run_bind(
    hass: HomeAssistant,
    api_client: ImouOpenApiClient,
    device_id: str,
    code: str,
) -> dict[str, str]:
    """Bind a device and return the refreshed device map."""
    manager = ImouDeviceManager(api_client)
    await manager.async_bind_device(device_id, code)
    return await async_build_device_map(hass, api_client)


def _selector_option_label(
    hass: HomeAssistant, language: str, selector: str, key: str, fallback: str
) -> str:
    """Load a selector option label for config flow placeholders."""
    translations = translation.async_get_cached_translations(
        hass, language, "selector", DOMAIN
    )
    translation_key = f"component.{DOMAIN}.selector.{selector}.options.{key}"
    return translations.get(translation_key, fallback)


class ImouConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Imou Life."""

    VERSION = 2

    def __init__(self) -> None:
        """Initialize config flow."""
        self._devices_map: dict[str, str] = {}
        self._login_data: dict[str, Any] = {}

    @staticmethod
    def _user_schema(default_region: str = DEFAULT_API_URL_REGION) -> vol.Schema:
        """Schema for App ID / secret / server region."""
        return vol.Schema(
            {
                vol.Required(PARAM_APP_ID): str,
                vol.Required(PARAM_APP_SECRET): str,
                vol.Required(PARAM_API_URL, default=default_region): SelectSelector(
                    SelectSelectorConfig(
                        options=list(API_URL_REGIONS),
                        translation_key="api_url",
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial login step."""
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=self._user_schema(),
            )

        await self.async_set_unique_id(user_input[PARAM_APP_ID])
        self._abort_if_unique_id_configured()

        api_region = api_url_region_from_value(user_input[PARAM_API_URL])
        api_hostname = api_url_from_region(api_region)
        api_client = ImouOpenApiClient(
            user_input[PARAM_APP_ID],
            user_input[PARAM_APP_SECRET],
            api_hostname,
        )
        try:
            try:
                await api_client.async_get_token()
            except ImouException as exception:
                return self.async_show_form(
                    step_id="user",
                    data_schema=self._user_schema(api_region),
                    errors={"base": _config_flow_error_key(exception)},
                    description_placeholders={
                        "error": _api_error_placeholder(exception)
                    },
                )

            self._login_data = {
                PARAM_APP_ID: user_input[PARAM_APP_ID],
                PARAM_APP_SECRET: user_input[PARAM_APP_SECRET],
                PARAM_API_URL: api_hostname,
                PARAM_WEBHOOK_ID: uuid.uuid4().hex,
            }

            try:
                self._devices_map = await async_build_device_map(self.hass, api_client)
            except ConnectFailedException as exception:
                _LOGGER.warning("Failed to fetch device list: %s", exception.message)
                return self.async_abort(
                    reason="cannot_connect",
                    description_placeholders={
                        "error": _api_error_placeholder(exception)
                    },
                )
            except RequestFailedException as exception:
                _LOGGER.warning("Failed to fetch device list: %s", exception.message)
                return self.async_abort(
                    reason="request_failed",
                    description_placeholders={
                        "error": _api_error_placeholder(exception)
                    },
                )
            except ImouException as exception:
                _LOGGER.warning("Failed to fetch device list: %s", exception.message)
                return self.async_abort(
                    reason="request_failed",
                    description_placeholders={
                        "error": _api_error_placeholder(exception)
                    },
                )
            except Exception as exception:
                _LOGGER.exception("Failed to fetch device list")
                return self.async_abort(
                    reason="request_failed",
                    description_placeholders={
                        "error": _api_error_placeholder(exception)
                    },
                )

            if not self._devices_map:
                return await self.async_step_no_devices_menu()

            return await self.async_step_select_devices()
        finally:
            await api_client.async_close()

    async def async_step_no_devices_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer bind or finish when the account has no devices."""
        return self.async_show_menu(
            step_id="no_devices_menu",
            menu_options=["bind_device", "finish_without_devices"],
        )

    async def async_step_finish_without_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the entry for an account that holds no devices yet.

        Store an empty selection so later devices bound in the Imou app are
        not polled until the user picks them under Configure → Devices.
        That matches setup when the account already had cameras.
        """
        return self.async_create_entry(
            title=_entry_title(self._login_data[PARAM_APP_ID]),
            data={**self._login_data, PARAM_SELECTED_DEVICES: []},
        )

    async def async_step_bind_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Bind a device by serial number and optional code."""
        if user_input is None:
            return self.async_show_form(
                step_id="bind_device",
                data_schema=_BIND_DEVICE_SCHEMA,
            )

        api_client = ImouOpenApiClient(
            self._login_data[PARAM_APP_ID],
            self._login_data[PARAM_APP_SECRET],
            self._login_data[PARAM_API_URL],
        )
        try:
            try:
                self._devices_map = await _async_run_bind(
                    self.hass,
                    api_client,
                    user_input["device_id"],
                    user_input.get("code", ""),
                )
            except ImouException as exception:
                return self.async_show_form(
                    step_id="bind_device",
                    data_schema=_BIND_DEVICE_SCHEMA,
                    errors={"base": "bind_failed"},
                    description_placeholders={
                        "error": _api_error_placeholder(exception)
                    },
                )
        finally:
            await api_client.async_close()

        # The bind is only done once the device actually shows up in the
        # account. An account that already holds other devices would otherwise
        # report a bind that did not take as successful.
        if user_input["device_id"] not in self._devices_map:
            return self.async_show_form(
                step_id="bind_device",
                data_schema=_BIND_DEVICE_SCHEMA,
                errors={"base": "bind_device_not_listed"},
            )

        return await self.async_step_select_devices()

    async def async_step_select_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let user select which devices to add."""
        if user_input is not None:
            selected = user_input.get(PARAM_SELECTED_DEVICES, [])
            return self.async_create_entry(
                title=_entry_title(self._login_data[PARAM_APP_ID]),
                data={**self._login_data, PARAM_SELECTED_DEVICES: selected},
            )

        return self.async_show_form(
            step_id="select_devices",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        PARAM_SELECTED_DEVICES,
                        default=list(self._devices_map.keys()),
                    ): cv.multi_select(self._devices_map),
                }
            ),
            description_placeholders={
                "device_count": str(len(self._devices_map)),
            },
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauthentication."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauthentication with a new App Secret."""
        reauth_entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        error_detail = ""

        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm",
                data_schema=vol.Schema({vol.Required(PARAM_APP_SECRET): str}),
                description_placeholders={
                    "app_id": reauth_entry.data[PARAM_APP_ID],
                    "error": "",
                },
            )

        api_client = ImouOpenApiClient(
            reauth_entry.data[PARAM_APP_ID],
            user_input[PARAM_APP_SECRET],
            reauth_entry.data[PARAM_API_URL],
        )
        try:
            await api_client.async_get_token()
        except InvalidAppIdOrSecretException as exception:
            errors["base"] = "invalid_auth"
            error_detail = _api_error_placeholder(exception)
        except ImouException as exception:
            errors["base"] = _config_flow_error_key(exception)
            error_detail = _api_error_placeholder(exception)
        else:
            return self.async_update_reload_and_abort(
                reauth_entry,
                data={
                    **reauth_entry.data,
                    PARAM_APP_SECRET: user_input[PARAM_APP_SECRET],
                },
            )
        finally:
            await api_client.async_close()

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(PARAM_APP_SECRET): str}),
            errors=errors,
            description_placeholders={
                "app_id": reauth_entry.data[PARAM_APP_ID],
                "error": error_detail,
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OptionsFlow:
        """Return the options flow."""
        return ImouOptionsFlow()


class ImouOptionsFlow(OptionsFlow):
    """Imou Life options."""

    def __init__(self) -> None:
        """Initialize options flow."""
        self._devices_map: dict[str, str] = {}
        self._devices_error: str = ""
        self._callback_error: str = ""
        self._password_device_id: str = ""

    @staticmethod
    def _suggested_option_subset(
        options: Mapping[str, Any], keys: tuple[str, ...]
    ) -> dict[str, Any]:
        """Return suggested values for a subset of option keys."""
        return {key: options[key] for key in keys if key in options}

    def _merge_options(self, **updates: Any) -> dict[str, Any]:
        """Replace options wholesale while keeping untouched keys."""
        return {**dict(self.config_entry.options), **updates}

    def _general_settings_schema(self) -> vol.Schema:
        """Build the general options form."""
        return vol.Schema(
            {
                vol.Optional(PARAM_ENABLE_POLLING, default=True): bool,
                vol.Required(
                    PARAM_UPDATE_INTERVAL, default=DEFAULT_UPDATE_INTERVAL
                ): vol.All(vol.Coerce(int), vol.Range(min=30, max=900)),
                vol.Optional(SECTION_CAMERA_DEFAULTS): section(
                    vol.Schema(
                        {
                            vol.Required(
                                PARAM_DOWNLOAD_SNAP_WAIT_TIME, default=3
                            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=9)),
                            vol.Required(
                                PARAM_LIVE_RESOLUTION, default=CONF_HD
                            ): vol.In([CONF_HD, CONF_SD]),
                            vol.Required(
                                PARAM_LIVE_PROTOCOL, default=CONF_HTTPS
                            ): vol.In([CONF_HTTPS, CONF_HTTP]),
                            vol.Required(PARAM_ROTATION_DURATION, default=500): vol.All(
                                vol.Coerce(int), vol.Range(min=100, max=10000)
                            ),
                        }
                    ),
                    {"collapsed": True},
                ),
            }
        )

    def _nested_general_suggestions(self, options: Mapping[str, Any]) -> dict[str, Any]:
        """Map stored general options into the section-based form."""
        flat = self._suggested_option_subset(options, _GENERAL_OPTION_KEYS)
        return {
            PARAM_ENABLE_POLLING: bool(flat.get(PARAM_ENABLE_POLLING, True)),
            PARAM_UPDATE_INTERVAL: flat.get(
                PARAM_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
            ),
            SECTION_CAMERA_DEFAULTS: {
                PARAM_DOWNLOAD_SNAP_WAIT_TIME: flat.get(
                    PARAM_DOWNLOAD_SNAP_WAIT_TIME, 3
                ),
                PARAM_LIVE_RESOLUTION: flat.get(PARAM_LIVE_RESOLUTION, CONF_HD),
                PARAM_LIVE_PROTOCOL: flat.get(PARAM_LIVE_PROTOCOL, CONF_HTTPS),
                PARAM_ROTATION_DURATION: flat.get(PARAM_ROTATION_DURATION, 500),
            },
        }

    @staticmethod
    def _flatten_general_input(
        user_input: dict[str, Any], stored_options: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Flatten section-based general input back to stored option keys."""
        flat: dict[str, Any] = {
            PARAM_ENABLE_POLLING: bool(user_input.get(PARAM_ENABLE_POLLING, True)),
            PARAM_UPDATE_INTERVAL: user_input.get(
                PARAM_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
            ),
        }
        if SECTION_CAMERA_DEFAULTS in user_input:
            flat.update(user_input[SECTION_CAMERA_DEFAULTS])
        else:
            for key, default in (
                (PARAM_DOWNLOAD_SNAP_WAIT_TIME, 3),
                (PARAM_LIVE_RESOLUTION, CONF_HD),
                (PARAM_LIVE_PROTOCOL, CONF_HTTPS),
                (PARAM_ROTATION_DURATION, 500),
            ):
                flat[key] = stored_options.get(key, default)
        return flat

    def _generated_webhook_url(self) -> str:
        """Return the webhook URL Home Assistant can generate, or empty."""
        webhook_id = self.config_entry.data.get(PARAM_WEBHOOK_ID, "")
        if not webhook_id:
            return ""
        try:
            return webhook.async_generate_url(self.hass, webhook_id)
        except Exception:
            return ""

    def _event_push_webhook_placeholders(self) -> dict[str, str]:
        """Return webhook reference values for the step description."""
        language = self.hass.config.language
        webhook_id = self.config_entry.data.get(PARAM_WEBHOOK_ID, "")
        suggested_webhook_url = self._generated_webhook_url()
        not_generated = _selector_option_label(
            self.hass, language, "webhook_placeholder", "not_generated", "Not generated"
        )
        return {
            # Keep webhook_id for cached frontend strings that still reference it.
            "webhook_id": webhook_id or not_generated,
            "suggested_url": suggested_webhook_url or not_generated,
        }

    def _nested_event_push_suggestions(
        self, options: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Map flat stored options to a section-based suggested values dict."""
        flat = self._suggested_option_subset(options, _EVENT_PUSH_OPTION_KEYS)
        enable_push = bool(flat.get(PARAM_ENABLE_EVENT_PUSH, False))
        event_push_types = flat.get(PARAM_EVENT_PUSH_TYPES, DEFAULT_EVENT_PUSH_TYPES)
        if isinstance(event_push_types, list):
            event_push_types = callback_flags_to_event_push_types(event_push_types)

        stored_webhook_url = str(flat.get(PARAM_WEBHOOK_URL) or "").strip()

        nested: dict[str, Any] = {
            PARAM_ENABLE_EVENT_PUSH: enable_push,
            PARAM_EVENT_PUSH_TYPES: event_push_types,
            SECTION_EVENT_PUSH_NOTIFICATIONS: {
                PARAM_NOTIFY_SERVICES: parse_notify_services(
                    flat.get(PARAM_NOTIFY_SERVICES)
                ),
                PARAM_ATTACH_DECRYPTED_THUMBNAIL: bool(
                    flat.get(PARAM_ATTACH_DECRYPTED_THUMBNAIL, False)
                ),
                PARAM_DEFAULT_DEVICE_PASSWORD: str(
                    flat.get(PARAM_DEFAULT_DEVICE_PASSWORD) or ""
                ),
            },
            SECTION_EVENT_PUSH_LOCAL_RECORDING: {
                PARAM_LOCAL_RECORD_PATH: flat.get(PARAM_LOCAL_RECORD_PATH, ""),
                PARAM_LOCAL_RECORD_DURATION: flat.get(
                    PARAM_LOCAL_RECORD_DURATION, DEFAULT_LOCAL_RECORD_DURATION
                ),
            },
        }
        if stored_webhook_url:
            nested[PARAM_WEBHOOK_URL] = stored_webhook_url
        return nested

    @staticmethod
    def _flatten_event_push_input(
        user_input: dict[str, Any], stored_options: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Flatten section-based event push input back to stored option keys."""
        flat: dict[str, Any] = {
            PARAM_ENABLE_EVENT_PUSH: user_input[PARAM_ENABLE_EVENT_PUSH],
            PARAM_WEBHOOK_URL: str(
                user_input.get(PARAM_WEBHOOK_URL, stored_options.get(PARAM_WEBHOOK_URL))
                or ""
            ).strip(),
            PARAM_EVENT_PUSH_TYPES: user_input.get(
                PARAM_EVENT_PUSH_TYPES,
                stored_options.get(PARAM_EVENT_PUSH_TYPES, DEFAULT_EVENT_PUSH_TYPES),
            ),
        }
        if SECTION_EVENT_PUSH_NOTIFICATIONS in user_input:
            notifications = dict(user_input[SECTION_EVENT_PUSH_NOTIFICATIONS])
            notifications[PARAM_NOTIFY_SERVICES] = parse_notify_services(
                notifications.get(PARAM_NOTIFY_SERVICES)
            )
            notifications[PARAM_ATTACH_DECRYPTED_THUMBNAIL] = bool(
                notifications.get(PARAM_ATTACH_DECRYPTED_THUMBNAIL, False)
            )
            notifications[PARAM_DEFAULT_DEVICE_PASSWORD] = str(
                notifications.get(PARAM_DEFAULT_DEVICE_PASSWORD) or ""
            )
            flat.update(notifications)
        else:
            flat[PARAM_NOTIFY_SERVICES] = parse_notify_services(
                stored_options.get(PARAM_NOTIFY_SERVICES)
            )
            flat[PARAM_ATTACH_DECRYPTED_THUMBNAIL] = bool(
                stored_options.get(PARAM_ATTACH_DECRYPTED_THUMBNAIL, False)
            )
            flat[PARAM_DEFAULT_DEVICE_PASSWORD] = str(
                stored_options.get(PARAM_DEFAULT_DEVICE_PASSWORD) or ""
            )
        if SECTION_EVENT_PUSH_LOCAL_RECORDING in user_input:
            recording = dict(user_input[SECTION_EVENT_PUSH_LOCAL_RECORDING])
            recording[PARAM_LOCAL_RECORD_PATH] = str(
                recording.get(PARAM_LOCAL_RECORD_PATH) or ""
            ).strip()
            flat.update(recording)
        else:
            flat[PARAM_LOCAL_RECORD_PATH] = str(
                stored_options.get(PARAM_LOCAL_RECORD_PATH) or ""
            ).strip()
            flat[PARAM_LOCAL_RECORD_DURATION] = stored_options.get(
                PARAM_LOCAL_RECORD_DURATION, DEFAULT_LOCAL_RECORD_DURATION
            )
        flat[PARAM_DEVICE_PASSWORDS] = stored_options.get(PARAM_DEVICE_PASSWORDS, {})
        return flat

    def _event_push_schema(self) -> vol.Schema:
        """Build the event push options form."""
        stored_notify = parse_notify_services(
            self.config_entry.options.get(PARAM_NOTIFY_SERVICES)
        )
        notify_options = notify_service_selector_options(self.hass, stored_notify)
        return vol.Schema(
            {
                vol.Required(PARAM_ENABLE_EVENT_PUSH, default=False): bool,
                vol.Optional(PARAM_WEBHOOK_URL, default=""): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.URL)
                ),
                vol.Required(
                    PARAM_EVENT_PUSH_TYPES,
                    default=DEFAULT_EVENT_PUSH_TYPES,
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=list(EVENT_PUSH_TYPE_OPTIONS),
                        multiple=True,
                        translation_key="event_push_type",
                    )
                ),
                vol.Required(SECTION_EVENT_PUSH_NOTIFICATIONS): section(
                    vol.Schema(
                        {
                            vol.Optional(
                                PARAM_NOTIFY_SERVICES, default=[]
                            ): SelectSelector(
                                SelectSelectorConfig(
                                    options=notify_options,
                                    multiple=True,
                                    mode=SelectSelectorMode.DROPDOWN,
                                )
                            ),
                            vol.Optional(
                                PARAM_ATTACH_DECRYPTED_THUMBNAIL, default=False
                            ): bool,
                            vol.Optional(
                                PARAM_DEFAULT_DEVICE_PASSWORD, default=""
                            ): TextSelector(
                                TextSelectorConfig(type=TextSelectorType.PASSWORD)
                            ),
                        }
                    ),
                    {"collapsed": False},
                ),
                vol.Required(SECTION_EVENT_PUSH_LOCAL_RECORDING): section(
                    vol.Schema(
                        {
                            vol.Optional(PARAM_LOCAL_RECORD_PATH, default=""): str,
                            vol.Required(
                                PARAM_LOCAL_RECORD_DURATION,
                                default=DEFAULT_LOCAL_RECORD_DURATION,
                            ): vol.All(vol.Coerce(int), vol.Range(min=15, max=180)),
                        }
                    ),
                    {"collapsed": False},
                ),
            }
        )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which options section to edit."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "general_settings",
                "event_push",
                "alarm_image_passwords",
                "devices",
            ],
        )

    def _ui_language(self) -> str:
        language = getattr(self.hass.config, "language", None)
        if isinstance(language, str) and language:
            return language
        return "en"

    def _native_lib_placeholders(self) -> dict[str, str]:
        """Return decrypt-library path, file count, and platform support."""
        return {
            "native_dir": str(pic_thumbnail.native_lib_dir(self.hass)),
            "native_libs_found": str(pic_thumbnail.native_libs_found(self.hass)),
            "native_platform": pic_thumbnail.native_platform_label(),
            "native_support": pic_thumbnail.native_support_status(self._ui_language()),
        }

    def _device_passwords(self) -> dict[str, str]:
        """Return the stored per-serial alarm image passwords."""
        stored = self.config_entry.options.get(PARAM_DEVICE_PASSWORDS, {})
        return dict(stored) if isinstance(stored, dict) else {}

    def _add_device_password_schema(self) -> vol.Schema:
        """Build the serial-number picker."""
        runtime = get_runtime_data(self.config_entry)
        if runtime is not None:
            device_ids = sorted(
                {
                    device.device_id
                    for device in runtime.coordinator.devices_by_key.values()
                    if device.device_id
                }
            )
            device_field: SelectSelector | TextSelector = SelectSelector(
                SelectSelectorConfig(options=device_ids)
            )
        else:
            device_field = TextSelector()
        return vol.Schema({vol.Required("device_id"): device_field})

    def _edit_device_password_schema(self, stored_password: str) -> vol.Schema:
        """Build the password form, suggesting the value already stored for this SN."""
        return self.add_suggested_values_to_schema(
            vol.Schema(
                {
                    vol.Optional("password", default=""): TextSelector(
                        TextSelectorConfig(
                            type=TextSelectorType.PASSWORD,
                            autocomplete="new-password",
                        )
                    ),
                }
            ),
            {"password": stored_password},
        )

    async def async_step_alarm_image_passwords(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage per-serial alarm image passwords."""
        return self.async_show_menu(
            step_id="alarm_image_passwords",
            menu_options=["add_device_password", "finish_passwords"],
            description_placeholders={
                "password_count": str(len(self._device_passwords())),
                **self._native_lib_placeholders(),
            },
        )

    async def async_step_add_device_password(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the serial whose alarm image password to edit."""
        if user_input is not None:
            self._password_device_id = str(user_input["device_id"]).strip()
            if self._password_device_id:
                return await self.async_step_edit_device_password()

        return self.async_show_form(
            step_id="add_device_password",
            data_schema=self._add_device_password_schema(),
        )

    async def async_step_edit_device_password(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add, update, or remove the password for the selected serial."""
        device_id = self._password_device_id
        if not device_id:
            return await self.async_step_add_device_password()
        if user_input is not None:
            password = str(user_input.get("password") or "")
            passwords = self._device_passwords()
            if not password:
                passwords.pop(device_id, None)
            else:
                passwords[device_id] = password
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                options={
                    **dict(self.config_entry.options),
                    PARAM_DEVICE_PASSWORDS: passwords,
                },
            )
            return await self.async_step_alarm_image_passwords()

        stored = self._device_passwords().get(device_id, "")
        return self.async_show_form(
            step_id="edit_device_password",
            data_schema=self._edit_device_password_schema(stored),
            description_placeholders={"device_id": device_id},
        )

    async def async_step_finish_passwords(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Close the password loop and save options."""
        return self.async_create_entry(data=dict(self.config_entry.options))

    async def async_step_general_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage options — polling, camera, and PTZ settings."""
        if user_input is not None:
            return self.async_create_entry(
                data=self._merge_options(
                    **self._flatten_general_input(user_input, self.config_entry.options)
                )
            )

        return self.async_show_form(
            step_id="general_settings",
            data_schema=self.add_suggested_values_to_schema(
                self._general_settings_schema(),
                self._nested_general_suggestions(self.config_entry.options),
            ),
        )

    def _local_record_path_error(self, folder: str) -> str | None:
        """Return an options error key when the save folder is not allowlisted."""
        if not folder:
            return None
        probe = str(Path(folder) / "imou_record.mp4")
        if self.hass.config.is_allowed_path(probe):
            return None
        return "record_path_not_allowed"

    async def async_step_event_push(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage options — event push, notifications, and local recording."""
        stored = dict(self.config_entry.options)
        errors: dict[str, Any] = {}
        error_detail = ""
        suggested_source: Mapping[str, Any] = stored
        if user_input is not None:
            flat = self._flatten_event_push_input(user_input, stored)
            path_error = self._local_record_path_error(
                str(flat.get(PARAM_LOCAL_RECORD_PATH) or "")
            )
            if path_error:
                errors[SECTION_EVENT_PUSH_LOCAL_RECORDING] = {
                    PARAM_LOCAL_RECORD_PATH: path_error
                }
            if not errors and flat[PARAM_ENABLE_EVENT_PUSH]:
                callback_url = str(flat.get(PARAM_WEBHOOK_URL) or "").strip()
                if not callback_url:
                    errors[PARAM_WEBHOOK_URL] = "callback_url_missing"
                else:
                    error_key = await self._async_register_message_callback(
                        callback_url,
                        list(flat.get(PARAM_EVENT_PUSH_TYPES) or []),
                    )
                    if error_key:
                        errors["base"] = error_key
                        error_detail = self._callback_error
            if not errors:
                return self.async_create_entry(data=self._merge_options(**flat))
            suggested_source = {**stored, **flat}

        placeholders = self._event_push_webhook_placeholders()
        if error_detail:
            placeholders["error"] = error_detail
        return self.async_show_form(
            step_id="event_push",
            data_schema=self.add_suggested_values_to_schema(
                self._event_push_schema(),
                self._nested_event_push_suggestions(suggested_source),
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def _async_register_message_callback(
        self, callback_url: str, event_push_types: list[str]
    ) -> str | None:
        """Register the Imou callback. Return an options error key, or None."""
        runtime = get_runtime_data(self.config_entry)
        client = runtime.client if runtime is not None else None
        owned = False
        if client is None:
            client = ImouOpenApiClient(
                self.config_entry.data[PARAM_APP_ID],
                self.config_entry.data[PARAM_APP_SECRET],
                self.config_entry.data[PARAM_API_URL],
            )
            owned = True
        try:
            try:
                await client.async_set_message_callback(
                    status="on",
                    callback_url=callback_url,
                    callback_flag=event_push_types_to_callback_flags(event_push_types)
                    or None,
                    base_push=BASE_PUSH_ALWAYS,
                )
            except ImouException as exception:
                self._callback_error = _api_error_placeholder(exception)
                return "callback_failed"
            except Exception as exception:
                _LOGGER.exception("Failed to register Imou message callback")
                self._callback_error = _api_error_placeholder(exception)
                return "callback_failed"
        finally:
            if owned:
                await client.async_close()
        return None

    def _stored_selected_devices(self) -> list[str] | None:
        """Return the stored whitelist, or None when there is no filter."""
        if PARAM_SELECTED_DEVICES in self.config_entry.options:
            return list(self.config_entry.options[PARAM_SELECTED_DEVICES])
        if PARAM_SELECTED_DEVICES in self.config_entry.data:
            return list(self.config_entry.data[PARAM_SELECTED_DEVICES])
        return None

    def _options_current_selected(self) -> list[str]:
        """Return the current selected_devices list for options binding/selection."""
        stored = self._stored_selected_devices()
        if stored is not None:
            return stored
        if self._devices_map:
            return list(self._devices_map.keys())
        return []

    def _clear_selected_from_entry_data(self) -> None:
        """Drop a setup-time whitelist so options cannot fall back to it."""
        if PARAM_SELECTED_DEVICES not in self.config_entry.data:
            return
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={
                key: value
                for key, value in self.config_entry.data.items()
                if key != PARAM_SELECTED_DEVICES
            },
        )

    def _create_selected_entry(self, selected: list[str]) -> ConfigFlowResult:
        """Write the poll list and close the options dialog."""
        self._clear_selected_from_entry_data()
        return self.async_create_entry(
            data=self._merge_options(**{PARAM_SELECTED_DEVICES: selected})
        )

    def _devices_schema(self) -> vol.Schema:
        """Build the poll-list form."""
        current = [
            device_id
            for device_id in self._options_current_selected()
            if device_id in self._devices_map
        ]
        return vol.Schema(
            {
                vol.Required(
                    PARAM_SELECTED_DEVICES,
                    default=current,
                ): cv.multi_select(self._devices_map),
            }
        )

    def _show_devices_form(
        self,
        errors: dict[str, str] | None = None,
        error_detail: str = "",
    ) -> ConfigFlowResult:
        """Show the poll-list form with a Submit button that saves."""
        placeholders = {"device_count": str(len(self._devices_map))}
        if error_detail:
            placeholders["error"] = error_detail
        return self.async_show_form(
            step_id="select_poll_devices",
            data_schema=self._devices_schema(),
            description_placeholders=placeholders,
            errors=errors or {},
        )

    def _show_devices_menu(self) -> ConfigFlowResult:
        """Choose poll list vs bind when the account has devices."""
        return self.async_show_menu(
            step_id="devices_menu",
            menu_options=["select_poll_devices", "bind_device"],
        )

    async def _async_bind_device_id(self, device_id: str, code: str) -> str | None:
        """Bind a serial and refresh the account map. Return an error key or None."""
        api_client = ImouOpenApiClient(
            self.config_entry.data[PARAM_APP_ID],
            self.config_entry.data[PARAM_APP_SECRET],
            self.config_entry.data[PARAM_API_URL],
        )
        try:
            try:
                self._devices_map = await _async_run_bind(
                    self.hass, api_client, device_id, code
                )
            except ImouException as exception:
                self._devices_error = _api_error_placeholder(exception)
                return "bind_failed"
        finally:
            await api_client.async_close()
        if device_id not in self._devices_map:
            return "bind_device_not_listed"
        return None

    def _merge_bound_device(self, device_id: str, selected: list[str]) -> list[str]:
        """Keep still-listed ids and append the newly bound serial."""
        pending = [item for item in selected if item in self._devices_map]
        if device_id not in pending:
            pending.append(device_id)
        return pending

    async def async_step_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Load the account device list, then offer poll or bind."""
        errors: dict[str, str] = {}
        error_detail = ""
        try:
            api_client = ImouOpenApiClient(
                self.config_entry.data[PARAM_APP_ID],
                self.config_entry.data[PARAM_APP_SECRET],
                self.config_entry.data[PARAM_API_URL],
            )
            try:
                self._devices_map = await async_build_device_map(self.hass, api_client)
            finally:
                await api_client.async_close()
        except ImouException as exception:
            _LOGGER.warning(
                "Failed to fetch device list for options: %s", exception.message
            )
            errors["base"] = "request_failed"
            error_detail = _api_error_placeholder(exception)
        except Exception as exception:
            _LOGGER.exception("Failed to fetch device list for options")
            errors["base"] = "request_failed"
            error_detail = _api_error_placeholder(exception)

        if errors:
            self._devices_error = error_detail
            return await self.async_step_devices_unavailable()

        if not self._devices_map:
            return await self.async_step_no_devices_menu()

        return self._show_devices_menu()

    async def async_step_devices_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose whether to edit the poll list or bind a device."""
        return self._show_devices_menu()

    async def async_step_select_poll_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick which account devices to poll. Submit saves and closes."""
        if user_input is not None and PARAM_SELECTED_DEVICES in user_input:
            selected = list(user_input.get(PARAM_SELECTED_DEVICES, []))
            return self._create_selected_entry(selected)
        if not self._devices_map:
            return await self.async_step_devices()
        return self._show_devices_form()

    async def async_step_devices_unavailable(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer retry or closing without changing devices when listing fails."""
        return self.async_show_menu(
            step_id="devices_unavailable",
            menu_options=["devices", "save_without_devices"],
            description_placeholders={"error": self._devices_error},
        )

    async def async_step_save_without_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Close without changing the device selection."""
        return self.async_create_entry(data=self._merge_options())

    async def async_step_no_devices_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer bind or save options when the account has no devices."""
        return self.async_show_menu(
            step_id="no_devices_menu",
            menu_options=["bind_device", "finish_without_bind"],
        )

    async def async_step_finish_without_bind(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Record poll-nothing when the account list is empty, then save.

        The account was listed successfully and holds nothing, so any previous
        selected_devices list is stale (e.g. devices deleted in the Imou app).
        Write an empty list: poll nothing until the user picks devices under
        Devices. Setup may have stored the list in entry.data; clear
        that too or get_selected_device_ids would fall back to it. Cloud
        failures that cannot list the account use save_without_devices and
        keep the selection.
        """
        return self._create_selected_entry([])

    async def async_step_bind_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Bind a device from Devices and save the merged selection."""
        if user_input is None:
            return self.async_show_form(
                step_id="bind_device",
                data_schema=_BIND_DEVICE_SCHEMA,
            )

        device_id = user_input["device_id"]
        error_key = await self._async_bind_device_id(
            device_id, user_input.get("code", "")
        )
        if error_key is not None:
            return self.async_show_form(
                step_id="bind_device",
                data_schema=_BIND_DEVICE_SCHEMA,
                errors={"base": error_key},
                description_placeholders={"error": self._devices_error},
            )

        selected = self._merge_bound_device(device_id, self._options_current_selected())
        return self._create_selected_entry(selected)
