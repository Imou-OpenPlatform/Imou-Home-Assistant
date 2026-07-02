"""Runtime data stored on Imou config entries."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .coordinator import ImouDataUpdateCoordinator


@dataclass
class ImouRuntimeData:
    """Data attached to a config entry at runtime."""

    coordinator: ImouDataUpdateCoordinator
    push_enabled: bool = False
    selected_devices: list[str] = field(default_factory=list)
    notify_services: list[str] = field(default_factory=list)
