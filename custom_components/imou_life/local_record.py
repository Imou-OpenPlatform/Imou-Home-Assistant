"""Post-alarm local clips via camera.record (cloud HLS)."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import (
    DEFAULT_LOCAL_RECORD_DURATION,
    DOMAIN,
    PARAM_LOCAL_EVENT_RECORD,
    PARAM_LOCAL_RECORD_DURATION,
    PARAM_LOCAL_RECORD_PATH,
    imou_life_device_key_from_ids,
)
from .runtime_data import get_runtime_data

_LOGGER = logging.getLogger(__name__)


def _switch_is_on(hass: HomeAssistant, device_key: str) -> bool:
    """Return True when this camera's local-record switch is on."""
    entity_id = er.async_get(hass).async_get_entity_id(
        "switch", DOMAIN, f"{device_key}${PARAM_LOCAL_EVENT_RECORD}"
    )
    if not entity_id:
        return False
    state = hass.states.get(entity_id)
    return state is not None and state.state == STATE_ON


def _camera_entity_id(hass: HomeAssistant, device_key: str) -> str | None:
    """Return the live camera entity for this Imou channel."""
    return er.async_get(hass).async_get_entity_id(
        "camera", DOMAIN, f"{device_key}$camera"
    )


async def async_maybe_record_from_alarm(
    hass: HomeAssistant,
    entry: ConfigEntry,
    event_data: dict,
) -> None:
    """Start camera.record when this channel's local-record switch is on."""
    device_key = imou_life_device_key_from_ids(
        event_data.get("device_id"),
        event_data.get("channel_id"),
        event_data.get("product_id"),
    )
    if device_key is None or not _switch_is_on(hass, device_key):
        return

    folder = str(entry.options.get(PARAM_LOCAL_RECORD_PATH) or "").strip()
    if not folder:
        _LOGGER.warning(
            "Local event recording is on for %s but no save folder is configured",
            device_key,
        )
        return

    duration = int(
        entry.options.get(PARAM_LOCAL_RECORD_DURATION, DEFAULT_LOCAL_RECORD_DURATION)
    )
    camera_id = _camera_entity_id(hass, device_key)
    if not camera_id:
        _LOGGER.warning("No camera entity for %s, skip local recording", device_key)
        return

    device_id = event_data.get("device_id") or "device"
    channel_id = event_data.get("channel_id")
    channel = "x" if channel_id is None else str(channel_id)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = str(Path(folder) / f"{device_id}_{channel}_{stamp}.mp4")
    if not hass.config.is_allowed_path(filename):
        _LOGGER.warning(
            "Local recording path is not allowlisted: %s",
            filename,
        )
        return

    runtime = get_runtime_data(entry)
    if runtime is not None:
        started = runtime.local_record_started_at.get(device_key, 0.0)
        now = hass.loop.time()
        if now - started < duration:
            _LOGGER.debug("Skip overlapping local record for %s", device_key)
            return
        runtime.local_record_started_at[device_key] = now

    await hass.services.async_call(
        "camera",
        "record",
        {
            "entity_id": camera_id,
            "filename": filename,
            "duration": duration,
            "lookback": 0,
        },
        blocking=False,
    )
    _LOGGER.info("Started local event recording for %s -> %s", camera_id, filename)
