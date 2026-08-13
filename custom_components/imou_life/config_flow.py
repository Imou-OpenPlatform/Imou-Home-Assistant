"""Config flow for Imou Life."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
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

from .const import (
    API_URL_REGIONS,
    CONF_HD,
    CONF_HTTP,
    CONF_HTTPS,
    CONF_SD,
    DEFAULT_API_URL_REGION,
    DEFAULT_EVENT_PUSH_TYPES,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    EVENT_PUSH_TYPE_OPTIONS,
    PARAM_API_URL,
    PARAM_APP_ID,
    PARAM_APP_SECRET,
    PARAM_DOWNLOAD_SNAP_WAIT_TIME,
    PARAM_ENABLE_EVENT_PUSH,
    PARAM_ENABLE_POLLING,
    PARAM_EVENT_PUSH_TYPES,
    PARAM_LIVE_PROTOCOL,
    PARAM_LIVE_RESOLUTION,
    PARAM_NOTIFY_SERVICES,
    PARAM_ROTATION_DURATION,
    PARAM_SELECTED_DEVICES,
    PARAM_UPDATE_INTERVAL,
    PARAM_WEBHOOK_ID,
    PARAM_WEBHOOK_URL,
    api_url_from_region,
    api_url_region_from_value,
    callback_flags_to_event_push_types,
)
from .helpers import async_build_device_map

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
)

SECTION_EVENT_PUSH_CALLBACK = "callback"
SECTION_EVENT_PUSH_SUBSCRIPTIONS = "subscriptions"
SECTION_EVENT_PUSH_NOTIFICATIONS = "notifications"
SECTION_BIND_DEVICE = "bind_new_device"


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
        not polled until the user picks them under Configure → Manage devices.
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

    @staticmethod
    def _suggested_option_subset(
        options: Mapping[str, Any], keys: tuple[str, ...]
    ) -> dict[str, Any]:
        """Return suggested values for a subset of option keys."""
        return {key: options[key] for key in keys if key in options}

    def _merge_options(self, **updates: Any) -> dict[str, Any]:
        """Replace options wholesale while keeping untouched keys."""
        return {**dict(self.config_entry.options), **updates}

    @staticmethod
    def _general_settings_schema() -> vol.Schema:
        """Build the general options form."""
        return vol.Schema(
            {
                vol.Optional(PARAM_ENABLE_POLLING, default=True): bool,
                vol.Required(
                    PARAM_UPDATE_INTERVAL, default=DEFAULT_UPDATE_INTERVAL
                ): vol.All(vol.Coerce(int), vol.Range(min=30, max=900)),
                vol.Required(PARAM_DOWNLOAD_SNAP_WAIT_TIME, default=3): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=9)
                ),
                vol.Required(PARAM_LIVE_RESOLUTION, default=CONF_HD): vol.In(
                    [CONF_HD, CONF_SD]
                ),
                vol.Required(PARAM_LIVE_PROTOCOL, default=CONF_HTTPS): vol.In(
                    [CONF_HTTPS, CONF_HTTP]
                ),
                vol.Required(PARAM_ROTATION_DURATION, default=500): vol.All(
                    vol.Coerce(int), vol.Range(min=100, max=10000)
                ),
            }
        )

    def _event_push_webhook_placeholders(self) -> dict[str, str]:
        """Return webhook reference values for the step description."""
        language = self.hass.config.language
        webhook_id = self.config_entry.data.get(PARAM_WEBHOOK_ID, "")
        suggested_webhook_url = ""
        if webhook_id:
            try:
                suggested_webhook_url = webhook.async_generate_url(
                    self.hass, webhook_id
                )
            except Exception:
                suggested_webhook_url = f"/api/webhook/{webhook_id}"

        not_generated = _selector_option_label(
            self.hass, language, "webhook_placeholder", "not_generated", "Not generated"
        )
        return {
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

        nested: dict[str, Any] = {
            PARAM_ENABLE_EVENT_PUSH: enable_push,
            SECTION_EVENT_PUSH_CALLBACK: {
                PARAM_WEBHOOK_URL: flat.get(PARAM_WEBHOOK_URL, ""),
            },
            SECTION_EVENT_PUSH_SUBSCRIPTIONS: {
                PARAM_EVENT_PUSH_TYPES: event_push_types,
            },
            SECTION_EVENT_PUSH_NOTIFICATIONS: {
                PARAM_NOTIFY_SERVICES: flat.get(PARAM_NOTIFY_SERVICES, ""),
            },
        }
        return nested

    @staticmethod
    def _flatten_event_push_input(
        user_input: dict[str, Any], stored_options: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Flatten section-based event push input back to stored option keys."""
        callback = user_input.get(SECTION_EVENT_PUSH_CALLBACK, {})
        flat: dict[str, Any] = {
            PARAM_ENABLE_EVENT_PUSH: user_input[PARAM_ENABLE_EVENT_PUSH],
            PARAM_WEBHOOK_URL: callback.get(PARAM_WEBHOOK_URL, ""),
        }
        if SECTION_EVENT_PUSH_SUBSCRIPTIONS in user_input:
            flat.update(user_input[SECTION_EVENT_PUSH_SUBSCRIPTIONS])
        else:
            flat[PARAM_EVENT_PUSH_TYPES] = stored_options.get(
                PARAM_EVENT_PUSH_TYPES, DEFAULT_EVENT_PUSH_TYPES
            )
        if SECTION_EVENT_PUSH_NOTIFICATIONS in user_input:
            flat.update(user_input[SECTION_EVENT_PUSH_NOTIFICATIONS])
        else:
            flat[PARAM_NOTIFY_SERVICES] = stored_options.get(PARAM_NOTIFY_SERVICES, "")
        return flat

    @staticmethod
    def _event_push_schema() -> vol.Schema:
        """Build the event push options form."""
        return vol.Schema(
            {
                vol.Required(PARAM_ENABLE_EVENT_PUSH, default=False): bool,
                vol.Required(SECTION_EVENT_PUSH_CALLBACK): section(
                    vol.Schema(
                        {
                            vol.Optional(PARAM_WEBHOOK_URL, default=""): TextSelector(
                                TextSelectorConfig(type=TextSelectorType.URL)
                            ),
                        }
                    ),
                ),
                vol.Required(SECTION_EVENT_PUSH_SUBSCRIPTIONS): section(
                    vol.Schema(
                        {
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
                        }
                    ),
                ),
                vol.Optional(SECTION_EVENT_PUSH_NOTIFICATIONS): section(
                    vol.Schema(
                        {
                            vol.Optional(PARAM_NOTIFY_SERVICES, default=""): str,
                        }
                    ),
                    {"collapsed": True},
                ),
            }
        )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which options section to edit."""
        return self.async_show_menu(
            step_id="init",
            menu_options=["general_settings", "event_push", "devices"],
        )

    async def async_step_general_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage options — polling, camera, and PTZ settings."""
        if user_input is not None:
            return self.async_create_entry(data=self._merge_options(**user_input))

        suggested = self._suggested_option_subset(
            self.config_entry.options, _GENERAL_OPTION_KEYS
        )
        return self.async_show_form(
            step_id="general_settings",
            data_schema=self.add_suggested_values_to_schema(
                self._general_settings_schema(),
                suggested,
            ),
        )

    async def async_step_event_push(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage options — event push and alarm notifications."""
        stored = dict(self.config_entry.options)
        if user_input is not None:
            flat = self._flatten_event_push_input(user_input, stored)
            return self.async_create_entry(data=self._merge_options(**flat))

        suggested_options = self._nested_event_push_suggestions(stored)

        return self.async_show_form(
            step_id="event_push",
            data_schema=self.add_suggested_values_to_schema(
                self._event_push_schema(),
                suggested_options,
            ),
            description_placeholders=self._event_push_webhook_placeholders(),
        )

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
        """Build the manage-devices form: poll list plus optional bind."""
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
                vol.Optional(SECTION_BIND_DEVICE): section(
                    vol.Schema(
                        {
                            vol.Optional("device_id", default=""): str,
                            vol.Optional("code", default=""): str,
                        }
                    ),
                    {"collapsed": True},
                ),
            }
        )

    def _show_devices_form(
        self,
        errors: dict[str, str] | None = None,
        error_detail: str = "",
    ) -> ConfigFlowResult:
        """Show the manage-devices form with a Submit button that saves."""
        placeholders = {"device_count": str(len(self._devices_map))}
        if error_detail:
            placeholders["error"] = error_detail
        return self.async_show_form(
            step_id="devices",
            data_schema=self._devices_schema(),
            description_placeholders=placeholders,
            errors=errors or {},
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
        """Pick which account devices to poll. Submit saves and closes."""
        if user_input is not None and PARAM_SELECTED_DEVICES in user_input:
            selected = list(user_input.get(PARAM_SELECTED_DEVICES, []))
            bind = user_input.get(SECTION_BIND_DEVICE) or {}
            device_id = str(bind.get("device_id") or "").strip()
            if device_id:
                error_key = await self._async_bind_device_id(
                    device_id, bind.get("code", "")
                )
                if error_key is not None:
                    return self._show_devices_form(
                        errors={"base": error_key},
                        error_detail=self._devices_error,
                    )
                selected = self._merge_bound_device(device_id, selected)
            return self._create_selected_entry(selected)

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
        Manage devices. Setup may have stored the list in entry.data; clear
        that too or get_selected_device_ids would fall back to it. Cloud
        failures that cannot list the account use save_without_devices and
        keep the selection.
        """
        return self._create_selected_entry([])

    async def async_step_bind_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Bind a device from Manage devices and save the merged selection."""
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
