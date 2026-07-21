"""Pytest fixtures for the Imou Life integration."""

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.imou_life.const import DOMAIN, PARAM_WEBHOOK_ID
from custom_components.imou_life.runtime_data import ImouRuntimeData
from homeassistant.core import HomeAssistant
from pyimouapi import InvalidAppIdOrSecretException
from pytest_homeassistant_custom_component.common import MockConfigEntry

from . import USER_INPUT

IMOU_TOKEN_RETURN = {
    "accessToken": "test_token",
    "expireTime": 3600,
    "currentDomain": "https://openapi.imoulife.com:443",
}


@pytest.fixture
def imou_config_flow() -> Generator[MagicMock]:
    """Mock ImouOpenApiClient for successful config flow tests."""
    with (
        patch(
            "custom_components.imou_life.config_flow.ImouOpenApiClient",
        ) as mock_client,
        patch(
            "custom_components.imou_life.config_flow.async_build_device_map",
            AsyncMock(return_value={}),
        ),
    ):
        instance = MagicMock()
        instance.async_get_token = AsyncMock(return_value=IMOU_TOKEN_RETURN)
        instance.async_close = AsyncMock()
        mock_client.return_value = instance
        yield mock_client


@pytest.fixture
def imou_config_flow_with_devices() -> Generator[MagicMock]:
    """Mock ImouOpenApiClient returning devices for selection tests."""
    with (
        patch(
            "custom_components.imou_life.config_flow.ImouOpenApiClient",
        ) as mock_client,
        patch(
            "custom_components.imou_life.config_flow.async_build_device_map",
            AsyncMock(
                return_value={
                    "device_1": "Front Door (IPC) [Online]",
                    "device_2": "Garage [Offline]",
                }
            ),
        ),
    ):
        instance = MagicMock()
        instance.async_get_token = AsyncMock(return_value=IMOU_TOKEN_RETURN)
        instance.async_close = AsyncMock()
        mock_client.return_value = instance
        yield mock_client


@pytest.fixture
def imou_config_flow_exception() -> Generator[MagicMock]:
    """Mock ImouOpenApiClient raising InvalidAppIdOrSecretException."""
    with patch(
        "custom_components.imou_life.config_flow.ImouOpenApiClient"
    ) as mock_client:
        instance = MagicMock()
        instance.async_get_token = AsyncMock(
            side_effect=InvalidAppIdOrSecretException()
        )
        instance.async_request_api = AsyncMock()
        instance.async_close = AsyncMock()
        mock_client.return_value = instance
        yield mock_client


def setup_imou_runtime(
    hass: HomeAssistant,
    *,
    webhook_id: str = "webhook-id",
    push_enabled: bool = True,
    selected_devices: list[str] | None = None,
    notify_services: list[str] | None = None,
    app_id: str = "test_app_id",
) -> ImouRuntimeData:
    """Attach ImouRuntimeData to a mock config entry for webhook tests."""
    entry_data = {**USER_INPUT, "app_id": app_id, PARAM_WEBHOOK_ID: webhook_id}
    entry = MockConfigEntry(domain=DOMAIN, data=entry_data)
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    delegate = MagicMock()
    delegate.async_resolve_event_identifier = AsyncMock(return_value=None)
    coordinator.device_manager.delegate = delegate
    runtime = ImouRuntimeData(
        coordinator=coordinator,
        push_enabled=push_enabled,
        selected_devices=selected_devices or [],
        notify_services=notify_services or [],
    )
    entry.runtime_data = runtime
    return runtime
