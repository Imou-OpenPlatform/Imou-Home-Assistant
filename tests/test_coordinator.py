"""Unit tests for ImouDataUpdateCoordinator."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from custom_components.imou_life.const import DOMAIN, PARAM_SELECTED_DEVICES
from custom_components.imou_life.coordinator import ImouDataUpdateCoordinator
from homeassistant.core import HomeAssistant
from pyimouapi.ha_device import ImouHaDevice
from pytest_homeassistant_custom_component.common import MockConfigEntry

from . import USER_INPUT


def _mock_device(device_id: str, channel_id: str | None = None) -> MagicMock:
    device = MagicMock(spec=ImouHaDevice)
    device.device_id = device_id
    device.channel_id = channel_id
    device.product_id = f"prod_{device_id}"
    return device


@pytest.fixture
def device_manager() -> MagicMock:
    """Mock ImouHaDeviceManager."""
    manager = MagicMock()
    manager.async_get_devices = AsyncMock()
    manager.async_update_device_status = AsyncMock(return_value=None)
    return manager


async def _run_update(
    hass: HomeAssistant,
    device_manager: MagicMock,
    devices: list[MagicMock],
    *,
    data: dict | None = None,
    options: dict | None = None,
) -> ImouDataUpdateCoordinator:
    entry_data = {**USER_INPUT, **(data or {})}
    entry = MockConfigEntry(domain=DOMAIN, data=entry_data, options=options or {})
    entry.add_to_hass(hass)
    device_manager.async_get_devices.return_value = devices
    coordinator = ImouDataUpdateCoordinator(hass, device_manager, entry)
    await coordinator._async_update_data()
    return coordinator


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_filter_none_selects_all(
    hass: HomeAssistant, device_manager: MagicMock
) -> None:
    """When no selection is stored, all devices are polled."""
    devices = [_mock_device("d1"), _mock_device("d2")]
    coordinator = await _run_update(hass, device_manager, devices)
    assert len(coordinator.devices) == 2
    assert {d.device_id for d in coordinator.devices} == {"d1", "d2"}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_filter_empty_list_selects_none(
    hass: HomeAssistant, device_manager: MagicMock
) -> None:
    """An explicit empty selection polls no devices."""
    devices = [_mock_device("d1"), _mock_device("d2")]
    coordinator = await _run_update(
        hass,
        device_manager,
        devices,
        data={PARAM_SELECTED_DEVICES: []},
    )
    assert coordinator.devices == []


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_filter_specific_ids(
    hass: HomeAssistant, device_manager: MagicMock
) -> None:
    """Only devices whose ids appear in the selection are polled."""
    devices = [_mock_device("d1"), _mock_device("d2"), _mock_device("d3")]
    coordinator = await _run_update(
        hass,
        device_manager,
        devices,
        options={PARAM_SELECTED_DEVICES: ["d1", "d3"]},
    )
    assert len(coordinator.devices) == 2
    assert {d.device_id for d in coordinator.devices} == {"d1", "d3"}
