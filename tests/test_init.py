"""Tests for Imou Life setup and unload."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.imou_life import (
    async_migrate_entry,
    async_remove_config_entry_device,
    async_remove_replaced_legacy_entities,
    async_remove_ungated_push_entities,
    async_unload_entry,
)
from custom_components.imou_life.const import (
    DOMAIN,
    PARAM_SELECTED_DEVICES,
    PARAM_WEBHOOK_ID,
)
from custom_components.imou_life.runtime_data import ImouRuntimeData
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from . import USER_INPUT


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_migrate_entry_adds_missing_webhook_id(hass) -> None:
    """Legacy v1 entries receive a webhook_id during migration."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, version=1)
    entry.add_to_hass(hass)

    assert await async_migrate_entry(hass, entry) is True
    assert entry.version == 2
    assert PARAM_WEBHOOK_ID in entry.data
    assert entry.data[PARAM_WEBHOOK_ID]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_setup_removes_replaced_mode_select_and_siren_buttons(hass) -> None:
    """Upgrade leftover select.mode and siren buttons must not stay as ghosts."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, version=2)
    entry.add_to_hass(hass)
    device_registry = dr.async_get(hass)
    device_entry = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "cam_0")},
        name="Cam",
    )
    registry = er.async_get(hass)
    mode_select = registry.async_get_or_create(
        "select",
        DOMAIN,
        "cam_0$mode",
        config_entry=entry,
        device_id=device_entry.id,
    )
    night = registry.async_get_or_create(
        "select",
        DOMAIN,
        "cam_0$night_vision_mode",
        config_entry=entry,
        device_id=device_entry.id,
    )
    siren_start = registry.async_get_or_create(
        "button",
        DOMAIN,
        "cam_0$siren_start",
        config_entry=entry,
        device_id=device_entry.id,
    )
    siren_stop = registry.async_get_or_create(
        "button",
        DOMAIN,
        "cam_0$siren_stop",
        config_entry=entry,
        device_id=device_entry.id,
    )
    panel = registry.async_get_or_create(
        "alarm_control_panel",
        DOMAIN,
        "cam_0$mode",
        config_entry=entry,
        device_id=device_entry.id,
    )

    async_remove_replaced_legacy_entities(hass, entry)

    assert registry.async_get(mode_select.entity_id) is None
    assert registry.async_get(siren_start.entity_id) is None
    assert registry.async_get(siren_stop.entity_id) is None
    assert registry.async_get(night.entity_id) is not None
    assert registry.async_get(panel.entity_id) is not None


def _paas_camera(device_id: str, *, channel_ability: str) -> MagicMock:
    device = MagicMock()
    device.device_id = device_id
    device.channel_id = "0"
    device.product_id = None
    device.is_ipc = True
    device.device_ability = "WLAN"
    device.channel_ability = channel_ability
    return device


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_upgrade_drops_motion_on_cameras_without_detect(hass) -> None:
    """1.4.0 created Motion on every camera; ungated leftovers must be removed."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, version=2)
    entry.add_to_hass(hass)
    keep = _paas_camera("keep", channel_ability="WLAN,MobileDetect")
    drop = _paas_camera("drop", channel_ability="WLAN")
    coordinator = MagicMock()
    coordinator.devices = [keep, drop]
    coordinator.devices_by_key = {"keep_0": keep, "drop_0": drop}
    coordinator.device_manager.delegate.cached_event_map = MagicMock(return_value={})
    entry.runtime_data = ImouRuntimeData(coordinator=coordinator)

    device_registry = dr.async_get(hass)
    keep_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "keep_0")},
        name="Keep",
    )
    drop_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "drop_0")},
        name="Drop",
    )
    gone_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "gone_0")},
        name="Gone",
    )
    registry = er.async_get(hass)
    keep_motion = registry.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        "keep_0$motion",
        config_entry=entry,
        device_id=keep_device.id,
    )
    drop_motion = registry.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        "drop_0$motion",
        config_entry=entry,
        device_id=drop_device.id,
    )
    drop_doorbell = registry.async_get_or_create(
        "event",
        DOMAIN,
        "drop_0$doorbell",
        config_entry=entry,
        device_id=drop_device.id,
    )
    gone_motion = registry.async_get_or_create(
        "binary_sensor",
        DOMAIN,
        "gone_0$motion",
        config_entry=entry,
        device_id=gone_device.id,
    )

    async_remove_ungated_push_entities(hass, entry)

    assert registry.async_get(keep_motion.entity_id) is not None
    assert registry.async_get(drop_motion.entity_id) is None
    assert registry.async_get(drop_doorbell.entity_id) is None
    assert registry.async_get(gone_motion.entity_id) is not None


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_unload_keeps_device_registry(hass) -> None:
    """Unload must not bulk-remove device registry entries."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    entry.runtime_data = ImouRuntimeData(coordinator=AsyncMock())

    device_registry = dr.async_get(hass)
    device_entry = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "device_key_1")},
        name="Front Door",
    )

    with patch(
        "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
        AsyncMock(return_value=True),
    ):
        assert await async_unload_entry(hass, entry) is True

    assert device_registry.async_get(device_entry.id) is not None


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_remove_device_updates_selected_devices(hass) -> None:
    """Removing a device persists exclusion in options so poll won't re-add it."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**USER_INPUT, PARAM_SELECTED_DEVICES: ["d1", "d2"]},
        options={PARAM_SELECTED_DEVICES: ["d1", "d2"]},
    )
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.devices_by_key = {}
    entry.runtime_data = ImouRuntimeData(
        coordinator=coordinator, selected_devices=["d1", "d2"]
    )

    device_registry = dr.async_get(hass)
    device_entry = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "d1_0")},
        serial_number="d1",
        name="Cam 1",
    )

    assert await async_remove_config_entry_device(hass, entry, device_entry) is True
    assert entry.options[PARAM_SELECTED_DEVICES] == ["d2"]
    assert entry.runtime_data.selected_devices == ["d2"]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_remove_device_materializes_allow_list_when_all_selected(hass) -> None:
    """When selection means all devices, remove persists remaining ids as allow-list."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)

    d1 = MagicMock()
    d1.device_id = "d1"
    d2 = MagicMock()
    d2.device_id = "d2"
    coordinator = MagicMock()
    coordinator.devices_by_key = {"d1_0": d1, "d2_0": d2}
    entry.runtime_data = ImouRuntimeData(coordinator=coordinator, selected_devices=None)

    device_registry = dr.async_get(hass)
    device_entry = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "d1_0")},
        serial_number="d1",
        name="Cam 1",
    )

    assert await async_remove_config_entry_device(hass, entry, device_entry) is True
    assert entry.options[PARAM_SELECTED_DEVICES] == ["d2"]
    assert entry.runtime_data.selected_devices == ["d2"]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_removing_one_channel_does_not_take_its_siblings(hass) -> None:
    """Exclusion is per account device, so one channel cannot be expressed in it.

    An NVR and a multi-lens camera arrive as one account device carrying several
    channels, each of which becomes its own device here. Excluding the account
    device would drop the siblings out of Home Assistant, losing whatever the
    user had named, placed, or automated on them.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={PARAM_SELECTED_DEVICES: ["nvr1", "d2"]},
    )
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.devices_by_key = {}
    entry.runtime_data = ImouRuntimeData(
        coordinator=coordinator, selected_devices=["nvr1", "d2"]
    )

    device_registry = dr.async_get(hass)
    channel_0 = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "nvr1_0")},
        serial_number="nvr1",
        name="Front Door",
    )
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "nvr1_1")},
        serial_number="nvr1",
        name="Driveway",
    )

    with pytest.raises(HomeAssistantError) as err:
        await async_remove_config_entry_device(hass, entry, channel_0)
    assert "Driveway" in str(err.value)
    assert entry.options[PARAM_SELECTED_DEVICES] == ["nvr1", "d2"]
    assert entry.runtime_data.selected_devices == ["nvr1", "d2"]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_another_device_is_not_mistaken_for_a_sibling_channel(hass) -> None:
    """Only channels of the same account device count; other cameras must not."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        options={PARAM_SELECTED_DEVICES: ["d1", "d2"]},
    )
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.devices_by_key = {}
    entry.runtime_data = ImouRuntimeData(
        coordinator=coordinator, selected_devices=["d1", "d2"]
    )

    device_registry = dr.async_get(hass)
    device_entry = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "d1_0")},
        serial_number="d1",
        name="Cam 1",
    )
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "d2_0")},
        serial_number="d2",
        name="Cam 2",
    )

    assert await async_remove_config_entry_device(hass, entry, device_entry) is True
    assert entry.options[PARAM_SELECTED_DEVICES] == ["d2"]


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_remove_device_without_runtime_refuses(hass) -> None:
    """Without runtime, do not rewrite 'all' into an empty allow-list."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)

    device_registry = dr.async_get(hass)
    device_entry = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "d1_0")},
        serial_number="d1",
        name="Cam 1",
    )

    with pytest.raises(HomeAssistantError):
        await async_remove_config_entry_device(hass, entry, device_entry)
    assert PARAM_SELECTED_DEVICES not in entry.options


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_remove_ghost_device_when_coordinator_map_is_empty(hass) -> None:
    """A device already gone from the account must still be removable in HA.

    selected_devices is unset (poll all). The coordinator no longer lists the
    device, so there is nothing to materialize — just allow the registry drop.
    """
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.devices_by_key = {}
    entry.runtime_data = ImouRuntimeData(coordinator=coordinator, selected_devices=None)

    device_registry = dr.async_get(hass)
    device_entry = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "d1_0")},
        serial_number="d1",
        name="Cam 1",
    )

    assert await async_remove_config_entry_device(hass, entry, device_entry) is True
    assert PARAM_SELECTED_DEVICES not in entry.options


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_update_soft_path_skips_reload(hass) -> None:
    """Changing decrypt/notify options must not unload and re-setup the entry."""
    from custom_components.imou_life import (
        async_update_options,
        options_reload_signature,
    )
    from custom_components.imou_life.const import (
        PARAM_ATTACH_DECRYPTED_THUMBNAIL,
        PARAM_NOTIFY_SERVICES,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**USER_INPUT, PARAM_WEBHOOK_ID: "wh"},
        options={PARAM_NOTIFY_SERVICES: ["notify.old"]},
    )
    entry.add_to_hass(hass)
    runtime = ImouRuntimeData(
        coordinator=MagicMock(),
        notify_services=["notify.old"],
        reload_signature=options_reload_signature(entry.options),
    )
    entry.runtime_data = runtime

    hass.config_entries.async_update_entry(
        entry,
        options={
            PARAM_NOTIFY_SERVICES: ["notify.new"],
            PARAM_ATTACH_DECRYPTED_THUMBNAIL: True,
        },
    )

    with patch(
        "homeassistant.config_entries.ConfigEntries.async_reload",
        AsyncMock(),
    ) as reload:
        await async_update_options(hass, entry)
        reload.assert_not_awaited()

    assert runtime.notify_services == ["notify.new"]
    assert runtime.reload_signature == options_reload_signature(entry.options)


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_update_soft_path_clears_pic_decoder_failed(hass) -> None:
    """Soft-saving decrypt options must allow the native decoder to be retried."""
    from custom_components.imou_life import (
        async_update_options,
        options_reload_signature,
    )
    from custom_components.imou_life.const import PARAM_ATTACH_DECRYPTED_THUMBNAIL
    from custom_components.imou_life.runtime_data import ImouRuntimeData

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**USER_INPUT, PARAM_WEBHOOK_ID: "wh"},
        options={},
    )
    entry.add_to_hass(hass)
    runtime = ImouRuntimeData(
        coordinator=MagicMock(),
        pic_decoder_failed=True,
        pic_decoder=object(),
        reload_signature=options_reload_signature(entry.options),
    )
    entry.runtime_data = runtime
    hass.config_entries.async_update_entry(
        entry,
        options={PARAM_ATTACH_DECRYPTED_THUMBNAIL: True},
    )

    with patch(
        "homeassistant.config_entries.ConfigEntries.async_reload",
        AsyncMock(),
    ) as reload:
        await async_update_options(hass, entry)
        reload.assert_not_awaited()

    assert runtime.pic_decoder_failed is False
    assert runtime.pic_decoder is None


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_options_update_reloads_when_polling_changes(hass) -> None:
    """Changing the poll interval still forces a full reload."""
    from custom_components.imou_life import (
        async_update_options,
        options_reload_signature,
    )
    from custom_components.imou_life.const import PARAM_UPDATE_INTERVAL

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**USER_INPUT, PARAM_WEBHOOK_ID: "wh"},
        options={PARAM_UPDATE_INTERVAL: 300},
    )
    entry.add_to_hass(hass)
    runtime = ImouRuntimeData(
        coordinator=MagicMock(),
        reload_signature=options_reload_signature(entry.options),
    )
    entry.runtime_data = runtime
    hass.config_entries.async_update_entry(entry, options={PARAM_UPDATE_INTERVAL: 120})

    with patch(
        "homeassistant.config_entries.ConfigEntries.async_reload",
        AsyncMock(),
    ) as reload:
        await async_update_options(hass, entry)
        reload.assert_awaited_once_with(entry.entry_id)
