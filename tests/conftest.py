"""Pytest fixtures for the Imou Life integration."""

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pyimouapi import InvalidAppIdOrSecretException

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
