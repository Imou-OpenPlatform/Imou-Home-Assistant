"""Event push / webhook setup for Imou Life."""

from __future__ import annotations

import logging
from functools import partial
from urllib.parse import urlparse, urlunparse

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from pyimouapi.openapi import ImouOpenApiClient

from .const import (
    BASE_PUSH_ALWAYS,
    PARAM_API_URL,
    PARAM_APP_ID,
    PARAM_APP_SECRET,
    PARAM_ENABLE_EVENT_PUSH,
    PARAM_WEBHOOK_ID,
    PARAM_WEBHOOK_URL,
)
from .helpers import (
    get_selected_device_ids,
    notify_services_from_entry_options,
)
from .repairs import (
    async_create_event_push_no_url_issue,
    async_delete_event_push_issues,
)
from .runtime_data import ImouRuntimeData
from .webhook import (
    async_register_imou_webhook,
    async_unregister_imou_webhook,
)

_LOGGER = logging.getLogger(__name__)


def _redact_callback_url(url: str) -> str:
    """Hide the webhook id that authenticates the callback URL."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if not path:
        return url
    prefix, separator, last = path.rpartition("/")
    if not separator or not last:
        return url
    hidden = f"{last[:4]}…" if len(last) > 4 else "…"
    new_path = f"{prefix}/{hidden}"
    if parsed.path.endswith("/"):
        new_path += "/"
    return urlunparse(parsed._replace(path=new_path, query="", fragment=""))


async def async_setup_event_push(
    hass: HomeAssistant,
    entry: ConfigEntry,
    imou_client: ImouOpenApiClient,
    runtime: ImouRuntimeData,
) -> tuple[str, str]:
    """Register webhook and optionally enable Imou message callback."""
    async_delete_event_push_issues(hass, entry)
    runtime.notify_services = []
    runtime.push_enabled = bool(entry.options.get(PARAM_ENABLE_EVENT_PUSH))
    runtime.selected_devices = get_selected_device_ids(entry)

    webhook_id = entry.data.get(PARAM_WEBHOOK_ID, "")
    if not webhook_id:
        _LOGGER.error(
            "Config entry %s missing webhook_id; event push cannot be registered",
            entry.entry_id,
        )
        return webhook_id, ""

    generated_url = async_register_imou_webhook(hass, webhook_id)
    entry.async_on_unload(partial(async_unregister_imou_webhook, hass, webhook_id))
    if runtime.push_enabled and not (
        generated_url or str(entry.options.get(PARAM_WEBHOOK_URL) or "").strip()
    ):
        async_create_event_push_no_url_issue(hass, entry)

    runtime.notify_services = notify_services_from_entry_options(hass, entry.options)

    return webhook_id, generated_url


async def async_teardown_event_push(
    hass: HomeAssistant,
    entry: ConfigEntry,
    imou_client: ImouOpenApiClient | None = None,
) -> None:
    """Unregister webhook without touching cloud subscription."""
    del imou_client
    async_delete_event_push_issues(hass, entry)
    webhook_id = entry.data.get(PARAM_WEBHOOK_ID, "")
    if webhook_id:
        async_unregister_imou_webhook(hass, webhook_id)


def _callback_belongs_to_entry(callback_url: str, webhook_id: str) -> bool:
    """Return whether the cloud callback points at this entry's webhook."""
    if not callback_url or not webhook_id:
        return False
    return (
        urlparse(callback_url).path.rstrip("/").endswith(f"/api/webhook/{webhook_id}")
    )


async def async_disable_cloud_callback(
    hass: HomeAssistant, entry: ConfigEntry, client: ImouOpenApiClient | None = None
) -> None:
    """Turn off the Imou callback only when it still posts to this entry."""
    webhook_id = entry.data.get(PARAM_WEBHOOK_ID, "")
    if not webhook_id:
        return
    owned = client is None
    if client is None:
        client = ImouOpenApiClient(
            entry.data[PARAM_APP_ID],
            entry.data[PARAM_APP_SECRET],
            entry.data[PARAM_API_URL],
        )
    try:
        data = await client.async_get_message_callback()
        callback_url = str(data.get("callbackUrl") or "")
        if not _callback_belongs_to_entry(callback_url, webhook_id):
            _LOGGER.debug("Leaving a different Imou callback subscription in place")
            return
        await client.async_set_message_callback(
            status="off", base_push=BASE_PUSH_ALWAYS
        )
    except Exception:
        _LOGGER.warning("Could not disable the Imou message callback", exc_info=True)
    finally:
        if owned:
            await client.async_close()
