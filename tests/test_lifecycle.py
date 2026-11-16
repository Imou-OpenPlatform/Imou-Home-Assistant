"""Tests for Imou Life config entry lifecycle and resource cleanup."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.imou_life import async_unload_entry
from custom_components.imou_life.const import (
    DOMAIN,
    PARAM_ENABLE_EVENT_PUSH,
    PARAM_WEBHOOK_ID,
)
from custom_components.imou_life.diagnostics import async_get_config_entry_diagnostics
from custom_components.imou_life.insight_frontend import (
    async_register_insight_trial_frontend,
)
from custom_components.imou_life.runtime_data import ImouRuntimeData, get_runtime_data
from custom_components.imou_life.webhook import (
    async_handle_imou_webhook,
    async_register_imou_webhook,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pyimouapi.exceptions import ConnectFailedException, InvalidAppIdOrSecretException
from pytest_homeassistant_custom_component.common import MockConfigEntry

from . import USER_INPUT


class MockRequest:
    """Minimal aiohttp request mock."""

    def __init__(self, payload: Any) -> None:
        """Initialize the request mock."""
        self._payload = payload

    async def json(self) -> Any:
        """Return the configured JSON payload."""
        return self._payload


def _entry(hass: HomeAssistant, **kwargs: Any) -> MockConfigEntry:
    """Add a config entry with a webhook id to hass."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**USER_INPUT, PARAM_WEBHOOK_ID: "wh-lifecycle"},
        version=2,
        **kwargs,
    )
    entry.add_to_hass(hass)
    return entry


async def test_trial_frontend_absence_does_not_fail_setup(
    hass: HomeAssistant,
) -> None:
    """Missing optional frontend support must not prevent setup."""
    assert "frontend" not in hass.config.components

    assert await async_register_insight_trial_frontend(hass) is False


async def test_trial_frontend_registers_once(hass: HomeAssistant) -> None:
    """Available frontend support is registered only once."""
    assert await async_setup_component(hass, "http", {})
    hass.config.components.add("frontend")

    with (
        patch.object(
            hass.http, "async_register_static_paths", AsyncMock()
        ) as register,
        patch(
            "custom_components.imou_life.insight_frontend.frontend.add_extra_js_url"
        ) as add_js,
    ):
        assert await async_register_insight_trial_frontend(hass) is True
        assert await async_register_insight_trial_frontend(hass) is True

    register.assert_awaited_once()
    add_js.assert_called_once()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_failed_setup_closes_api_session(hass: HomeAssistant) -> None:
    """A setup that fails after creating the client must still close its session."""
    entry = _entry(hass)
    client = MagicMock()
    client.async_close = AsyncMock()
    manager = MagicMock()
    manager.async_get_devices = AsyncMock(side_effect=ConnectFailedException("boom"))

    with (
        patch(
            "custom_components.imou_life.ImouOpenApiClient", return_value=client
        ) as mock_client,
        patch("custom_components.imou_life.ImouDeviceManager"),
        patch("custom_components.imou_life.ImouHaDeviceManager", return_value=manager),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id) is False
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert mock_client.call_count == 1
    client.async_close.assert_awaited_once()


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize(
    ("exception", "expected_state"),
    [
        (
            InvalidAppIdOrSecretException("bad credentials"),
            ConfigEntryState.SETUP_ERROR,
        ),
        (ConnectFailedException("cloud failure"), ConfigEntryState.SETUP_RETRY),
    ],
)
async def test_setup_entry_auth_and_cloud_errors(
    hass: HomeAssistant,
    exception: Exception,
    expected_state: ConfigEntryState,
) -> None:
    """Invalid credentials fail setup; other cloud errors retry."""
    entry = _entry(hass)
    manager = MagicMock()
    manager.async_get_devices = AsyncMock(side_effect=exception)

    with (
        patch("custom_components.imou_life.ImouOpenApiClient"),
        patch("custom_components.imou_life.ImouDeviceManager"),
        patch("custom_components.imou_life.ImouHaDeviceManager", return_value=manager),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is expected_state


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_registration_survives_setup_retry(hass: HomeAssistant) -> None:
    """Registering the same webhook id twice must not raise."""
    assert async_register_imou_webhook(hass, "wh-retry") is not None
    # A previous failed setup leaves the handler behind; HA raises on duplicates.
    async_register_imou_webhook(hass, "wh-retry")


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_unload_reuses_runtime_client(hass: HomeAssistant) -> None:
    """Disabling the cloud callback reuses the client that already holds a token."""
    entry = _entry(hass, options={PARAM_ENABLE_EVENT_PUSH: True})
    client = MagicMock()
    entry.runtime_data = ImouRuntimeData(
        coordinator=AsyncMock(), client=client, push_enabled=True
    )

    with (
        patch(
            "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
            AsyncMock(return_value=True),
        ),
        patch("custom_components.imou_life.ImouOpenApiClient") as mock_client,
        patch(
            "custom_components.imou_life.async_teardown_event_push", AsyncMock()
        ) as mock_teardown,
    ):
        assert await async_unload_entry(hass, entry) is True

    mock_client.assert_not_called()
    assert mock_teardown.await_args.args[2] is client


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_runtime_data_helper_handles_unloaded_entry(hass: HomeAssistant) -> None:
    """runtime_data is absent, not None, while an entry is not set up."""
    entry = _entry(hass)

    assert not hasattr(entry, "runtime_data")
    assert get_runtime_data(entry) is None


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_diagnostics_on_unloaded_entry(hass: HomeAssistant) -> None:
    """Diagnostics must not raise for an entry that is not loaded."""
    entry = _entry(hass)

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["last_update_success"] is None
    assert result["event_push"]["last_msg_type"] is None


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_webhook_ignores_unloaded_entry(hass: HomeAssistant) -> None:
    """A push for an entry without runtime data is acknowledged and dropped."""
    _entry(hass)

    response = await async_handle_imou_webhook(
        hass,
        "wh-lifecycle",
        MockRequest({"msgType": "alarmLocal", "deviceId": "dev-a"}),
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert response.status == 200
