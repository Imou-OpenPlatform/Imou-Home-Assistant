"""Unit tests for ImouDataUpdateCoordinator."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from custom_components.imou_life.const import (
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    EVENT_PUSH_TYPE_CAPABILITY_EVENT,
    EVENT_PUSH_TYPE_IOT,
    PARAM_ENABLE_EVENT_PUSH,
    PARAM_ENABLE_POLLING,
    PARAM_EVENT_PUSH_TYPES,
    PARAM_SELECTED_DEVICES,
    PARAM_UPDATE_INTERVAL,
    imou_life_device_key,
)
from custom_components.imou_life.coordinator import ImouDataUpdateCoordinator
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_registry import RegistryEntryDisabler
from pyimouapi.bulb_light import empty_bulb_state
from pyimouapi.capability.catalog import CapabilitySet
from pyimouapi.ha_device import ImouHaDevice
from pytest_homeassistant_custom_component.common import MockConfigEntry

from . import USER_INPUT


def _mock_device(device_id: str, channel_id: str | None = None) -> MagicMock:
    device = MagicMock(spec=ImouHaDevice)
    device.device_id = device_id
    device.channel_id = channel_id
    device.product_id = f"prod_{device_id}"
    device.parent_device_id = None
    device.parent_product_id = None
    return device


@pytest.fixture
def device_manager() -> MagicMock:
    """Mock ImouHaDeviceManager."""
    manager = MagicMock()
    manager.async_get_devices = AsyncMock()
    manager.async_update_device_status = AsyncMock(return_value=None)
    manager.async_update_devices_status = AsyncMock(return_value=None)
    manager.delegate.async_ensure_event_map = AsyncMock()
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
async def test_refresh_copies_the_new_play_token(
    hass: HomeAssistant, device_manager: MagicMock
) -> None:
    """A later device list updates the play token kept on the known device."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    coordinator = ImouDataUpdateCoordinator(hass, device_manager, entry)
    current = ImouHaDevice("SN1", "Cam", "Imou", "IPC", "1.0")
    current.set_channel_id("0")
    fresh = ImouHaDevice("SN1", "Cam", "Imou", "IPC", "1.0")
    fresh.set_channel_id("0")
    fresh.set_play_token_v2("token-v2")
    key = imou_life_device_key(current)
    coordinator.devices_by_key = {key: current}
    await coordinator._async_refresh_known_devices({key: fresh})
    assert current.play_token_v2 == "token-v2"


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


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_coordinator_skips_poll_when_all_entities_disabled(
    hass: HomeAssistant, device_manager: MagicMock
) -> None:
    """Skip cloud status poll when every entity for a device is disabled."""
    devices = [_mock_device("d1")]
    coordinator = await _run_update(hass, device_manager, devices)
    device_key = imou_life_device_key(devices[0])

    device_registry = dr.async_get(hass)
    device_entry = device_registry.async_get_or_create(
        config_entry_id=coordinator.config_entry.entry_id,
        identifiers={(DOMAIN, device_key)},
        name="Test device",
    )
    entity_registry = er.async_get(hass)
    entity_registry.async_get_or_create(
        DOMAIN,
        "sensor",
        f"{device_key}$status",
        config_entry=coordinator.config_entry,
        device_id=device_entry.id,
        disabled_by=RegistryEntryDisabler.USER,
    )

    device_manager.async_update_devices_status.reset_mock()
    await coordinator._async_update_data()
    device_manager.async_update_devices_status.assert_not_called()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_first_discovery_purges_registry_devices_gone_from_account(
    hass: HomeAssistant, device_manager: MagicMock
) -> None:
    """Reload starts uninitialized; an empty account must still drop stale devices.

    Unload deliberately leaves registry entries alone. The next setup's first
    discovery used to assign an empty map and return without cleaning, so
    devices deleted in the Imou app kept showing after Configure → save.
    """
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    stale_key = "gone_prod_gone"
    device_registry = dr.async_get(hass)
    stale = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, stale_key)},
        name="Deleted camera",
    )

    device_manager.async_get_devices.return_value = []
    coordinator = ImouDataUpdateCoordinator(hass, device_manager, entry)
    await coordinator._async_update_data()

    assert coordinator.devices == []
    assert (
        device_registry.async_get(stale.id) is None
        or entry.entry_id not in device_registry.async_get(stale.id).config_entries
    )


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_first_discovery_keeps_registry_devices_still_on_account(
    hass: HomeAssistant, device_manager: MagicMock
) -> None:
    """First discovery must not detach devices that are still listed."""
    devices = [_mock_device("d1")]
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    device_key = imou_life_device_key(devices[0])
    device_registry = dr.async_get(hass)
    kept = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, device_key)},
        name="Still here",
    )

    device_manager.async_get_devices.return_value = devices
    coordinator = ImouDataUpdateCoordinator(hass, device_manager, entry)
    await coordinator._async_update_data()

    assert len(coordinator.devices) == 1
    assert device_registry.async_get(kept.id) is not None
    assert entry.entry_id in device_registry.async_get(kept.id).config_entries


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_deselecting_a_device_removes_it_from_the_registry(
    hass: HomeAssistant, device_manager: MagicMock
) -> None:
    """Unchecked devices leave the Home Assistant device list.

    The poll picker still lists every account device. A device left unchecked
    is removed here, together with its entities.
    """
    devices = [_mock_device("d1"), _mock_device("d2")]
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={PARAM_SELECTED_DEVICES: ["d1"]},
    )
    entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    kept = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, imou_life_device_key(devices[0]))},
        name="Still polled",
    )
    filtered_key = imou_life_device_key(devices[1])
    filtered = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, filtered_key)},
        name="Not selected",
    )
    entity_registry = er.async_get(hass)
    entity_registry.async_get_or_create(
        DOMAIN,
        "sensor",
        f"{filtered_key}$status",
        config_entry=entry,
        device_id=filtered.id,
    )

    device_manager.async_get_devices.return_value = devices
    coordinator = ImouDataUpdateCoordinator(hass, device_manager, entry)
    await coordinator._async_update_data()

    assert {d.device_id for d in coordinator.devices} == {"d1"}
    assert entry.entry_id in device_registry.async_get(kept.id).config_entries
    gone = device_registry.async_get(filtered.id)
    assert gone is None or entry.entry_id not in gone.config_entries
    assert (
        entity_registry.async_get_entity_id(DOMAIN, "sensor", f"{filtered_key}$status")
        is None
    )


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_coordinator_no_interval_when_polling_disabled(
    hass: HomeAssistant, device_manager: MagicMock
) -> None:
    """When enable_polling is false, coordinator has no scheduled interval."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={PARAM_ENABLE_POLLING: False, PARAM_UPDATE_INTERVAL: 120},
    )
    entry.add_to_hass(hass)
    coordinator = ImouDataUpdateCoordinator(hass, device_manager, entry)
    assert coordinator.update_interval is None


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_coordinator_interval_when_polling_enabled(
    hass: HomeAssistant, device_manager: MagicMock
) -> None:
    """When enable_polling is true, coordinator uses update_interval option."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={PARAM_ENABLE_POLLING: True, PARAM_UPDATE_INTERVAL: 120},
    )
    entry.add_to_hass(hass)
    coordinator = ImouDataUpdateCoordinator(hass, device_manager, entry)
    assert coordinator.update_interval is not None
    assert coordinator.update_interval.total_seconds() == 120


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_coordinator_default_interval_is_five_minutes(
    hass: HomeAssistant, device_manager: MagicMock
) -> None:
    """Unset update_interval falls back to 300 seconds."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, options={})
    entry.add_to_hass(hass)
    coordinator = ImouDataUpdateCoordinator(hass, device_manager, entry)
    assert coordinator.update_interval is not None
    assert coordinator.update_interval.total_seconds() == DEFAULT_UPDATE_INTERVAL


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_rediscovery_uses_single_capability_list_when_unchanged(
    hass: HomeAssistant, device_manager: MagicMock
) -> None:
    """Rediscovery lists the account once; pyimouapi runs capability.list inside."""
    devices = [_mock_device("d1")]
    coordinator = await _run_update(hass, device_manager, devices)
    device_manager.async_get_devices.assert_awaited()

    device_manager.async_get_devices.reset_mock()
    device_manager.async_get_devices.return_value = devices
    coordinator._last_discovery = None
    await coordinator._async_update_data()

    device_manager.async_get_devices.assert_awaited_once()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_discovery_configures_only_selected_devices(
    hass: HomeAssistant, device_manager: MagicMock
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={PARAM_SELECTED_DEVICES: ["d1"]},
    )
    entry.add_to_hass(hass)
    coordinator = ImouDataUpdateCoordinator(hass, device_manager, entry)
    device_manager.async_get_devices.return_value = [_mock_device("d1")]
    await coordinator._async_update_data()
    kwargs = device_manager.async_get_devices.await_args.kwargs
    assert kwargs.get("configure_device_ids") == {"d1"}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_ability_text_change_does_not_list_devices_again(
    hass: HomeAssistant, device_manager: MagicMock
) -> None:
    original = _mock_device("d1")
    original.device_ability = "WLAN"
    original.channel_ability = "old"
    coordinator = await _run_update(hass, device_manager, [original])
    changed = _mock_device("d1")
    changed.device_ability = "WLAN"
    changed.channel_ability = "new"
    device_manager.async_get_devices.reset_mock()
    device_manager.async_get_devices.return_value = [changed]
    coordinator._last_discovery = None
    await coordinator._async_update_data()
    device_manager.async_get_devices.assert_awaited_once()
    original.set_channel_ability.assert_called_once_with("new")


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_rediscovery_registers_new_device_from_single_list(
    hass: HomeAssistant, device_manager: MagicMock
) -> None:
    """pyimouapi 2.x configures entities on the first list; no second list pass."""
    first = [_mock_device("d1")]
    coordinator = await _run_update(hass, device_manager, first)

    second = [_mock_device("d1"), _mock_device("d2")]
    device_manager.async_get_devices.reset_mock()
    device_manager.async_get_devices.return_value = second
    coordinator._last_discovery = None
    await coordinator._async_update_data()

    device_manager.async_get_devices.assert_awaited_once()
    assert {d.device_id for d in coordinator.devices} == {"d1", "d2"}


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_rediscovery_retries_and_merges_complete_v2_snapshot(
    hass: HomeAssistant, device_manager: MagicMock
) -> None:
    """A recovered product/capability lookup adds entities to the live device."""
    missing = ImouHaDevice("lamp", "Lamp", "Imou", "L1", "1.0")
    missing.set_product_id("lamp-product")
    coordinator = await _run_update(hass, device_manager, [missing])
    callback = MagicMock()
    coordinator.new_device_callbacks.append(callback)

    capability = CapabilitySet(
        properties=dict.fromkeys(("control.outlet.enabled",)),  # type: ignore[arg-type]
        services=frozenset(),
        events=frozenset(),
    )
    recovered = ImouHaDevice("lamp", "Lamp", "Imou", "L1", "1.0")
    recovered.set_product_id("lamp-product")
    recovered.set_product_device_type("Lamp")
    recovered.set_capability_set(capability)
    recovered.bulb_light = empty_bulb_state()
    recovered.set_bulb_v2(True)

    device_manager.async_get_devices.return_value = [recovered]
    coordinator._last_discovery = None
    await coordinator._async_update_data()

    current = coordinator.devices[0]
    assert current is missing
    assert current.product_device_type == "Lamp"
    assert current.capability_set == capability
    assert current.bulb_light is not None
    callback.assert_called_once_with([current])

    coordinator._last_discovery = None
    await coordinator._async_update_data()

    callback.assert_called_once_with([current])


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_discovery_does_not_fetch_product_event_maps(
    hass: HomeAssistant, device_manager: MagicMock
) -> None:
    """Call and motion entities follow the capability list, not product events."""
    d1 = _mock_device("d1")
    d2 = _mock_device("d2")
    d2.product_id = "prod_d1"
    await _run_update(hass, device_manager, [d1, d2])
    device_manager.delegate.async_ensure_event_map.assert_not_awaited()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_new_device_does_not_fetch_product_event_maps(
    hass: HomeAssistant, device_manager: MagicMock
) -> None:
    """A camera added later is registered from its capability list."""
    first = [_mock_device("d1")]
    coordinator = await _run_update(hass, device_manager, first)
    device_manager.delegate.async_ensure_event_map.reset_mock()

    second = [_mock_device("d1"), _mock_device("d2")]
    device_manager.async_get_devices.side_effect = [second, second]
    coordinator._last_discovery = None
    await coordinator._async_update_data()

    device_manager.delegate.async_ensure_event_map.assert_not_awaited()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_rediscovery_single_list_removes_gone_and_adds_new(
    hass: HomeAssistant, device_manager: MagicMock
) -> None:
    """One capability.list pass updates additions and removals together."""
    first = [_mock_device("d1"), _mock_device("gone")]
    coordinator = await _run_update(hass, device_manager, first)
    assert {d.device_id for d in coordinator.devices} == {"d1", "gone"}

    remaining = [_mock_device("d1"), _mock_device("new")]
    device_manager.async_get_devices.reset_mock()
    device_manager.async_get_devices.return_value = remaining
    coordinator._last_discovery = None
    await coordinator._async_update_data()

    assert {d.device_id for d in coordinator.devices} == {"d1", "new"}
    assert coordinator._last_discovery is not None


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_status_poll_uses_shared_device_update(
    hass: HomeAssistant, device_manager: MagicMock
) -> None:
    """The status poll batches devices so multi-channel reads can be shared."""
    devices = [_mock_device("d1", "0"), _mock_device("d1", "1")]
    coordinator = await _run_update(hass, device_manager, devices)

    device_manager.async_update_devices_status.reset_mock()
    coordinator._last_discovery = 1e18  # discovery not due
    await coordinator._async_update_data()

    device_manager.async_update_devices_status.assert_awaited_once()
    polled = device_manager.async_update_devices_status.await_args.args[0]
    assert {imou_life_device_key(d) for d in polled} == {
        imou_life_device_key(devices[0]),
        imou_life_device_key(devices[1]),
    }
    device_manager.async_update_device_status.assert_not_called()


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize(
    "push_type", [EVENT_PUSH_TYPE_CAPABILITY_EVENT, EVENT_PUSH_TYPE_IOT]
)
async def test_property_push_skips_v2_state_after_first_snapshot(
    hass: HomeAssistant, device_manager: MagicMock, push_type: str
) -> None:
    """First status update snapshots; the next interval skips those ids."""
    device = _mock_device("d1", "0")
    device_manager.async_update_devices_status = AsyncMock(return_value={"d1"})
    options = {
        PARAM_ENABLE_EVENT_PUSH: True,
        PARAM_EVENT_PUSH_TYPES: [push_type],
    }
    coordinator = await _run_update(hass, device_manager, [device], options=options)
    first_skip = device_manager.async_update_devices_status.await_args.kwargs.get(
        "skip_iot_property_ids"
    )
    assert first_skip in (None, set())
    assert coordinator._v2_state_snapshots == {"d1"}

    device_manager.async_update_devices_status.reset_mock()
    device_manager.async_update_devices_status.return_value = set()
    await coordinator._async_update_data()
    skipped = device_manager.async_update_devices_status.await_args.kwargs[
        "skip_iot_property_ids"
    ]
    assert "d1" in skipped


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_property_push_inactive_never_skips_iot_detail(
    hass: HomeAssistant, device_manager: MagicMock
) -> None:
    """With event push off, every interval still requests a detail fetch."""
    device = _mock_device("d1", "0")
    device_manager.async_update_devices_status = AsyncMock(return_value={"d1"})
    coordinator = await _run_update(
        hass,
        device_manager,
        [device],
        options={PARAM_ENABLE_EVENT_PUSH: False},
    )
    await coordinator._async_update_data()
    kwargs = device_manager.async_update_devices_status.await_args.kwargs
    assert kwargs.get("skip_iot_property_ids") in (None, set())


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_removed_accessory_forgets_snapshot_before_rejoining(
    hass: HomeAssistant, device_manager: MagicMock
) -> None:
    """A removed gateway accessory must take a fresh snapshot if it returns."""
    device = _mock_device("sensor", "0")
    device.parent_device_id = "gateway"
    device.parent_product_id = "gateway-product"
    physical_id = "sensor_gateway_gateway-product"
    device_manager.async_update_devices_status = AsyncMock(return_value={physical_id})
    options = {
        PARAM_ENABLE_EVENT_PUSH: True,
        PARAM_EVENT_PUSH_TYPES: [EVENT_PUSH_TYPE_IOT],
    }
    coordinator = await _run_update(hass, device_manager, [device], options=options)
    assert coordinator._v2_state_snapshots == {physical_id}

    coordinator._async_add_remove_devices({})
    assert coordinator._v2_state_snapshots == set()

    replacement = _mock_device("sensor", "0")
    replacement.parent_device_id = "gateway"
    replacement.parent_product_id = "gateway-product"
    replacement_key = imou_life_device_key(replacement)
    coordinator._async_add_remove_devices({replacement_key: replacement})
    coordinator._last_discovery = 1e18
    device_manager.async_update_devices_status.reset_mock()
    await coordinator._async_update_data()

    assert (
        device_manager.async_update_devices_status.await_args.kwargs[
            "skip_iot_property_ids"
        ]
        == set()
    )
