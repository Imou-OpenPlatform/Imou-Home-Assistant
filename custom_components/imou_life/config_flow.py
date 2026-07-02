"""Config flow for Imou Life."""

from __future__ import annotations

import logging
import uuid
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components import webhook
from homeassistant.config_entries import ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import translation
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
)
from pyimouapi.exceptions import ImouException
from pyimouapi.openapi import ImouOpenApiClient

from .const import (
    API_URL_OPTIONS,
    CONF_API_URL_SG,
    CONF_HD,
    CONF_HTTP,
    CONF_HTTPS,
    CONF_SD,
    DEFAULT_BASE_PUSH,
    DEFAULT_EVENT_PUSH_TYPES,
    DOMAIN,
    EVENT_PUSH_TYPE_OPTIONS,
    PARAM_API_URL,
    PARAM_APP_ID,
    PARAM_APP_SECRET,
    PARAM_BASE_PUSH,
    PARAM_DOWNLOAD_SNAP_WAIT_TIME,
    PARAM_ENABLE_EVENT_PUSH,
    PARAM_EVENT_PUSH_TYPES,
    PARAM_LIVE_PROTOCOL,
    PARAM_LIVE_RESOLUTION,
    PARAM_NOTIFY_SERVICES,
    PARAM_ROTATION_DURATION,
    PARAM_SELECTED_DEVICES,
    PARAM_UPDATE_INTERVAL,
    PARAM_WEBHOOK_ID,
    PARAM_WEBHOOK_URL,
    callback_flags_to_event_push_types,
)
from .helpers import async_build_device_map

_LOGGER = logging.getLogger(__name__)


def _options_placeholder(hass, key: str, fallback: str) -> str:
    """Load webhook placeholder label from selector translations."""
    translations = translation.async_get_cached_translations(
        hass, hass.config.language, "selector", DOMAIN
    )
    translation_key = f"component.{DOMAIN}.selector.webhook_placeholder.options.{key}"
    return translations.get(translation_key, fallback)


class ImouConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Imou Life."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize config flow."""
        self._devices_map: dict[str, str] = {}
        self._login_data: dict[str, Any] = {}

    @staticmethod
    def _login_schema(default_api_url: str = CONF_API_URL_SG) -> vol.Schema:
        """Schema for App Id / Secret / API region."""
        return vol.Schema(
            {
                vol.Required(PARAM_APP_ID): str,
                vol.Required(PARAM_APP_SECRET): str,
                vol.Required(PARAM_API_URL, default=default_api_url): vol.In(
                    API_URL_OPTIONS
                ),
            }
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step when user starts adding the integration."""
        if user_input is None:
            return self.async_show_form(
                step_id="login",
                data_schema=self._login_schema(),
            )
        return await self.async_step_login(user_input)

    async def async_step_login(self, user_input: dict[str, Any]) -> ConfigFlowResult:
        """Validate credentials, then fetch device list for selection."""
        await self.async_set_unique_id(user_input[PARAM_APP_ID])
        self._abort_if_unique_id_configured()
        api_client = ImouOpenApiClient(
            user_input[PARAM_APP_ID],
            user_input[PARAM_APP_SECRET],
            user_input[PARAM_API_URL],
        )
        errors: dict[str, str] = {}
        try:
            await api_client.async_get_token()
        except ImouException as exception:
            errors["base"] = exception.get_title()
            return self.async_show_form(
                step_id="login",
                data_schema=self._login_schema(user_input[PARAM_API_URL]),
                errors=errors,
            )

        # Save login data for later entry creation
        self._login_data = {
            PARAM_APP_ID: user_input[PARAM_APP_ID],
            PARAM_APP_SECRET: user_input[PARAM_APP_SECRET],
            PARAM_API_URL: user_input[PARAM_API_URL],
            PARAM_WEBHOOK_ID: uuid.uuid4().hex,
        }

        # Fetch device list for selection
        try:
            self._devices_map = await async_build_device_map(self.hass, api_client)
        except Exception:
            _LOGGER.warning(
                "Failed to fetch device list, creating entry without device selection"
            )
            self._devices_map = {}
        finally:
            await api_client.async_close()

        if not self._devices_map:
            # No devices found or fetch failed — create entry directly (all devices)
            return self.async_create_entry(
                title=DOMAIN,
                data=self._login_data,
            )

        return await self.async_step_select_devices()

    async def async_step_select_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let user select which devices to add."""
        if user_input is not None:
            selected = user_input.get(PARAM_SELECTED_DEVICES, [])
            return self.async_create_entry(
                title=DOMAIN,
                data={**self._login_data, PARAM_SELECTED_DEVICES: selected},
            )

        # Default: all devices preselected
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
        self._general_options: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage options — general settings page."""
        if user_input is not None:
            # Stash general options, then move to device selection step
            self._general_options = user_input
            return await self.async_step_devices()

        webhook_id = self.config_entry.data.get(PARAM_WEBHOOK_ID, "")
        suggested_webhook_url = ""
        if webhook_id:
            try:
                suggested_webhook_url = webhook.async_generate_url(
                    self.hass, webhook_id
                )
            except Exception:
                suggested_webhook_url = f"/api/webhook/{webhook_id}"
        current_webhook_url = self.config_entry.options.get(PARAM_WEBHOOK_URL, "")

        not_generated = _options_placeholder(
            self.hass, "not_generated", "Not generated"
        )
        not_set_use_suggested = _options_placeholder(
            self.hass,
            "not_set_use_suggested",
            "Not set — the suggested URL above will be used",
        )

        suggested_options = dict(self.config_entry.options)
        if stored_types := suggested_options.get(PARAM_EVENT_PUSH_TYPES):
            suggested_options[PARAM_EVENT_PUSH_TYPES] = (
                callback_flags_to_event_push_types(stored_types)
            )

        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(PARAM_UPDATE_INTERVAL, default=60): vol.All(
                            vol.Coerce(int), vol.Range(min=30, max=900)
                        ),
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
                        # --- Event push settings ---
                        vol.Required(PARAM_ENABLE_EVENT_PUSH, default=False): bool,
                        vol.Optional(PARAM_WEBHOOK_URL, default=""): str,
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
                        vol.Required(
                            PARAM_BASE_PUSH, default=DEFAULT_BASE_PUSH
                        ): SelectSelector(
                            SelectSelectorConfig(
                                options=["1", "2"],
                                translation_key="base_push",
                            )
                        ),
                        # --- Notification settings ---
                        vol.Optional(PARAM_NOTIFY_SERVICES, default=""): str,
                    }
                ),
                suggested_options,
            ),
            description_placeholders={
                "webhook_id": webhook_id or not_generated,
                "suggested_webhook_url": suggested_webhook_url or not_generated,
                "current_webhook_url": current_webhook_url or not_set_use_suggested,
            },
            last_step=False,
        )

    async def async_step_devices(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage device selection — add/remove devices without re-setup."""
        if user_input is not None:
            selected = user_input.get(PARAM_SELECTED_DEVICES, [])
            # Merge general options + device selection into one options dict
            merged = {**self._general_options, PARAM_SELECTED_DEVICES: selected}
            return self.async_create_entry(data=merged)

        # Fetch current device list from API
        errors: dict[str, str] = {}
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
        except Exception:
            _LOGGER.exception("Failed to fetch device list for options")
            errors["base"] = "request_failed"

        if not self._devices_map and not errors:
            errors["base"] = "request_failed"

        if errors:
            # Can't fetch devices, show error and let user go back
            return self.async_show_form(
                step_id="devices",
                data_schema=vol.Schema({}),
                errors=errors,
            )

        # Preselect currently active devices
        current_selected = (
            self.config_entry.options.get(PARAM_SELECTED_DEVICES)
            or self.config_entry.data.get(PARAM_SELECTED_DEVICES)
            or list(self._devices_map.keys())
        )

        return self.async_show_form(
            step_id="devices",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        PARAM_SELECTED_DEVICES,
                        default=[d for d in current_selected if d in self._devices_map],
                    ): cv.multi_select(self._devices_map),
                }
            ),
            description_placeholders={
                "device_count": str(len(self._devices_map)),
            },
        )
