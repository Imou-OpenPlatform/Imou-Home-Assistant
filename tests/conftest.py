"""Pytest fixtures for the Imou Life integration."""

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.imou_life.const import DOMAIN, PARAM_WEBHOOK_ID
from custom_components.imou_life.runtime_data import ImouRuntimeData
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
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
        instance.async_set_message_callback = AsyncMock()
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
        instance.async_set_message_callback = AsyncMock()
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


def register_imou_ha_device(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    device_id: str,
    *,
    channel_id: str | int | None = "0",
    product_id: str | None = None,
    name: str = "Test Device",
) -> None:
    """Create a device registry row so webhook pushes resolve a HA name."""
    from custom_components.imou_life.const import imou_life_device_key_from_ids

    registry = dr.async_get(hass)
    key = imou_life_device_key_from_ids(device_id, channel_id, product_id)
    if key is None:
        key = f"{device_id}_0"
    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, key)},
        name=name,
    )


def setup_imou_runtime(
    hass: HomeAssistant,
    *,
    webhook_id: str = "webhook-id",
    push_enabled: bool = True,
    selected_devices: list[str] | None = None,
    notify_services: list[str] | None = None,
    app_id: str = "test_app_id",
    register_ha_devices: bool = True,
    options: dict | None = None,
) -> ImouRuntimeData:
    """Attach ImouRuntimeData to a mock config entry for webhook tests."""
    from custom_components.imou_life.const import (
        PARAM_ENABLE_EVENT_PUSH,
        PARAM_SELECTED_DEVICES,
    )

    entry_options = {PARAM_ENABLE_EVENT_PUSH: push_enabled, **(options or {})}
    # Webhook filters by entry.options; keep the helper argument as the source
    # of truth unless the caller already set selected_devices in options.
    if selected_devices is not None and PARAM_SELECTED_DEVICES not in entry_options:
        entry_options[PARAM_SELECTED_DEVICES] = list(selected_devices)
    entry_data = {**USER_INPUT, "app_id": app_id, PARAM_WEBHOOK_ID: webhook_id}
    entry = MockConfigEntry(domain=DOMAIN, data=entry_data, options=entry_options)
    entry.add_to_hass(hass)
    if register_ha_devices and selected_devices:
        for device_id in selected_devices:
            register_imou_ha_device(hass, entry, device_id)
    coordinator = MagicMock()
    coordinator.devices = []
    coordinator.async_set_updated_data = MagicMock()
    delegate = MagicMock()
    delegate.async_resolve_event_identifier = AsyncMock(return_value=None)
    coordinator.device_manager.delegate = delegate
    runtime = ImouRuntimeData(
        coordinator=coordinator,
        push_enabled=push_enabled,
        selected_devices=selected_devices,
        notify_services=notify_services or [],
    )
    entry.runtime_data = runtime
    return runtime
