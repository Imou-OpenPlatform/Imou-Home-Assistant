"""Tests for how Imou devices are nested on the Home Assistant device pages.

An account device with several channels, and a gateway with accessories paired
to it, used to arrive here as unrelated devices. Each channel and accessory now
points at the device it belongs to, so the device page groups them.
"""

from __future__ import annotations

from time import monotonic
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.imou_life import async_remove_config_entry_device
from custom_components.imou_life.const import (
    DISCOVERY_INTERVAL,
    DOMAIN,
    PARAM_SELECTED_DEVICES,
    PARAM_WEBHOOK_ID,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from pyimouapi.const import PARAM_STATE
from pyimouapi.ha_device import DeviceStatus, ImouHaDevice
from pytest_homeassistant_custom_component.common import MockConfigEntry

from . import USER_INPUT


def _channel(
    device_id: str,
    channel_id: str,
    *,
    device_name: str = "Cam",
    channel_name: str | None = None,
) -> ImouHaDevice:
    """Return one camera channel of an account device."""
    device = ImouHaDevice(device_id, device_name, "Imou", "IPC-A1", "1.0")
    device.set_channel_id(channel_id)
    if channel_name is not None:
        device.set_channel_name(channel_name)
    device.sensors["status"] = {PARAM_STATE: DeviceStatus.ONLINE.value}
    return device


def _accessory(
    device_id: str,
    product_id: str,
    *,
    parent_device_id: str | None = None,
    parent_product_id: str | None = None,
    device_name: str = "Door contact",
) -> ImouHaDevice:
    """Return a channel-less accessory, optionally paired to a gateway."""
    device = ImouHaDevice(device_id, device_name, "Imou", "DS21", "1.0")
    device.set_product_id(product_id)
    if parent_device_id is not None:
        device.set_parent_device_id(parent_device_id)
    if parent_product_id is not None:
        device.set_parent_product_id(parent_product_id)
    device.sensors["status"] = {PARAM_STATE: DeviceStatus.ONLINE.value}
    return device


async def _setup(
    hass: HomeAssistant,
    devices: list[ImouHaDevice],
    *,
    options: dict | None = None,
) -> tuple[MockConfigEntry, MagicMock]:
    """Load the integration with the given devices."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**USER_INPUT, PARAM_WEBHOOK_ID: "wh-links"},
        options=options or {},
        version=2,
    )
    entry.add_to_hass(hass)
    manager = MagicMock()
    manager.async_get_devices = AsyncMock(return_value=devices)
    manager.async_update_devices_status = AsyncMock(return_value=None)
    manager.delegate.async_ensure_event_map = AsyncMock(return_value={})
    client = MagicMock()
    client.async_close = AsyncMock()
    with (
        patch("custom_components.imou_life.ImouOpenApiClient", return_value=client),
        patch("custom_components.imou_life.ImouDeviceManager"),
        patch("custom_components.imou_life.ImouHaDeviceManager", return_value=manager),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id) is True
        await hass.async_block_till_done()
    return entry, manager


def _row(hass: HomeAssistant, key: str) -> dr.DeviceEntry | None:
    """Return the device registry row for an Imou registry key."""
    return dr.async_get(hass).async_get_device(identifiers={(DOMAIN, key)})


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_channels_are_nested_under_the_account_device(
    hass: HomeAssistant,
) -> None:
    """Both lenses of one camera belong to the device the account reports."""
    await _setup(
        hass,
        [
            _channel("dev0", "0", channel_name="Gate lens 1"),
            _channel("dev0", "1", channel_name="Gate lens 2"),
        ],
    )

    account = _row(hass, "dev0")
    assert account is not None
    assert account.name == "Cam"
    assert _row(hass, "dev0_0").via_device_id == account.id
    assert _row(hass, "dev0_1").via_device_id == account.id


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_a_single_channel_camera_stays_one_device(hass: HomeAssistant) -> None:
    """One lens must not gain an extra page standing for the same camera."""
    await _setup(hass, [_channel("dev0", "0")])

    assert _row(hass, "dev0") is None
    assert _row(hass, "dev0_0").via_device_id is None


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_an_accessory_is_nested_under_its_gateway(hass: HomeAssistant) -> None:
    """A paired accessory belongs to the gateway that reports it."""
    await _setup(
        hass,
        [
            _channel("gw0", "0", device_name="Hub"),
            _accessory("acc0", "prod1", parent_device_id="gw0", parent_product_id="p0"),
        ],
    )

    gateway = _row(hass, "gw0_0")
    assert gateway is not None
    assert _row(hass, "acc0_prod1").via_device_id == gateway.id


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_an_accessory_without_its_gateway_is_left_flat(
    hass: HomeAssistant,
) -> None:
    """The gateway can be deselected, and a link to nothing is not a link."""
    await _setup(
        hass,
        [_accessory("acc0", "prod1", parent_device_id="gw0", parent_product_id="p0")],
    )

    assert _row(hass, "acc0_prod1").via_device_id is None


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_a_device_leaving_the_account_keeps_the_others_nested(
    hass: HomeAssistant,
) -> None:
    """The account row is keyed by the bare device id, not by a channel.

    A device deleted in the Imou app detaches every row whose key is no longer
    on the account, and the account row reads as one of those unless the whole
    device id counts too.
    """
    entry, manager = await _setup(
        hass,
        [
            _channel("dev0", "0"),
            _channel("dev0", "1"),
            _channel("gone", "0", device_name="Old cam"),
        ],
    )
    coordinator = entry.runtime_data.coordinator
    manager.async_get_devices.return_value = [
        _channel("dev0", "0"),
        _channel("dev0", "1"),
    ]

    with patch(
        "custom_components.imou_life.coordinator.monotonic",
        return_value=monotonic() + DISCOVERY_INTERVAL,
    ):
        await coordinator.async_refresh()
    await hass.async_block_till_done()

    account = _row(hass, "dev0")
    assert account is not None
    assert entry.entry_id in account.config_entries
    assert _row(hass, "gone_0") is None
    assert _row(hass, "dev0_1").via_device_id == account.id


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_removing_the_account_device_deselects_it(hass: HomeAssistant) -> None:
    """Deleting the row that stands for the whole device is that exclusion.

    A channel cannot be excluded on its own, but the account device can, and
    that row is the only place where the user can say it.
    """
    entry, _ = await _setup(
        hass,
        [_channel("dev0", "0"), _channel("dev0", "1")],
        options={PARAM_SELECTED_DEVICES: ["dev0", "dev9"]},
    )

    account = _row(hass, "dev0")
    assert await async_remove_config_entry_device(hass, entry, account) is True
    assert entry.options[PARAM_SELECTED_DEVICES] == ["dev9"]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_removing_one_channel_still_refuses(hass: HomeAssistant) -> None:
    """The account row must not read as a sibling channel of itself."""
    entry, _ = await _setup(
        hass,
        [
            _channel("dev0", "0", channel_name="Gate lens 1"),
            _channel("dev0", "1", channel_name="Gate lens 2"),
        ],
        options={PARAM_SELECTED_DEVICES: ["dev0"]},
    )

    with pytest.raises(HomeAssistantError) as err:
        await async_remove_config_entry_device(hass, entry, _row(hass, "dev0_0"))
    assert "Gate lens 2" in str(err.value)
    assert "Cam" not in str(err.value)
    assert entry.options[PARAM_SELECTED_DEVICES] == ["dev0"]
