"""Provides the Imou DataUpdateCoordinator."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable
from datetime import timedelta
from time import monotonic

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pyimouapi.capability.catalog import CapabilitySet
from pyimouapi.device import compose_iot_device_id
from pyimouapi.exceptions import ImouException, InvalidAppIdOrSecretException
from pyimouapi.ha_device import (
    ImouHaDevice,
    ImouHaDeviceManager,
    device_is_iot,
)

from .const import (
    DEFAULT_UPDATE_INTERVAL,
    DISCOVERY_INTERVAL,
    DOMAIN,
    EVENT_PUSH_TYPE_CAPABILITY_EVENT,
    PARAM_ENABLE_POLLING,
    PARAM_UPDATE_INTERVAL,
    UPDATE_TIMEOUT,
    imou_life_device_key,
)
from .devices import (
    async_register_imou_devices,
    registry_row_for_key_on_entry,
)
from .helpers import (
    event_push_type_active,
    get_excluded_device_ids,
    get_selected_device_ids,
)
from .pic_thumbnail import async_remember_picture_passwords
from .repairs import async_delete_quota_issue, async_notify_imou_api_error
from .runtime_data import ImouRuntimeData, get_runtime_data

_LOGGER = logging.getLogger(__name__)


def _device_identity(
    device: ImouHaDevice,
) -> tuple[str, str | None, str, str] | None:
    """Return name, model, and firmware when they are real strings."""
    values = (
        device.device_name,
        device.channel_name,
        device.model,
        device.swversion,
    )
    if not all(value is None or isinstance(value, str) for value in values):
        return None
    name, channel_name, model, swversion = values
    if (
        not isinstance(name, str)
        or not isinstance(model, str)
        or not isinstance(swversion, str)
    ):
        return None
    channel = channel_name if isinstance(channel_name, str) else None
    return (name, channel, model, swversion)


def _device_ability(device: ImouHaDevice) -> tuple[str, str] | None:
    """Return ability strings, or None when the device object is not populated."""
    device_ability = device.device_ability
    channel_ability = device.channel_ability
    if not isinstance(device_ability, str) or not isinstance(channel_ability, str):
        return None
    return (device_ability, channel_ability)


class ImouDataUpdateCoordinator(DataUpdateCoordinator[None]):
    """Coordinates polling Imou device status from the cloud."""

    # The base class allows a coordinator without an entry; this one is always
    # built for one, and everything below reads it.
    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        device_manager: ImouHaDeviceManager,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize ImouDataUpdateCoordinator."""
        enable_polling = config_entry.options.get(PARAM_ENABLE_POLLING, True)
        update_interval_seconds = config_entry.options.get(
            PARAM_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
        )
        update_interval = (
            timedelta(seconds=update_interval_seconds) if enable_polling else None
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=update_interval,
            always_update=True,
        )
        self._device_manager = device_manager
        self.devices_by_key: dict[str, ImouHaDevice] = {}
        self._devices_initialized = False
        self._last_discovery: float | None = None
        self._v2_state_snapshots: set[str] = set()
        self.new_device_callbacks: list[Callable[[list[ImouHaDevice]], None]] = []

    @property
    def devices(self) -> list[ImouHaDevice]:
        """Devices discovered for this config entry."""
        return list(self.devices_by_key.values())

    @property
    def device_manager(self) -> ImouHaDeviceManager:
        """Underlying pyimouapi device manager."""
        return self._device_manager

    def get_device(self, device_key: str) -> ImouHaDevice | None:
        """Return the current device for device_key, if still on the account."""
        return self.devices_by_key.get(device_key)

    def _filter_devices(self, devices_list: list[ImouHaDevice]) -> list[ImouHaDevice]:
        """Apply selected-devices filter from config entry options or data."""
        selected_ids = get_selected_device_ids(self.config_entry)
        excluded = get_excluded_device_ids(self.config_entry)
        devices = devices_list
        if selected_ids is not None:
            if not selected_ids:
                devices = []
            else:
                selected_set = set(selected_ids)
                devices = [d for d in devices if d.device_id in selected_set]
        if excluded:
            devices = [d for d in devices if d.device_id not in excluded]
        if len(devices) != len(devices_list):
            _LOGGER.debug(
                "Device filter active: %d/%d devices selected for polling",
                len(devices),
                len(devices_list),
            )
        return devices

    def _discovery_is_due(self) -> bool:
        """Whether it is time to look for devices added to or gone from the account."""
        if self._last_discovery is None:
            return True
        return monotonic() - self._last_discovery >= DISCOVERY_INTERVAL

    async def _async_discover_devices(self) -> None:
        """Refresh which devices the account holds.

        Entity registration uses ``device.capability.list``.
        """
        _LOGGER.debug("Listing Imou devices")
        first_discovery = not self._devices_initialized
        selected = get_selected_device_ids(self.config_entry)
        excluded = get_excluded_device_ids(self.config_entry)
        configure_ids = None if selected is None else set(selected) - excluded
        try:
            async with asyncio.timeout(UPDATE_TIMEOUT):
                fresh_devices = await self._device_manager.async_get_devices(
                    configure_device_ids=configure_ids,
                    skip_device_ids=excluded,
                )
        except TimeoutError as err:
            if first_discovery:
                raise UpdateFailed(f"Timeout while fetching data: {err}") from err
            # Same as a later listing error: keep the entities we already have.
            _LOGGER.warning("Timeout while listing Imou devices: %s", err)
            return
        except InvalidAppIdOrSecretException as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="invalid_auth",
            ) from err
        except ImouException as err:
            async_notify_imou_api_error(self.hass, self.config_entry, err)
            if first_discovery:
                raise UpdateFailed(
                    f"Error fetching Imou devices: {err.message or err}"
                ) from err
            # Discovery runs on its own slow clock, so a blip here says nothing
            # about the devices already known. Leaving the clock untouched
            # retries on the next poll instead of blanking every entity for
            # the rest of the interval.
            _LOGGER.warning("Could not list Imou devices: %s", err.message or err)
            return

        self._last_discovery = monotonic()
        self._debug_log_capability_lists(fresh_devices)
        filtered_list = self._filter_devices(fresh_devices)
        fresh_by_key = {imou_life_device_key(d): d for d in filtered_list}

        if first_discovery:
            self._async_add_remove_devices(fresh_by_key)
            await self._async_remember_picture_passwords()
            return

        self._async_add_remove_devices(fresh_by_key)
        await self._async_refresh_known_devices(fresh_by_key)
        await self._async_remember_picture_passwords()

    def _debug_log_capability_lists(self, devices: Iterable[ImouHaDevice]) -> None:
        """Log each channel's capability.list result on the integration logger.

        The library logs the same call on ``pyimouapi``. Home Assistant only
        shows that when that logger is set to debug, so the device page is
        hard to compare with the list that actually came back.
        """
        for device in devices:
            name = device.channel_name or device.device_name
            capability = device.capability_set
            if not isinstance(capability, CapabilitySet):
                _LOGGER.debug(
                    "device.capability.list missing %s ch%s (%s) ability=%s",
                    device.device_id,
                    device.channel_id,
                    name,
                    device.channel_ability,
                )
                continue
            _LOGGER.debug(
                "device.capability.list %s ch%s (%s) properties=%s services=%s "
                "events=%s switches=%s buttons=%s ability=%s",
                device.device_id,
                device.channel_id,
                name,
                sorted(capability.properties),
                sorted(capability.services),
                sorted(capability.events),
                sorted(device.switches),
                sorted(device.buttons),
                device.channel_ability,
            )

    async def _async_update_data(self) -> None:
        """Fetch latest device status from Imou cloud."""
        _LOGGER.debug("Polling Imou device status")
        if self._discovery_is_due():
            await self._async_discover_devices()

        devices_to_update = [
            device
            for device in self.devices_by_key.values()
            if not self._should_skip_device_update(device)
        ]
        if len(devices_to_update) < len(self.devices_by_key):
            _LOGGER.debug(
                "Skipping cloud poll for %s device(s) with all entities disabled",
                len(self.devices_by_key) - len(devices_to_update),
            )
        if not devices_to_update:
            return

        property_push = event_push_type_active(
            self.config_entry, EVENT_PUSH_TYPE_CAPABILITY_EVENT
        )
        skip_ids = (
            set(self._v2_state_snapshots) & self._iot_physical_ids()
            if property_push
            else None
        )
        try:
            async with asyncio.timeout(UPDATE_TIMEOUT):
                fetched = await self._device_manager.async_update_devices_status(
                    devices_to_update,
                    skip_iot_property_ids=skip_ids,
                )
        except InvalidAppIdOrSecretException as err:
            # Credentials can be revoked between two listings, and the status
            # calls are what notice it first now that listing is on a slow clock.
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="invalid_auth",
            ) from err
        except (TimeoutError, ImouException) as err:
            async_notify_imou_api_error(self.hass, self.config_entry, err)
            # last_update_success stays true, so entities keep the last state
            # instead of all going unavailable until the next interval.
            _LOGGER.warning(
                "Could not update Imou device status: %s",
                getattr(err, "message", None) or err,
            )
            return
        else:
            if isinstance(fetched, set) and fetched:
                self._v2_state_snapshots.update(fetched)
            async_delete_quota_issue(self.hass, self.config_entry)
            runtime = getattr(self.config_entry, "runtime_data", None)
            if runtime is not None:
                runtime.countdown.sync_all(self.hass, self)

    def _async_add_remove_devices(self, fresh_by_key: dict[str, ImouHaDevice]) -> None:
        """Add devices selected for polling and drop the rest.

        The picker still lists every account device. Anything not in this map
        leaves the Home Assistant device list, including a device deleted in
        the Imou app and one the user left unchecked.
        """
        if not self._devices_initialized:
            self.devices_by_key = fresh_by_key
            self._devices_initialized = True
            # Unload leaves registry entries in place. After reload the
            # coordinator starts empty, so the first discovery must still
            # detach devices that are not being polled.
            self._async_detach_registry_devices_missing_from(fresh_by_key)
            return

        current_keys = set(fresh_by_key)
        known_keys = set(self.devices_by_key)
        self._async_detach_registry_devices_missing_from(fresh_by_key)

        if current_keys == known_keys:
            return

        if removed_keys := known_keys - current_keys:
            _LOGGER.debug("Removed Imou device(s): %s", ", ".join(removed_keys))
            removed_devices = [
                self.devices_by_key[device_key] for device_key in removed_keys
            ]
            for device_key in removed_keys:
                del self.devices_by_key[device_key]
            self._forget_v2_state_snapshots(removed_devices)

        if new_keys := current_keys - known_keys:
            _LOGGER.debug("New Imou device(s) found: %s", ", ".join(new_keys))
            new_devices = []
            for device_key in new_keys:
                self.devices_by_key[device_key] = fresh_by_key[device_key]
                new_devices.append(fresh_by_key[device_key])
            for callback in self.new_device_callbacks:
                callback(new_devices)

    def _async_detach_registry_devices_missing_from(
        self, kept_by_key: dict[str, ImouHaDevice]
    ) -> None:
        """Detach config-entry devices that are not being polled."""
        device_registry = dr.async_get(self.hass)
        # A multi-channel device also has a row of its own, keyed by the bare
        # account device id rather than by one of its channels.
        kept_device_ids = {device.device_id for device in kept_by_key.values()}
        for device in dr.async_entries_for_config_entry(
            device_registry, self.config_entry.entry_id
        ):
            imou_keys = [
                ident for domain, ident in device.identifiers if domain == DOMAIN
            ]
            if not imou_keys:
                continue
            if any(key in kept_by_key or key in kept_device_ids for key in imou_keys):
                continue
            device_registry.async_update_device(
                device_id=device.id,
                remove_config_entry_id=self.config_entry.entry_id,
            )

    async def _async_remember_picture_passwords(self) -> None:
        """Read picture passwords for devices that do not have one yet."""
        runtime = get_runtime_data(self.config_entry)
        if runtime is None:
            return
        await async_remember_picture_passwords(runtime, self.devices_by_key.values())

    async def _async_refresh_known_devices(
        self, fresh_by_key: dict[str, ImouHaDevice]
    ) -> None:
        """Update identity and merge complete capability snapshots."""
        identity_changed: list[ImouHaDevice] = []
        refreshed: dict[str, ImouHaDevice] = {}
        for key, fresh in fresh_by_key.items():
            current = self.devices_by_key.get(key)
            if current is None:
                continue
            token = fresh.play_token_v2
            if isinstance(token, str) and token:
                current.set_play_token_v2(token)
            source = fresh.source
            if isinstance(source, str) and source:
                current.set_source(source)
            device_caps = fresh.device_capability_ids
            if isinstance(device_caps, frozenset) and (
                device_caps or isinstance(fresh.capability_ids, frozenset)
            ):
                current.set_device_capability_ids(device_caps)
            if fresh.cs_status is not None:
                current.set_cs_status(fresh.cs_status)
            if fresh.ai_insight_status is not None:
                current.set_ai_insight_status(fresh.ai_insight_status)
            current_identity = _device_identity(current)
            fresh_identity = _device_identity(fresh)
            if (
                current_identity is not None
                and fresh_identity is not None
                and current_identity != fresh_identity
            ):
                current.apply_identity(fresh)
                identity_changed.append(current)
            current_ability = _device_ability(current)
            fresh_ability = _device_ability(fresh)
            if (
                current_ability is not None
                and fresh_ability is not None
                and current_ability != fresh_ability
            ):
                current.set_device_ability(fresh.device_ability)
                if isinstance(fresh.channel_ability, str):
                    current.set_channel_ability(fresh.channel_ability)
            if (
                isinstance(fresh.capability_ids, frozenset)
                or isinstance(fresh.capability_set, CapabilitySet)
            ) and current.capability_snapshot != fresh.capability_snapshot:
                removed = current.replace_capabilities(fresh)
                self._async_remove_entity_suffixes(key, removed)
                refreshed[key] = current
        if identity_changed:
            async_register_imou_devices(self.hass, self.config_entry, identity_changed)
        if refreshed:
            ready = list(refreshed.values())
            for callback in self.new_device_callbacks:
                callback(ready)

    def _forget_v2_state_snapshots(self, devices: Iterable[ImouHaDevice]) -> None:
        """Allow a device that left to fetch a state snapshot when it returns."""
        remaining = {
            compose_iot_device_id(
                device.device_id,
                device.parent_device_id,
                device.parent_product_id,
            )
            for device in self.devices_by_key.values()
        }
        for device in devices:
            physical_id = compose_iot_device_id(
                device.device_id,
                device.parent_device_id,
                device.parent_product_id,
            )
            if physical_id not in remaining:
                self._v2_state_snapshots.discard(physical_id)

    def _async_remove_entity_suffixes(
        self, device_key: str, suffixes: list[str]
    ) -> None:
        """Drop registry rows for entity types this device no longer exposes."""
        if not suffixes:
            return
        wanted = {f"{device_key}${suffix}" for suffix in suffixes}
        registry = er.async_get(self.hass)
        for entity in list(
            er.async_entries_for_config_entry(registry, self.config_entry.entry_id)
        ):
            if entity.unique_id in wanted:
                registry.async_remove(entity.entity_id)

    def _iot_physical_ids(self) -> set[str]:
        """Physical ids whose entity values arrive on capability pushes."""
        return {
            compose_iot_device_id(
                device.device_id,
                device.parent_device_id,
                device.parent_product_id,
            )
            for device in self.devices_by_key.values()
            if device_is_iot(device)
        }

    def _should_skip_device_update(self, device: ImouHaDevice) -> bool:
        """Skip cloud status poll when every HA entity for this device is disabled."""
        entry_id = self.config_entry.entry_id
        device_registry = dr.async_get(self.hass)
        entity_registry = er.async_get(self.hass)
        device_key = imou_life_device_key(device)
        device_entry = registry_row_for_key_on_entry(
            device_registry, entry_id, device_key
        )
        if device_entry is None:
            return False
        entries = [
            e
            for e in er.async_entries_for_device(
                entity_registry,
                device_entry.id,
                include_disabled_entities=True,
            )
            if e.config_entry_id == entry_id
        ]
        if not entries:
            return False
        return all(e.disabled_by is not None for e in entries)


type ImouConfigEntry = ConfigEntry[ImouRuntimeData]
