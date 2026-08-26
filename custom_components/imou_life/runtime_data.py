"""Runtime data stored on Imou config entries."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry

from .const import PUSH_DISPATCH_LIMIT

if TYPE_CHECKING:
    from pyimouapi.openapi import ImouOpenApiClient
    from pyimouapi.pic_decode import LCOpenPicDecoder

    from .coordinator import ImouDataUpdateCoordinator

_MAX_PUSH_MSG_TYPE_KEYS = 50


@dataclass
class ImouRuntimeData:
    """Data attached to a config entry at runtime."""

    coordinator: ImouDataUpdateCoordinator
    client: ImouOpenApiClient | None = None
    push_enabled: bool = False
    # None = all devices; [] = none; non-empty = allow-list
    selected_devices: list[str] | None = None
    notify_services: list[str] = field(default_factory=list)
    push_msg_type_counts: dict[str, int] = field(default_factory=dict)
    push_last_msg_type: str | None = None
    push_last_received_at: datetime | None = None
    local_record_started_at: dict[str, float] = field(default_factory=dict)
    pic_decoder: LCOpenPicDecoder | None = None
    pic_decoder_failed: bool = False
    # Options that force unload/setup when they change. Soft keys (passwords,
    # decrypt switch, notify targets, camera defaults, local record path) are
    # read from entry.options on use or refreshed in place.
    reload_signature: tuple[object, ...] = ()
    push_dispatch_sema: asyncio.Semaphore = field(
        default_factory=lambda: asyncio.Semaphore(PUSH_DISPATCH_LIMIT)
    )
    thumb_decrypt_in_flight: set[str] = field(default_factory=set)

    def record_push_msg(self, msg_type: str | None) -> None:
        """Record an accepted push for diagnostics (in-memory only)."""
        display = msg_type if msg_type is not None else "_unknown"
        count_key = display
        if (
            count_key not in self.push_msg_type_counts
            and len(self.push_msg_type_counts) >= _MAX_PUSH_MSG_TYPE_KEYS
        ):
            count_key = "_other"
        self.push_msg_type_counts[count_key] = (
            self.push_msg_type_counts.get(count_key, 0) + 1
        )
        self.push_last_msg_type = display
        self.push_last_received_at = datetime.now(UTC)


def get_runtime_data(entry: ConfigEntry) -> ImouRuntimeData | None:
    """Return runtime data, or None when the entry never finished setting up.

    Home Assistant deletes ``runtime_data`` only when an entry unloads
    cleanly, so the attribute is still there while unloading, which is when
    the client is needed to switch event push off. A setup that raised
    ``ConfigEntryNotReady`` also leaves it behind, holding a closed client;
    callers reached only from a loaded entry, which is all of them today, do
    not see that.
    """
    return getattr(entry, "runtime_data", None)
