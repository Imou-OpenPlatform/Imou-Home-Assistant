"""Config flow for Imou Life."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from pyimouapi.exceptions import ImouException
from pyimouapi.openapi import ImouOpenApiClient

from .const import (
    API_URL_OPTIONS,
    CONF_API_URL_SG,
    CONF_HD,
    CONF_HTTP,
    CONF_SD,
    CONF_HTTPS,
    DOMAIN,
    PARAM_API_URL,
    PARAM_APP_ID,
    PARAM_APP_SECRET,
    PARAM_DOWNLOAD_SNAP_WAIT_TIME,
    PARAM_LIVE_PROTOCOL,
    PARAM_LIVE_RESOLUTION,
    PARAM_ROTATION_DURATION,
    PARAM_UPDATE_INTERVAL,
)


class ImouConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Imou Life."""

    VERSION = 1

    @staticmethod
    def _login_schema(default_api_url: str = CONF_API_URL_SG) -> vol.Schema:
        """Schema for App Id / Secret / API region."""
        return vol.Schema(
            {
                vol.Required(PARAM_APP_ID): str,
                vol.Required(PARAM_APP_SECRET): str,
                vol.Required(PARAM_API_URL, default=default_api_url): vol.In(API_URL_OPTIONS),
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

    async def async_step_login(
        self, user_input: dict[str, Any]
    ) -> ConfigFlowResult:
        """Validate credentials and create the config entry."""
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

        return self.async_create_entry(
            title=DOMAIN,
            data={
                PARAM_APP_ID: user_input[PARAM_APP_ID],
                PARAM_APP_SECRET: user_input[PARAM_APP_SECRET],
                PARAM_API_URL: user_input[PARAM_API_URL],
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

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

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
                    }
                ),
                self.config_entry.options,
            ),
        )
