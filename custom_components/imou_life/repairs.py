"""Repair issues for Imou Life."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

ISSUE_EVENT_PUSH_NO_URL = "event_push_no_external_url"
ISSUE_EVENT_PUSH_CALLBACK = "event_push_callback_failed"


def _issue_id(key: str, entry_id: str) -> str:
    return f"{key}_{entry_id}"


@callback
def async_create_event_push_no_url_issue(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Warn when event push is enabled without a usable callback URL."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id(ISSUE_EVENT_PUSH_NO_URL, entry.entry_id),
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_EVENT_PUSH_NO_URL,
    )


@callback
def async_create_event_push_callback_failed_issue(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Warn when Imou message callback registration fails."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id(ISSUE_EVENT_PUSH_CALLBACK, entry.entry_id),
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_EVENT_PUSH_CALLBACK,
    )


@callback
def async_delete_event_push_issues(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clear event push repair issues for a config entry."""
    for key in (ISSUE_EVENT_PUSH_NO_URL, ISSUE_EVENT_PUSH_CALLBACK):
        ir.async_delete_issue(hass, DOMAIN, _issue_id(key, entry.entry_id))
