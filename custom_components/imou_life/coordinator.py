"""Provides the Imou DataUpdateCoordinator."""

from __future__ import annotations

import asyncio
import inspect
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
from pyimouapi.exceptions import ImouException, InvalidAppIdOrSecretException
from pyimouapi.ha_device import ImouHaDevice, ImouHaDeviceManager

from .const import (
    DEFAULT_UPDATE_INTERVAL,
    DISCOVERY_INTERVAL,
    DOMAIN,
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
    get_excluded_device_ids,
    get_selected_device_ids,
    iot_property_push_active,
)
from .repairs import async_delete_quota_issue, async_notify_imou_api_error
from .runtime_data import ImouRuntimeData

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
        self._iot_detail_fetched: set[str] = set()
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

        Ability-ref detail calls are only needed to register entities for a
        device Home Assistant has not seen yet. A rediscovery that finds the
        same keys therefore lists without spending those calls; when something
        new appears, only that device's ids are queried again.
        """
        _LOGGER.debug("Listing Imou devices")
        first_discovery = not self._devices_initialized
        try:
            async with asyncio.timeout(UPDATE_TIMEOUT):
                fresh_devices = await self._device_manager.async_get_devices(
                    fetch_ability_refs=first_discovery
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
        account_by_key = {imou_life_device_key(d): d for d in fresh_devices}
        filtered_list = self._filter_devices(fresh_devices)
        fresh_by_key = {imou_life_device_key(d): d for d in filtered_list}

        if first_discovery:
            await self._async_prefetch_event_maps(filtered_list)
            self._async_add_remove_devices(fresh_by_key, account_by_key)
            return

        new_keys = set(fresh_by_key) - set(self.devices_by_key)
        if new_keys:
            new_ids = {fresh_by_key[key].device_id for key in new_keys}
            try:
                async with asyncio.timeout(UPDATE_TIMEOUT):
                    detailed = await self._device_manager.async_get_devices(
                        fetch_ability_refs=new_ids
                    )
            except TimeoutError as err:
                _LOGGER.warning(
                    "Timeout while loading new Imou device details: %s", err
                )
                for key in new_keys:
                    fresh_by_key.pop(key, None)
                self._last_discovery = None
                self._async_add_remove_devices(fresh_by_key, account_by_key)
                return
            except InvalidAppIdOrSecretException as err:
                raise ConfigEntryAuthFailed(
                    translation_domain=DOMAIN,
                    translation_key="invalid_auth",
                ) from err
            except ImouException as err:
                async_notify_imou_api_error(self.hass, self.config_entry, err)
                # Keep removals from the shallow list. Leave brand-new keys out
                # until detail succeeds (shallow IoT shells have no configured
                # refs yet) and rewind the discovery clock to retry soon.
                _LOGGER.warning(
                    "Could not load new Imou device details: %s", err.message or err
                )
                for key in new_keys:
                    fresh_by_key.pop(key, None)
                self._last_discovery = None
                self._async_add_remove_devices(fresh_by_key, account_by_key)
                return
            # Merge into the shallow account map; never replace it with a
            # partial list if a future library change returns only new ids.
            for device in detailed:
                account_by_key[imou_life_device_key(device)] = device
            detailed_by_key = {
                imou_life_device_key(d): d for d in self._filter_devices(detailed)
            }
            for key in new_keys:
                if key in detailed_by_key:
                    fresh_by_key[key] = detailed_by_key[key]

        if new_keys:
            await self._async_prefetch_event_maps(
                [fresh_by_key[key] for key in new_keys if key in fresh_by_key]
            )
        self._async_add_remove_devices(fresh_by_key, account_by_key)
        await self._async_refresh_known_devices(fresh_by_key)
        await self._async_retry_event_maps()

    async def _async_prefetch_event_maps(self, devices: Iterable[ImouHaDevice]) -> None:
        """Fetch product-model events so doorbell / motion can be gated at setup."""
        seen: set[str] = set()
        for device in devices:
            product_id = device.product_id
            if not product_id or product_id in seen:
                continue
            seen.add(product_id)
            await self._device_manager.delegate.async_ensure_event_map(product_id)

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

        skip_ids = (
            set(self._iot_detail_fetched)
            if iot_property_push_active(self.config_entry)
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
                self._iot_detail_fetched.update(fetched)
            async_delete_quota_issue(self.hass, self.config_entry)
            runtime = getattr(self.config_entry, "runtime_data", None)
            if runtime is not None:
                runtime.countdown.sync_all(self.hass, self)

    def _async_add_remove_devices(
        self,
        fresh_by_key: dict[str, ImouHaDevice],
        account_by_key: dict[str, ImouHaDevice],
    ) -> None:
        """Add new devices and drop ones no longer selected for polling.

        Registry detach uses the unfiltered account list: deselecting a device
        only stops polling. Devices deleted in the Imou app are still removed.
        """
        if not self._devices_initialized:
            self.devices_by_key = fresh_by_key
            self._devices_initialized = True
            # Unload leaves registry entries in place by design. After reload the
            # coordinator starts empty, so the first discovery must still detach
            # devices that are no longer on the account.
            self._async_detach_registry_devices_missing_from(account_by_key)
            return

        current_keys = set(fresh_by_key)
        known_keys = set(self.devices_by_key)
        # A device that is not being polled can still leave the account. The
        # polling set does not change, so this has to run even when the keys match.
        self._async_detach_registry_devices_missing_from(account_by_key)

        if current_keys == known_keys:
            return

        if removed_keys := known_keys - current_keys:
            _LOGGER.debug("Removed Imou device(s): %s", ", ".join(removed_keys))
            removed_devices = [
                self.devices_by_key[device_key] for device_key in removed_keys
            ]
            for device_key in removed_keys:
                del self.devices_by_key[device_key]
            self._forget_iot_details(removed_devices)

        if new_keys := current_keys - known_keys:
            _LOGGER.debug("New Imou device(s) found: %s", ", ".join(new_keys))
            new_devices = []
            for device_key in new_keys:
                self.devices_by_key[device_key] = fresh_by_key[device_key]
                new_devices.append(fresh_by_key[device_key])
            for callback in self.new_device_callbacks:
                callback(new_devices)

    def _async_detach_registry_devices_missing_from(
        self, account_by_key: dict[str, ImouHaDevice]
    ) -> None:
        """Detach config-entry devices whose Imou keys are not on the account."""
        device_registry = dr.async_get(self.hass)
        # A multi-channel device also has a row of its own, keyed by the bare
        # account device id rather than by one of its channels.
        account_device_ids = {device.device_id for device in account_by_key.values()}
        for device in dr.async_entries_for_config_entry(
            device_registry, self.config_entry.entry_id
        ):
            imou_keys = [
                ident for domain, ident in device.identifiers if domain == DOMAIN
            ]
            if not imou_keys:
                continue
            if any(
                key in account_by_key or key in account_device_ids for key in imou_keys
            ):
                continue
            device_registry.async_update_device(
                device_id=device.id,
                remove_config_entry_id=self.config_entry.entry_id,
            )

    async def _async_refresh_known_devices(
        self, fresh_by_key: dict[str, ImouHaDevice]
    ) -> None:
        """Update name and firmware, and reload abilities when they change."""
        identity_changed: list[ImouHaDevice] = []
        ability_ids: set[str] = set()
        for key, fresh in fresh_by_key.items():
            current = self.devices_by_key.get(key)
            if current is None:
                continue
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
                ability_ids.add(fresh.device_id)
        if identity_changed:
            async_register_imou_devices(self.hass, self.config_entry, identity_changed)
        if not ability_ids:
            return
        try:
            async with asyncio.timeout(UPDATE_TIMEOUT):
                detailed = await self._device_manager.async_get_devices(
                    fetch_ability_refs=ability_ids
                )
        except InvalidAppIdOrSecretException as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="invalid_auth",
            ) from err
        except (TimeoutError, ImouException) as err:
            _LOGGER.warning("Could not refresh changed Imou device abilities: %s", err)
            return
        refreshed: list[ImouHaDevice] = []
        for device in self._filter_devices(detailed):
            key = imou_life_device_key(device)
            current = self.devices_by_key.get(key)
            if current is None:
                continue
            removed = current.replace_capabilities(device)
            self._async_remove_entity_suffixes(key, removed)
            refreshed.append(current)
        for callback in self.new_device_callbacks:
            callback(refreshed)

    async def _async_retry_event_maps(self) -> None:
        """Retry product-model events that failed earlier, then add entities."""
        delegate = self._device_manager.delegate
        ensure = getattr(delegate, "async_ensure_event_map", None)
        cached = getattr(delegate, "cached_event_map", None)
        if not inspect.iscoroutinefunction(ensure) or not callable(cached):
            return
        gained: set[str] = set()
        seen: set[str] = set()
        for device in self.devices_by_key.values():
            product_id = device.product_id
            if not product_id or product_id in seen:
                continue
            seen.add(product_id)
            if cached(product_id):
                continue
            await ensure(product_id)
            if cached(product_id):
                gained.add(product_id)
        if not gained:
            return
        ready = [
            device
            for device in self.devices_by_key.values()
            if device.product_id in gained
        ]
        for callback in self.new_device_callbacks:
            callback(ready)

    def _forget_iot_details(self, devices: Iterable[ImouHaDevice]) -> None:
        """Allow a device that left to fetch detail again when it returns."""
        resolve = self._device_manager._resolve_device_id
        remaining = {resolve(device) for device in self.devices_by_key.values()}
        for device in devices:
            physical_id = resolve(device)
            if physical_id not in remaining:
                self._iot_detail_fetched.discard(physical_id)

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
