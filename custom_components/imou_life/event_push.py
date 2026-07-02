"""Event push / webhook setup for Imou Life."""

from __future__ import annotations

import logging
import uuid
from typing import Literal

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from pyimouapi.openapi import ImouOpenApiClient

from .const import (
    DOMAIN,
    PARAM_BASE_PUSH,
    PARAM_ENABLE_EVENT_PUSH,
    PARAM_EVENT_PUSH_TYPES,
    PARAM_NOTIFY_SERVICES,
    PARAM_SELECTED_DEVICES,
    PARAM_WEBHOOK_ID,
    PARAM_WEBHOOK_URL,
)
from .webhook import async_register_imou_webhook, async_unregister_imou_webhook

_LOGGER = logging.getLogger(__name__)


async def async_setup_event_push(
    hass: HomeAssistant,
    entry: ConfigEntry,
    imou_client: ImouOpenApiClient,
) -> tuple[str, str]:
    """Register webhook and optionally enable Imou message callback."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["notify_services"] = []
    hass.data[DOMAIN]["push_enabled"] = bool(entry.options.get(PARAM_ENABLE_EVENT_PUSH))
    hass.data[DOMAIN]["selected_devices"] = entry.options.get(
        PARAM_SELECTED_DEVICES
    ) or entry.data.get(PARAM_SELECTED_DEVICES, [])

    webhook_id = entry.data.get(PARAM_WEBHOOK_ID, "")
    if not webhook_id:
        webhook_id = uuid.uuid4().hex
        entry.data[PARAM_WEBHOOK_ID] = webhook_id
        await hass.config_entries.async_update_entry(entry, data=entry.data)
    generated_url = async_register_imou_webhook(hass, webhook_id)

    if entry.options.get(PARAM_ENABLE_EVENT_PUSH):
        await _async_set_message_callback(entry, imou_client, "on", generated_url)

    raw_services = entry.options.get(PARAM_NOTIFY_SERVICES, "")
    if raw_services:
        hass.data[DOMAIN]["notify_services"] = [
            s.strip() for s in raw_services.split(",") if s.strip()
        ]

    return webhook_id, generated_url


async def async_teardown_event_push(
    hass: HomeAssistant,
    entry: ConfigEntry,
    imou_client: ImouOpenApiClient | None = None,
) -> None:
    """Disable Imou message callback and unregister webhook."""
    webhook_id = entry.data.get(PARAM_WEBHOOK_ID, "")
    if entry.options.get(PARAM_ENABLE_EVENT_PUSH) and webhook_id and imou_client:
        await _async_set_message_callback(entry, imou_client, "off")
    if webhook_id:
        async_unregister_imou_webhook(hass, webhook_id)


async def _async_set_message_callback(
    entry: ConfigEntry,
    imou_client: ImouOpenApiClient,
    status: Literal["on", "off"],
    generated_webhook_url: str | None = None,
) -> None:
    """Register or unregister Imou Open Platform message callback."""
    callback_url = entry.options.get(PARAM_WEBHOOK_URL) or generated_webhook_url
    callback_flags = entry.options.get(PARAM_EVENT_PUSH_TYPES, [])
    base_push = entry.options.get(PARAM_BASE_PUSH, "2")
    if status == "on":
        if not callback_url:
            _LOGGER.error(
                "Cannot enable Imou event push: no webhook URL available. "
                "Please set webhook_url in integration options or configure HA "
                "external URL."
            )
            return
        try:
            await imou_client.async_set_message_callback(
                status="on",
                callback_url=callback_url,
                callback_flag=callback_flags if callback_flags else None,
                base_push=base_push,
            )
            _LOGGER.info(
                "Imou message callback set to %s (url=%s)",
                status,
                callback_url,
            )
        except Exception:
            _LOGGER.exception("Failed to set Imou message callback")
    else:
        try:
            await imou_client.async_set_message_callback(
                status="off",
                base_push=base_push,
            )
            _LOGGER.info(
                "Imou message callback set to %s (url=%s)",
                status,
                callback_url or "N/A",
            )
        except Exception:
            _LOGGER.exception("Failed to set Imou message callback")
