"""Device registry rows for Imou devices, and the links between them."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntry, DeviceInfo
from pyimouapi.ha_device import ImouHaDevice

from .const import DOMAIN, imou_life_device_key, imou_life_device_keys_from_ids


def multi_channel_device_ids(devices: Iterable[ImouHaDevice]) -> set[str]:
    """Return account device ids that Home Assistant splits into channels.

    An NVR, and a camera with more than one lens, arrives as a single account
    device carrying several channels, and every channel becomes its own Home
    Assistant device.
    """
    channels_by_id: dict[str, set[str]] = {}
    for device in devices:
        if device.channel_id is None:
            continue
        channels_by_id.setdefault(device.device_id, set()).add(str(device.channel_id))
    return {
        device_id for device_id, channels in channels_by_id.items() if len(channels) > 1
    }


def parent_device_key(
    devices: Sequence[ImouHaDevice], device: ImouHaDevice
) -> str | None:
    """Return the registry key of the device this one belongs to, or None.

    An accessory paired to a gateway carries its parent's ids, and is only
    linked when that gateway is one of the devices here; a channel of a
    multi-channel device is linked to the account device it is part of.
    """
    own_key = imou_life_device_key(device)
    if device.parent_device_id:
        known = {imou_life_device_key(item) for item in devices}
        for candidate in imou_life_device_keys_from_ids(
            device.parent_device_id, None, device.parent_product_id
        ):
            if candidate != own_key and candidate in known:
                return candidate
        return None
    if device.channel_id is None:
        return None
    if device.device_id in multi_channel_device_ids(devices):
        return device.device_id
    return None


def imou_device_info(device: ImouHaDevice, parent_key: str | None = None) -> DeviceInfo:
    """Return the registry row for one channel or accessory."""
    info = DeviceInfo(
        identifiers={(DOMAIN, imou_life_device_key(device))},
        name=device.channel_name or device.device_name,
        manufacturer=device.manufacturer,
        model=device.model,
        sw_version=device.swversion,
        serial_number=device.device_id,
    )
    if parent_key is not None:
        info["via_device"] = (DOMAIN, parent_key)
    return info


def is_account_device_row(entry: DeviceEntry) -> bool:
    """Return True for the row standing for a whole multi-channel device.

    Channels and accessories carry a suffixed identifier, so only the account
    device's own row has an identifier equal to its serial number.
    """
    serial = entry.serial_number
    if not serial:
        return False
    return any(
        domain == DOMAIN and ident == serial for domain, ident in entry.identifiers
    )


@callback
def async_register_imou_devices(
    hass: HomeAssistant, entry: ConfigEntry, devices: Sequence[ImouHaDevice]
) -> None:
    """Create the registry rows for these devices, parents first.

    Platforms create a row for whatever device an entity belongs to, but a row
    can only point at a parent that already exists, and nothing says a gateway
    is set up before its accessories. Registering here, before the platforms
    run, gives every link something to resolve.
    """
    registry = dr.async_get(hass)
    multi_channel = multi_channel_device_ids(devices)
    seen: set[str] = set()
    for device in devices:
        if device.device_id not in multi_channel or device.device_id in seen:
            continue
        seen.add(device.device_id)
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, device.device_id)},
            name=device.device_name,
            manufacturer=device.manufacturer,
            model=device.model,
            sw_version=device.swversion,
            serial_number=device.device_id,
        )
    for device in sorted(devices, key=lambda item: bool(item.parent_device_id)):
        parent = parent_device_key(devices, device)
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, imou_life_device_key(device))},
            name=device.channel_name or device.device_name,
            manufacturer=device.manufacturer,
            model=device.model,
            sw_version=device.swversion,
            serial_number=device.device_id,
            via_device=(DOMAIN, parent) if parent is not None else None,
        )
