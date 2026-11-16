"""Repair issues for Imou Life."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from pyimouapi.const import (
    ERROR_CODE_INVALID_APP,
    ERROR_CODE_INVALID_SIGN,
    ERROR_CODE_NO_PERMISSION,
    ERROR_CODE_OPEN_API_QUOTA_EXCEEDED,
    ERROR_CODE_TOKEN_OVERDUE,
)
from pyimouapi.exceptions import (
    InvalidAppIdOrSecretException,
    RequestFailedException,
)

from .const import DOMAIN, PARAM_API_URL, api_url_region_from_value

ISSUE_EVENT_PUSH_NO_URL = "event_push_no_external_url"
ISSUE_EVENT_PUSH_CALLBACK = "event_push_callback_failed"
ISSUE_OPEN_API_QUOTA = "open_api_quota_exceeded"
ISSUE_RECORD_PLAYBACK = "record_playback"

_RECORD_PLAYBACK_API_CODES = frozenset(
    {
        ERROR_CODE_INVALID_APP,
        ERROR_CODE_INVALID_SIGN,
        ERROR_CODE_NO_PERMISSION,
        ERROR_CODE_TOKEN_OVERDUE,
    }
)
_RESOURCES_CN = "https://open.imou.com/consoleNew/resourceManage/myResource"
_RESOURCES_OVERSEAS = "https://open.imoulife.com/consoleNew/resourceManage/myResource"


class RecordPlaybackEmptySourceError(ValueError):
    """Raised when Imou returns no playable recording source."""


def _issue_id(key: str, entry_id: str) -> str:
    return f"{key}_{entry_id}"


def is_quota_exceeded(error: BaseException) -> bool:
    """Return True when the Open Platform monthly call limit is used up."""
    return (
        isinstance(error, RequestFailedException)
        and error.api_code == ERROR_CODE_OPEN_API_QUOTA_EXCEEDED
    )


def _quota_learn_more_url(entry: ConfigEntry) -> str:
    region = api_url_region_from_value(str(entry.data.get(PARAM_API_URL, "")))
    return _RESOURCES_CN if region == "cn" else _RESOURCES_OVERSEAS


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
def async_create_quota_exceeded_issue(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Explain that the App ID has no Open Platform calls left this month."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id(ISSUE_OPEN_API_QUOTA, entry.entry_id),
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=ISSUE_OPEN_API_QUOTA,
        learn_more_url=_quota_learn_more_url(entry),
    )


@callback
def async_notify_imou_api_error(
    hass: HomeAssistant, entry: ConfigEntry, error: BaseException
) -> None:
    """Create the quota repair when the cloud refuses the call for that reason."""
    if is_quota_exceeded(error):
        async_create_quota_exceeded_issue(hass, entry)


@callback
def async_delete_quota_issue(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clear the quota repair after calls succeed again."""
    ir.async_delete_issue(hass, DOMAIN, _issue_id(ISSUE_OPEN_API_QUOTA, entry.entry_id))


@callback
def async_delete_event_push_issues(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clear event push repair issues for a config entry."""
    for key in (ISSUE_EVENT_PUSH_NO_URL, ISSUE_EVENT_PUSH_CALLBACK):
        ir.async_delete_issue(hass, DOMAIN, _issue_id(key, entry.entry_id))


@callback
def async_create_record_playback_issue(
    hass: HomeAssistant, entry_id: str, error: BaseException
) -> None:
    """Warn when a stable account failure prevents recording playback."""
    if isinstance(
        error, (RecordPlaybackEmptySourceError, InvalidAppIdOrSecretException)
    ):
        pass
    elif not isinstance(error, RequestFailedException) or not (
        error.api_code in _RECORD_PLAYBACK_API_CODES or error.status_code in (401, 403)
    ):
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id(ISSUE_RECORD_PLAYBACK, entry_id),
        is_fixable=False,
        severity=ir.IssueSeverity.ERROR,
        translation_key=ISSUE_RECORD_PLAYBACK,
    )


@callback
def async_delete_record_playback_issue(hass: HomeAssistant, entry_id: str) -> None:
    """Clear the recording playback issue after playback succeeds."""
    ir.async_delete_issue(hass, DOMAIN, _issue_id(ISSUE_RECORD_PLAYBACK, entry_id))
