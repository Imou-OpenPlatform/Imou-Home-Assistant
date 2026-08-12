"""Tests for how often the coordinator lists the Imou account.

Listing costs a paged request plus a detail round trip per iot device and its
results are thrown away for devices already known, so it runs on a slower clock
than the status poll. These tests pin that split.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.imou_life.const import DISCOVERY_INTERVAL, DOMAIN
from custom_components.imou_life.coordinator import ImouDataUpdateCoordinator
from homeassistant.core import HomeAssistant
from pyimouapi.exceptions import InvalidAppIdOrSecretException
from pyimouapi.ha_device import ImouHaDevice
from pytest_homeassistant_custom_component.common import MockConfigEntry

from . import USER_INPUT


def _mock_device(device_id: str) -> MagicMock:
    device = MagicMock(spec=ImouHaDevice)
    device.device_id = device_id
    device.channel_id = None
    device.product_id = f"prod_{device_id}"
    return device


@pytest.fixture
def device_manager() -> MagicMock:
    """Mock ImouHaDeviceManager returning one device."""
    manager = MagicMock()
    manager.async_get_devices = AsyncMock(return_value=[_mock_device("d1")])
    manager.async_update_device_status = AsyncMock(return_value=None)
    return manager


def _make_coordinator(
    hass: HomeAssistant, device_manager: MagicMock
) -> ImouDataUpdateCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    return ImouDataUpdateCoordinator(hass, device_manager, entry)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_status_polls_do_not_list_the_account_every_time(
    hass: HomeAssistant, device_manager: MagicMock
) -> None:
    """Back-to-back polls read status but list the account only once."""
    coordinator = _make_coordinator(hass, device_manager)

    await coordinator._async_update_data()
    await coordinator._async_update_data()
    await coordinator._async_update_data()

    assert device_manager.async_get_devices.await_count == 1
    assert device_manager.async_update_device_status.await_count == 3


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_listing_resumes_once_the_interval_passes(
    hass: HomeAssistant, device_manager: MagicMock
) -> None:
    """A device added to the account is picked up after the discovery interval."""
    coordinator = _make_coordinator(hass, device_manager)
    added: list[list[ImouHaDevice]] = []
    coordinator.new_device_callbacks.append(added.append)

    with patch(
        "custom_components.imou_life.coordinator.monotonic", return_value=1000.0
    ):
        await coordinator._async_update_data()
    assert len(coordinator.devices) == 1

    device_manager.async_get_devices.return_value = [
        _mock_device("d1"),
        _mock_device("d2"),
    ]
    with patch(
        "custom_components.imou_life.coordinator.monotonic",
        return_value=1000.0 + DISCOVERY_INTERVAL,
    ):
        await coordinator._async_update_data()

    assert device_manager.async_get_devices.await_count == 2
    assert {d.device_id for d in coordinator.devices} == {"d1", "d2"}
    assert [d.device_id for batch in added for d in batch] == ["d2"]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_a_poll_just_under_the_interval_still_skips_listing(
    hass: HomeAssistant, device_manager: MagicMock
) -> None:
    """The clock has to actually run out before listing again."""
    coordinator = _make_coordinator(hass, device_manager)

    with patch(
        "custom_components.imou_life.coordinator.monotonic", return_value=1000.0
    ):
        await coordinator._async_update_data()
    with patch(
        "custom_components.imou_life.coordinator.monotonic",
        return_value=1000.0 + DISCOVERY_INTERVAL - 1,
    ):
        await coordinator._async_update_data()

    assert device_manager.async_get_devices.await_count == 1


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_credentials_revoked_between_listings_still_ask_for_reauth(
    hass: HomeAssistant, device_manager: MagicMock
) -> None:
    """Status calls are what notice revoked credentials once listing slows down."""
    coordinator = _make_coordinator(hass, device_manager)
    await coordinator._async_update_data()
    assert not hass.config_entries.flow.async_progress()

    device_manager.async_update_device_status.side_effect = (
        InvalidAppIdOrSecretException("bad secret")
    )
    with pytest.raises(Exception, match="bad secret"):
        await coordinator._async_update_data()
    await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == "reauth"
    assert flows[0]["step_id"] == "reauth_confirm"
    assert flows[0]["context"]["entry_id"] == coordinator.config_entry.entry_id
