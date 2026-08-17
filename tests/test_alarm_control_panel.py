"""Imou alarm_control_panel for IoT arming mode."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from custom_components.imou_life.alarm_control_panel import (
    ImouAlarmControlPanel,
    _iter_alarm_control_panels,
)
from custom_components.imou_life.const import DOMAIN, PARAM_MODE
from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.exceptions import HomeAssistantError
from pyimouapi.const import PARAM_STATE
from pyimouapi.exceptions import ImouException
from pyimouapi.ha_device import ImouHaDevice
from pytest_homeassistant_custom_component.common import MockConfigEntry

from . import USER_INPUT


def _device(*, panel: dict | None) -> MagicMock:
    device = MagicMock(spec=ImouHaDevice)
    device.device_id = "dev1"
    device.channel_id = "0"
    device.product_id = "prod1"
    device.device_name = "Hub"
    device.channel_name = "Hub"
    device.manufacturer = "Imou"
    device.model = "Alarm"
    device.swversion = "1.0"
    device.alarm_control_panel = panel
    return device


def _coordinator(device: MagicMock) -> MagicMock:
    coordinator = MagicMock()
    coordinator.devices = [device]
    coordinator.devices_by_key = {}
    coordinator.device_manager.async_set_alarm_mode = AsyncMock()
    return coordinator


def test_iter_only_devices_with_panel() -> None:
    armed = _device(panel={PARAM_STATE: "home"})
    bare = _device(panel=None)
    pairs = _iter_alarm_control_panels(_coordinator(armed))
    assert [(key, dev) for key, dev in pairs] == [(PARAM_MODE, armed)]
    coordinator = _coordinator(bare)
    coordinator.devices = [bare]
    assert _iter_alarm_control_panels(coordinator) == []


@pytest.mark.parametrize(
    ("mode", "state"),
    [
        ("home", AlarmControlPanelState.ARMED_HOME),
        ("away", AlarmControlPanelState.ARMED_AWAY),
        ("disarm", AlarmControlPanelState.DISARMED),
    ],
)
def test_alarm_state_mapping(mode: str, state: AlarmControlPanelState) -> None:
    device = _device(panel={PARAM_STATE: mode})
    entity = ImouAlarmControlPanel(
        _coordinator(device), MockConfigEntry(domain=DOMAIN, data=USER_INPUT),
        PARAM_MODE, device,
    )
    assert entity.alarm_state is state


def test_translation_key_is_arming() -> None:
    device = _device(panel={PARAM_STATE: "home"})
    entity = ImouAlarmControlPanel(
        _coordinator(device), MockConfigEntry(domain=DOMAIN, data=USER_INPUT),
        PARAM_MODE, device,
    )
    assert entity._attr_translation_key == "arming"


@pytest.mark.parametrize(
    ("method", "mode"),
    [
        ("async_alarm_arm_home", "home"),
        ("async_alarm_arm_away", "away"),
        ("async_alarm_disarm", "disarm"),
    ],
)
@pytest.mark.usefixtures("enable_custom_integrations")
async def test_arm_methods_call_library(hass, method: str, mode: str) -> None:
    device = _device(panel={PARAM_STATE: "disarm"})
    coordinator = _coordinator(device)
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    entity = ImouAlarmControlPanel(coordinator, entry, PARAM_MODE, device)
    entity.async_write_ha_state = MagicMock()
    await getattr(entity, method)(None)
    coordinator.device_manager.async_set_alarm_mode.assert_awaited_once_with(
        device, mode
    )
    entity.async_write_ha_state.assert_called_once()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_library_error_is_translated(hass) -> None:
    device = _device(panel={PARAM_STATE: "home"})
    coordinator = _coordinator(device)
    coordinator.device_manager.async_set_alarm_mode = AsyncMock(
        side_effect=ImouException("quota")
    )
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    entity = ImouAlarmControlPanel(coordinator, entry, PARAM_MODE, device)
    with pytest.raises(HomeAssistantError) as err:
        await entity.async_alarm_disarm(None)
    assert err.value.translation_key == "alarm_arm_disarm_failed"
