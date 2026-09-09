"""Tests for how often the coordinator lists the Imou account.

Listing costs a paged request, and ability-ref detail calls only run for
devices Home Assistant has not seen yet. Status polling is on a faster clock.
These tests pin that split.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.imou_life.const import DISCOVERY_INTERVAL, DOMAIN
from custom_components.imou_life.coordinator import ImouDataUpdateCoordinator
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import issue_registry as ir
from pyimouapi.exceptions import (
    InvalidAppIdOrSecretException,
    RequestFailedException,
)
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
    manager.async_update_devices_status = AsyncMock(return_value=None)
    manager.delegate.async_ensure_event_map = AsyncMock()
    return manager


def _make_coordinator(
    hass: HomeAssistant,
    device_manager: MagicMock,
    *,
    setting_up: bool = False,
) -> ImouDataUpdateCoordinator:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    if setting_up:
        # async_config_entry_first_refresh refuses to run outside setup.
        entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)
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
    assert device_manager.async_update_devices_status.await_count == 3


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

    device_manager.async_get_devices.side_effect = [
        [_mock_device("d1"), _mock_device("d2")],
        [_mock_device("d1"), _mock_device("d2")],
    ]
    with patch(
        "custom_components.imou_life.coordinator.monotonic",
        return_value=1000.0 + DISCOVERY_INTERVAL,
    ):
        await coordinator._async_update_data()

    # First discovery + list-only rediscovery + ability refs for the new id.
    assert device_manager.async_get_devices.await_count == 3
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

    device_manager.async_update_devices_status.side_effect = (
        InvalidAppIdOrSecretException("bad secret")
    )
    # Going through the public refresh is the point: reporting the refusal as
    # ConfigEntryAuthFailed is what makes HA open the flow and stop polling,
    # and calling the private method would prove neither.
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress()
    assert len(flows) == 1
    assert flows[0]["context"]["source"] == "reauth"
    assert flows[0]["step_id"] == "reauth_confirm"
    assert flows[0]["context"]["entry_id"] == coordinator.config_entry.entry_id
    assert not coordinator.last_update_success


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize(
    "error",
    [
        RequestFailedException("cloud down"),
        TimeoutError("status poll timed out"),
    ],
    ids=["request_failed", "timeout"],
)
async def test_status_poll_failure_keeps_last_state(
    hass: HomeAssistant, device_manager: MagicMock, error: BaseException
) -> None:
    """A status refresh blip must not grey every entity until the next interval."""
    coordinator = _make_coordinator(hass, device_manager)
    await coordinator._async_update_data()
    known = dict(coordinator.devices_by_key)
    assert coordinator.last_update_success

    device_manager.async_update_devices_status.side_effect = error
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.last_update_success
    assert coordinator.devices_by_key == known


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_quota_poll_failure_creates_repair_and_clears_on_success(
    hass: HomeAssistant, device_manager: MagicMock
) -> None:
    """Used-up Open Platform calls must show under Repairs, then go away."""
    from custom_components.imou_life.repairs import ISSUE_OPEN_API_QUOTA

    coordinator = _make_coordinator(hass, device_manager)
    await coordinator._async_update_data()
    issue_id = f"{ISSUE_OPEN_API_QUOTA}_{coordinator.config_entry.entry_id}"
    assert (DOMAIN, issue_id) not in ir.async_get(hass).issues

    device_manager.async_update_devices_status.side_effect = RequestFailedException(
        "OP1013:Call interface times exceed limit (total)."
    )
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.last_update_success
    assert (DOMAIN, issue_id) in ir.async_get(hass).issues

    device_manager.async_update_devices_status.side_effect = None
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.last_update_success
    assert (DOMAIN, issue_id) not in ir.async_get(hass).issues


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_bad_credentials_stop_the_polling_instead_of_retrying(
    hass: HomeAssistant, device_manager: MagicMock
) -> None:
    """Retrying a known-bad secret every minute only burns the account's quota."""
    coordinator = _make_coordinator(hass, device_manager, setting_up=True)
    await coordinator.async_config_entry_first_refresh()
    unsubscribe = coordinator.async_add_listener(lambda: None)

    device_manager.async_update_devices_status.side_effect = (
        InvalidAppIdOrSecretException("bad secret")
    )
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator._unsub_refresh is None
    unsubscribe()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_a_failed_listing_leaves_known_devices_alone(
    hass: HomeAssistant, device_manager: MagicMock
) -> None:
    """A blip on the slow discovery clock must not blank every entity."""
    coordinator = _make_coordinator(hass, device_manager, setting_up=True)
    with patch(
        "custom_components.imou_life.coordinator.monotonic", return_value=1000.0
    ):
        await coordinator.async_config_entry_first_refresh()
    known = dict(coordinator.devices_by_key)

    device_manager.async_get_devices.side_effect = RequestFailedException("cloud down")
    with patch(
        "custom_components.imou_life.coordinator.monotonic",
        return_value=1000.0 + DISCOVERY_INTERVAL,
    ):
        await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert coordinator.devices_by_key == known
    assert device_manager.async_update_devices_status.await_count == 2

    # The clock stayed put, so the next poll retries rather than waiting out
    # the rest of the interval.
    device_manager.async_get_devices.side_effect = None
    device_manager.async_get_devices.return_value = [_mock_device("d1")]
    with patch(
        "custom_components.imou_life.coordinator.monotonic",
        return_value=1000.0 + DISCOVERY_INTERVAL + 1,
    ):
        await coordinator.async_refresh()
    assert device_manager.async_get_devices.await_count == 3


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_a_first_listing_failure_still_defers_setup(
    hass: HomeAssistant, device_manager: MagicMock
) -> None:
    """With nothing known yet there is nothing to keep, so setup must retry."""
    coordinator = _make_coordinator(hass, device_manager, setting_up=True)
    device_manager.async_get_devices.side_effect = RequestFailedException("cloud down")

    with pytest.raises(ConfigEntryNotReady):
        await coordinator.async_config_entry_first_refresh()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_a_first_status_poll_failure_still_finishes_setup(
    hass: HomeAssistant, device_manager: MagicMock
) -> None:
    """Devices are already listed; a status blip must not leave setup retrying."""
    coordinator = _make_coordinator(hass, device_manager, setting_up=True)
    device_manager.async_update_devices_status.side_effect = RequestFailedException(
        "cloud down"
    )

    await coordinator.async_config_entry_first_refresh()

    assert coordinator.last_update_success
    assert coordinator.devices_by_key
