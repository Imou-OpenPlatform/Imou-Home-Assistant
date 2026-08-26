"""Config flow for Imou Life."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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
from pyimouapi.pic_decode import is_tcm_ability

from . import pic_thumbnail
from .const import (
    API_URL_REGIONS,
    BASE_PUSH_ALWAYS,
    CONF_HD,
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

_FIELD_REMOVE_PASSWORDS = "remove_device_passwords"
_FIELD_CLEAR_DEFAULT_PASSWORD = "clear_default_device_password"

_ENTRY_NAME = "Imou Life Official"

_GENERAL_OPTION_KEYS = (
    PARAM_ENABLE_POLLING,
    PARAM_UPDATE_INTERVAL,
    PARAM_DOWNLOAD_SNAP_WAIT_TIME,
    PARAM_LIVE_RESOLUTION,
    PARAM_ROTATION_DURATION,
)

_EVENT_PUSH_OPTION_KEYS = (
    PARAM_ENABLE_EVENT_PUSH,
    PARAM_WEBHOOK_URL,
    PARAM_EVENT_PUSH_TYPES,
    PARAM_NOTIFY_SERVICES,
)

SECTION_EVENT_PUSH_NOTIFICATIONS = "notifications"
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


_PRIVATE_HOST_SUFFIXES = (".local", ".lan", ".home", ".internal", ".arpa")


def _looks_publicly_reachable(url: str) -> bool:
    """Return True when a URL could be reached from outside the LAN.

    The Imou cloud POSTs alarms to this address from the internet, so a LAN
    address can never work. ``webhook.async_generate_url`` defaults to
    ``prefer_external=True``, which falls back to the internal address without
    saying so, so the generated suggestion has to be checked before it is
    offered as usable.
    """
    host = urlparse(url).hostname
    if not host:
        return False
    host = host.rstrip(".").lower()
    try:
        return ip_address(host).is_global
    except ValueError:
        pass
    if host == "localhost" or host.endswith(_PRIVATE_HOST_SUFFIXES):
        return False
    # A bare hostname resolves on the LAN only; public names carry a domain.
    return "." in host


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
                            vol.Required(PARAM_ROTATION_DURATION, default=500): vol.All(
                                vol.Coerce(int), vol.Range(min=100, max=10000)
                            ),
                        }
                    ),
                    {"collapsed": False},
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

    def _effective_callback_url(self, options: Mapping[str, Any]) -> str:
        """Return the callback URL the form shows: stored, else generated."""
        return (
            str(options.get(PARAM_WEBHOOK_URL) or "").strip()
            or self._generated_webhook_url()
        )

    def _callback_reach_hint(self, callback_url: str) -> str:
        """Warn that a LAN callback address can never receive cloud alarms."""
        if not callback_url or _looks_publicly_reachable(callback_url):
            return ""
        text = _selector_option_label(
            self.hass,
            self._ui_language(),
            "prerequisite",
            "callback_not_public",
            "This address looks reachable only on your local network, so the "
            "Imou cloud cannot POST alarms to it. Set an internet-reachable "
            "address under Settings → System → Network → Home Assistant URL, "
            "or type the address your reverse proxy exposes.",
        )
        return f"**{text}**\n\n"

    def _event_push_webhook_placeholders(
        self, options: Mapping[str, Any]
    ) -> dict[str, str]:
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
            "lan_hint": self._callback_reach_hint(
                self._effective_callback_url(options)
            ),
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

        # Prefill the generated address so turning push on does not first fail
        # on an empty field the user has to copy out of the description.
        webhook_url = self._effective_callback_url(flat)

        nested: dict[str, Any] = {
            PARAM_ENABLE_EVENT_PUSH: enable_push,
            PARAM_EVENT_PUSH_TYPES: event_push_types,
            SECTION_EVENT_PUSH_NOTIFICATIONS: {
                PARAM_NOTIFY_SERVICES: parse_notify_services(
                    flat.get(PARAM_NOTIFY_SERVICES)
                ),
            },
        }
        if webhook_url:
            nested[PARAM_WEBHOOK_URL] = webhook_url
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
            flat.update(notifications)
        else:
            flat[PARAM_NOTIFY_SERVICES] = parse_notify_services(
                stored_options.get(PARAM_NOTIFY_SERVICES)
            )
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
                "alarm_image_decrypt",
                "local_recording",
                "select_poll_devices",
                "bind_device",
                "finish",
            ],
            description_placeholders=self._menu_summary_placeholders(),
        )

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Close the options dialog. Every page has already saved itself."""
        return self.async_create_entry(data=self._merge_options())

    def _save_options(self, **updates: Any) -> None:
        """Persist option changes without closing the dialog.

        Each page saves on submit and hands control back to the menu, so a
        visit that touches two sections no longer means opening Configure
        twice. Closing is its own menu entry.
        """
        self.hass.config_entries.async_update_entry(
            self.config_entry, options=self._merge_options(**updates)
        )

    def _ui_language(self) -> str:
        language = getattr(self.hass.config, "language", None)
        if isinstance(language, str) and language:
            return language
        return "en"

    def _status_label(self, key: str, fallback: str) -> str:
        """Return a translated status word for the menu summary."""
        return _selector_option_label(
            self.hass, self._ui_language(), "status", key, fallback
        )

    def _push_enabled(self) -> bool:
        """Return True when event push is on, which alarm features require."""
        return bool(self.config_entry.options.get(PARAM_ENABLE_EVENT_PUSH))

    def _push_prerequisite_hint(self) -> str:
        """Warn that alarm pages stay inert while event push is off.

        Decryption and recording both run off the webhook dispatch, so with
        push off they read as configured but never fire.
        """
        if self._push_enabled():
            return ""
        text = _selector_option_label(
            self.hass,
            self._ui_language(),
            "prerequisite",
            "push_off",
            "Alarm push is off, so nothing on this page takes effect yet. "
            "Turn it on under Configure → Alarm push and notifications.",
        )
        return f"**{text}**\n\n"

    def _menu_summary_placeholders(self) -> dict[str, str]:
        """Summarise what is on, so the menu reads without opening each page."""
        options = self.config_entry.options
        on = self._status_label("on", "on")
        off = self._status_label("off", "off")
        if options.get(PARAM_ATTACH_DECRYPTED_THUMBNAIL):
            decrypt = (
                on
                if pic_thumbnail.native_libs_present(self.hass)
                else self._status_label("missing_libs", "on, but libraries are missing")
            )
        else:
            decrypt = off
        selected = self._stored_selected_devices()
        return {
            "push_state": on if options.get(PARAM_ENABLE_EVENT_PUSH) else off,
            "polling_state": on if options.get(PARAM_ENABLE_POLLING, True) else off,
            "device_state": (
                str(len(selected))
                if selected is not None
                else self._status_label("all_devices", "all")
            ),
            "decrypt_state": decrypt,
            "record_state": (
                on if str(options.get(PARAM_LOCAL_RECORD_PATH) or "").strip() else off
            ),
        }

    def _device_passwords(self) -> dict[str, str]:
        """Return the stored per-serial alarm image passwords."""
        stored = self.config_entry.options.get(PARAM_DEVICE_PASSWORDS, {})
        return dict(stored) if isinstance(stored, dict) else {}

    def _password_fields(self) -> list[tuple[str, str]]:
        """Return (form field, serial) pairs for the password form.

        Only TCM devices are keyed by a device password; the rest decrypt from
        their serial, so listing them would be a form field that does nothing.
        Serials that already hold a password stay listed either way, so a value
        stored before a device changed hands can still be seen and cleared.
        """
        stored = self._device_passwords()
        names: dict[str, str] = {}
        serials = set(stored)
        runtime = get_runtime_data(self.config_entry)
        if runtime is not None:
            for device in runtime.coordinator.devices_by_key.values():
                serial = device.device_id
                if not serial:
                    continue
                if device.device_name:
                    names[serial] = device.device_name
                if is_tcm_ability(device.device_ability or ""):
                    serials.add(serial)
        fields = []
        for serial in sorted(serials):
            name = names.get(serial)
            fields.append((f"{name} ({serial})" if name else serial, serial))
        return fields

    def _alarm_image_decrypt_schema(
        self, overrides: Mapping[str, Any] | None = None
    ) -> vol.Schema:
        """Build the decrypt switch, the default password, and per-device ones."""
        stored = self._device_passwords()
        options = self.config_entry.options
        stored_default = str(options.get(PARAM_DEFAULT_DEVICE_PASSWORD) or "")
        password_selector = TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.PASSWORD,
                autocomplete="new-password",
            )
        )
        fields: dict[Any, Any] = {
            vol.Optional(PARAM_ATTACH_DECRYPTED_THUMBNAIL, default=False): bool,
            vol.Optional(PARAM_DEFAULT_DEVICE_PASSWORD, default=""): password_selector,
        }
        # Prefill stored passwords so reopening the page does not look empty.
        # Empty fields still keep the previous values on save; rejected
        # submits may still re-show typed text.
        suggested: dict[str, Any] = {
            PARAM_ATTACH_DECRYPTED_THUMBNAIL: bool(
                options.get(PARAM_ATTACH_DECRYPTED_THUMBNAIL, False)
            ),
            PARAM_DEFAULT_DEVICE_PASSWORD: stored_default,
        }
        for field, serial in self._password_fields():
            fields[vol.Optional(field, default="")] = password_selector
            suggested[field] = str(stored.get(serial) or "")
        if stored_default:
            fields[vol.Optional(_FIELD_CLEAR_DEFAULT_PASSWORD, default=False)] = bool
            suggested[_FIELD_CLEAR_DEFAULT_PASSWORD] = False
        if overrides:
            # A rejected submit must not throw away what was just typed.
            suggested.update(
                {key: overrides[key] for key in suggested if key in overrides}
            )
        if stored:
            fields[vol.Optional(_FIELD_REMOVE_PASSWORDS, default=[])] = SelectSelector(
                SelectSelectorConfig(
                    options=sorted(stored),
                    multiple=True,
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
        return self.add_suggested_values_to_schema(vol.Schema(fields), suggested)

    def _merge_device_passwords(self, user_input: Mapping[str, Any]) -> dict[str, str]:
        """Merge the password form into the stored serial map.

        Empty per-device fields keep the existing password. Checked remove
        serials are deleted last.
        """
        passwords = self._device_passwords()
        for field, serial in self._password_fields():
            password = str(user_input.get(field) or "")
            if password:
                passwords[serial] = password
        for serial in user_input.get(_FIELD_REMOVE_PASSWORDS) or []:
            passwords.pop(str(serial).strip(), None)
        return passwords

    def _merged_default_device_password(self, user_input: Mapping[str, Any]) -> str:
        """Keep the stored default when the password box is left empty."""
        if user_input.get(_FIELD_CLEAR_DEFAULT_PASSWORD):
            return ""
        typed = str(user_input.get(PARAM_DEFAULT_DEVICE_PASSWORD) or "")
        if typed:
            return typed
        return str(self.config_entry.options.get(PARAM_DEFAULT_DEVICE_PASSWORD) or "")

    async def async_step_alarm_image_decrypt(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Turn decryption on and hold every password it needs, on one form."""
        errors: dict[str, str] = {}
        if user_input is not None:
            attach = bool(user_input.get(PARAM_ATTACH_DECRYPTED_THUMBNAIL, False))
            turning_on = attach and not self.config_entry.options.get(
                PARAM_ATTACH_DECRYPTED_THUMBNAIL
            )
            if turning_on and not pic_thumbnail.native_libs_present(self.hass):
                # Switching it on here would silently do nothing. Something
                # already on is left alone: the libraries can go missing later,
                # and passwords still need to be editable. The menu reports
                # that state instead.
                errors[PARAM_ATTACH_DECRYPTED_THUMBNAIL] = "decrypt_libs_missing"
            else:
                default_password = self._merged_default_device_password(user_input)
                self._save_options(
                    **{
                        PARAM_ATTACH_DECRYPTED_THUMBNAIL: attach,
                        PARAM_DEFAULT_DEVICE_PASSWORD: default_password,
                        PARAM_DEVICE_PASSWORDS: self._merge_device_passwords(
                            user_input
                        ),
                    }
                )
                return await self.async_step_init()

        stored = self._device_passwords()
        return self.async_show_form(
            step_id="alarm_image_decrypt",
            data_schema=self._alarm_image_decrypt_schema(user_input),
            errors=errors,
            description_placeholders={
                "password_count": str(len(stored)),
                "configured_serials": ", ".join(sorted(stored)) if stored else "-",
                "native_hint": pic_thumbnail.native_libraries_hint(
                    self.hass, self._ui_language()
                ),
                "push_hint": self._push_prerequisite_hint(),
            },
        )

    def _local_recording_schema(self) -> vol.Schema:
        """Build the local recording form."""
        return vol.Schema(
            {
                vol.Optional(PARAM_LOCAL_RECORD_PATH, default=""): str,
                vol.Required(
                    PARAM_LOCAL_RECORD_DURATION,
                    default=DEFAULT_LOCAL_RECORD_DURATION,
                ): vol.All(vol.Coerce(int), vol.Range(min=15, max=180)),
            }
        )

    async def async_step_local_recording(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage options — where alarm clips go and how long they run."""
        options = self.config_entry.options
        errors: dict[str, str] = {}
        if user_input is not None:
            folder = str(user_input.get(PARAM_LOCAL_RECORD_PATH) or "").strip()
            path_error = self._local_record_path_error(folder)
            if path_error:
                errors[PARAM_LOCAL_RECORD_PATH] = path_error
            else:
                self._save_options(
                    **{
                        PARAM_LOCAL_RECORD_PATH: folder,
                        PARAM_LOCAL_RECORD_DURATION: user_input[
                            PARAM_LOCAL_RECORD_DURATION
                        ],
                    }
                )
                return await self.async_step_init()
            suggested = dict(user_input)
        else:
            suggested = {
                PARAM_LOCAL_RECORD_PATH: options.get(PARAM_LOCAL_RECORD_PATH, ""),
                PARAM_LOCAL_RECORD_DURATION: options.get(
                    PARAM_LOCAL_RECORD_DURATION, DEFAULT_LOCAL_RECORD_DURATION
                ),
            }

        return self.async_show_form(
            step_id="local_recording",
            data_schema=self.add_suggested_values_to_schema(
                self._local_recording_schema(), suggested
            ),
            errors=errors,
            description_placeholders={"push_hint": self._push_prerequisite_hint()},
        )

    async def async_step_general_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage options — polling, camera, and PTZ settings."""
        if user_input is not None:
            self._save_options(
                **self._flatten_general_input(user_input, self.config_entry.options)
            )
            return await self.async_step_init()

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
        """Manage options — event push and notification targets."""
        stored = dict(self.config_entry.options)
        errors: dict[str, Any] = {}
        error_detail = ""
        suggested_source: Mapping[str, Any] = stored
        if user_input is not None:
            flat = self._flatten_event_push_input(user_input, stored)
            if flat[PARAM_ENABLE_EVENT_PUSH]:
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
                self._save_options(**flat)
                return await self.async_step_init()
            suggested_source = {**stored, **flat}

        placeholders = self._event_push_webhook_placeholders(suggested_source)
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

    def _save_selected_devices(self, selected: list[str]) -> None:
        """Write the poll list, dropping any setup-time fallback."""
        self._clear_selected_from_entry_data()
        self._save_options(**{PARAM_SELECTED_DEVICES: selected})

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

    async def _async_load_devices_map(self) -> str:
        """Fetch the account device list. Return an error detail, or empty."""
        api_client = ImouOpenApiClient(
            self.config_entry.data[PARAM_APP_ID],
            self.config_entry.data[PARAM_APP_SECRET],
            self.config_entry.data[PARAM_API_URL],
        )
        try:
            try:
                self._devices_map = await async_build_device_map(self.hass, api_client)
            finally:
                await api_client.async_close()
        except ImouException as exception:
            _LOGGER.warning(
                "Failed to fetch device list for options: %s", exception.message
            )
            return _api_error_placeholder(exception)
        except Exception as exception:
            _LOGGER.exception("Failed to fetch device list for options")
            return _api_error_placeholder(exception)
        return ""

    async def async_step_select_poll_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick which account devices to poll. Submit saves and returns."""
        if user_input is not None and PARAM_SELECTED_DEVICES in user_input:
            selected = list(user_input.get(PARAM_SELECTED_DEVICES, []))
            self._save_selected_devices(selected)
            return await self.async_step_init()
        if not self._devices_map:
            self._devices_error = await self._async_load_devices_map()
            if self._devices_error:
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
            menu_options=["select_poll_devices", "save_without_devices"],
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
        self._save_selected_devices([])
        return self.async_create_entry(data=self._merge_options())

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
        self._save_selected_devices(selected)
        return await self.async_step_init()
