"""Tests for the entity-adding helper shared by every Imou platform."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from custom_components.imou_life.coordinator import ImouDataUpdateCoordinator
from custom_components.imou_life.entity import async_add_imou_entities
from pyimouapi.ha_device import ImouHaDevice


class FakeEntity:
    """Records the arguments a platform would construct an entity with."""

    def __init__(
        self,
        coordinator: ImouDataUpdateCoordinator,
        entry: Any,
        entity_key: Any,
        device: ImouHaDevice,
    ) -> None:
        """Store the construction arguments."""
        self.entity_key = entity_key
        self.device = device


def make_device(device_id: str) -> ImouHaDevice:
    """Return a device whose registry key is derived from its channel."""
    device = MagicMock(spec=ImouHaDevice)
    device.device_id = device_id
    device.channel_id = "0"
    device.product_id = None
    return device


def make_entry(devices: list[ImouHaDevice]) -> MagicMock:
    """Return a config entry whose coordinator reports the given devices."""
    coordinator = MagicMock()
    coordinator.devices = devices
    coordinator.new_device_callbacks = []
    entry = MagicMock()
    entry.runtime_data.coordinator = coordinator
    entry.async_on_unload = MagicMock()
    return entry


def iter_one_entity_per_device(
    coordinator: ImouDataUpdateCoordinator,
) -> list[tuple[str, ImouHaDevice]]:
    """Pair every known device with a single supported entity type."""
    return [("power", device) for device in coordinator.devices]


def added_devices(async_add_entities: MagicMock) -> list[list[str]]:
    """Return the device ids passed to each async_add_entities call."""
    return [
        [entity.device.device_id for entity in call.args[0]]
        for call in async_add_entities.call_args_list
    ]


def test_entities_are_added_for_the_known_devices() -> None:
    """Setup adds entities for whatever the coordinator already reported."""
    entry = make_entry([make_device("dev1"), make_device("dev2")])
    async_add_entities = MagicMock()

    async_add_imou_entities(
        entry, async_add_entities, FakeEntity, iter_one_entity_per_device
    )

    assert added_devices(async_add_entities) == [["dev1", "dev2"]]


def test_later_discovery_adds_only_the_new_device() -> None:
    """A device discovered on a later poll must not re-add existing entities."""
    entry = make_entry([make_device("dev1")])
    async_add_entities = MagicMock()
    async_add_imou_entities(
        entry, async_add_entities, FakeEntity, iter_one_entity_per_device
    )
    coordinator = entry.runtime_data.coordinator
    new_device = make_device("dev2")
    coordinator.devices = [*coordinator.devices, new_device]

    coordinator.new_device_callbacks[0]([new_device])

    assert added_devices(async_add_entities) == [["dev1"], ["dev2"]]


def test_unload_removes_the_discovery_callback() -> None:
    """A reloaded entry must not leave a stale callback adding duplicates."""
    entry = make_entry([make_device("dev1")])

    async_add_imou_entities(entry, MagicMock(), FakeEntity, iter_one_entity_per_device)
    coordinator = entry.runtime_data.coordinator
    assert len(coordinator.new_device_callbacks) == 1

    entry.async_on_unload.call_args.args[0]()

    assert coordinator.new_device_callbacks == []


def test_removal_is_safe_when_already_gone() -> None:
    """Removing twice must not raise, since unload can run after a teardown."""
    entry = make_entry([make_device("dev1")])
    async_add_imou_entities(entry, MagicMock(), FakeEntity, iter_one_entity_per_device)
    remove = entry.async_on_unload.call_args.args[0]

    remove()
    remove()

    assert entry.runtime_data.coordinator.new_device_callbacks == []
