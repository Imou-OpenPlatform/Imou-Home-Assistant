"""Tests for entities left behind by a device that dropped off the account.

HA reads an entity's capability attributes before it reads `available`, so a
select or text entity that looks its options up in the coordinator raises
before availability can save it. `async_update_listeners` has no handler for
that, which costs every entity after it in the list its update.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.imou_life.const import DOMAIN, PARAM_WEBHOOK_ID
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from pyimouapi.const import PARAM_STATE
from pyimouapi.ha_device import DeviceStatus, ImouHaDevice
from pytest_homeassistant_custom_component.common import MockConfigEntry

from . import USER_INPUT


def _device() -> ImouHaDevice:
    """Return a device carrying one select and one text entity."""
    device = ImouHaDevice("dev0", "Cam", "Imou", "IPC-A1", "1.0")
    device.set_channel_id("0")
    device.sensors["status"] = {PARAM_STATE: DeviceStatus.ONLINE.value}
    device.selects["night_vision_mode"] = {
        "options": ["auto", "on", "off"],
        "current_option": "auto",
    }
    device.texts["count_down_switch"] = {PARAM_STATE: "60"}
    return device


@pytest.fixture
def loaded_entry(hass: HomeAssistant):
    """Set up the integration with one device and hand back the coordinator."""

    async def _load():
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={**USER_INPUT, PARAM_WEBHOOK_ID: "wh-missing"},
            version=2,
        )
        entry.add_to_hass(hass)
        manager = MagicMock()
        manager.async_get_devices = AsyncMock(return_value=[_device()])
        manager.async_update_device_status = AsyncMock(return_value=None)
        client = MagicMock()
        client.async_close = AsyncMock()
        with (
            patch("custom_components.imou_life.ImouOpenApiClient", return_value=client),
            patch("custom_components.imou_life.ImouDeviceManager"),
            patch(
                "custom_components.imou_life.ImouHaDeviceManager", return_value=manager
            ),
        ):
            assert await hass.config_entries.async_setup(entry.entry_id) is True
            await hass.async_block_till_done()
        return entry, manager

    return _load


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_a_device_leaving_the_account_does_not_break_the_update(
    hass: HomeAssistant, loaded_entry
) -> None:
    """The last device going away is the case that gives the cleanup no chance."""
    entry, _ = await loaded_entry()
    coordinator = entry.runtime_data.coordinator
    assert hass.states.get("select.cam_night_vision_mode") is not None

    coordinator.devices_by_key.clear()
    # No devices left means the poll returns without awaiting anything, so the
    # registry cleanup task has not run by the time listeners are notified.
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    for entity_id in ("select.cam_night_vision_mode", "text.cam_countdown_timer_min"):
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == STATE_UNAVAILABLE


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_the_entity_recovers_when_the_device_comes_back(
    hass: HomeAssistant, loaded_entry
) -> None:
    """A device that reappears must go back to reporting its real value."""
    entry, _ = await loaded_entry()
    coordinator = entry.runtime_data.coordinator
    restored = dict(coordinator.devices_by_key)

    coordinator.devices_by_key.clear()
    coordinator.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get("select.cam_night_vision_mode").state == STATE_UNAVAILABLE

    coordinator.devices_by_key.update(restored)
    coordinator.async_update_listeners()
    await hass.async_block_till_done()
    assert hass.states.get("select.cam_night_vision_mode").state == "auto"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_later_entities_still_get_their_update(
    hass: HomeAssistant, loaded_entry
) -> None:
    """One raising entity used to cost every entity behind it its update."""
    entry, _ = await loaded_entry()
    coordinator = entry.runtime_data.coordinator
    device = next(iter(coordinator.devices_by_key.values()))

    device.selects["night_vision_mode"]["current_option"] = "on"
    coordinator.async_update_listeners()
    await hass.async_block_till_done()

    assert hass.states.get("select.cam_night_vision_mode").state == "on"
    assert hass.states.get("text.cam_countdown_timer_min").state == "60"
