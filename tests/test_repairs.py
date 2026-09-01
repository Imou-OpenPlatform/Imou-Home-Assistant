"""Tests for Imou Life repair issues."""

from __future__ import annotations

import pytest
from custom_components.imou_life.const import DOMAIN
from custom_components.imou_life.repairs import (
    ISSUE_EVENT_PUSH_NO_URL,
    ISSUE_OPEN_API_QUOTA,
    async_create_event_push_no_url_issue,
    async_delete_event_push_issues,
    async_delete_quota_issue,
    async_notify_imou_api_error,
    is_quota_exceeded,
)
from homeassistant.helpers import issue_registry as ir
from pyimouapi.exceptions import RequestFailedException
from pytest_homeassistant_custom_component.common import MockConfigEntry

from . import USER_INPUT


def test_is_quota_exceeded_detects_open_platform_limit() -> None:
    """The monthly call-limit code is what the repair is for."""
    assert is_quota_exceeded(
        RequestFailedException("OP1013:Call interface times exceed limit (total).")
    )
    assert is_quota_exceeded(RequestFailedException("op1013: quota"))
    assert not is_quota_exceeded(RequestFailedException("cloud down"))
    assert not is_quota_exceeded(RequestFailedException("OP1009:No permission"))
    assert not is_quota_exceeded(TimeoutError("timeout"))


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_event_push_no_url_issue_lifecycle(hass) -> None:
    """Create and delete the event push URL repair issue."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    issue_id = f"{ISSUE_EVENT_PUSH_NO_URL}_{entry.entry_id}"

    async_create_event_push_no_url_issue(hass, entry)
    await hass.async_block_till_done()

    issues = ir.async_get(hass).issues
    assert (DOMAIN, issue_id) in issues

    async_delete_event_push_issues(hass, entry)
    await hass.async_block_till_done()
    assert (DOMAIN, issue_id) not in ir.async_get(hass).issues


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_quota_issue_lifecycle(hass) -> None:
    """Quota errors raise a repair; other errors do not; success clears it."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    issue_id = f"{ISSUE_OPEN_API_QUOTA}_{entry.entry_id}"

    async_notify_imou_api_error(hass, entry, RequestFailedException("cloud down"))
    await hass.async_block_till_done()
    assert (DOMAIN, issue_id) not in ir.async_get(hass).issues

    async_notify_imou_api_error(
        hass,
        entry,
        RequestFailedException("OP1013:Call interface times exceed limit (total)."),
    )
    await hass.async_block_till_done()
    issue = ir.async_get(hass).issues[(DOMAIN, issue_id)]
    assert issue.translation_key == ISSUE_OPEN_API_QUOTA
    assert issue.severity is ir.IssueSeverity.ERROR
    assert issue.is_fixable is False
    assert issue.learn_more_url == (
        "https://open.imoulife.com/consoleNew/resourceManage/myResource"
    )

    async_delete_quota_issue(hass, entry)
    await hass.async_block_till_done()
    assert (DOMAIN, issue_id) not in ir.async_get(hass).issues


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_quota_repair_learn_more_uses_china_console(hass) -> None:
    """China App IDs must open the China console, not the overseas one."""
    from custom_components.imou_life.const import CONF_API_URL_HZ

    entry = MockConfigEntry(
        domain=DOMAIN, data={**USER_INPUT, "api_url": CONF_API_URL_HZ}
    )
    entry.add_to_hass(hass)
    async_notify_imou_api_error(
        hass,
        entry,
        RequestFailedException("OP1013:Call interface times exceed limit (total)."),
    )
    await hass.async_block_till_done()

    issue = ir.async_get(hass).issues[
        (DOMAIN, f"{ISSUE_OPEN_API_QUOTA}_{entry.entry_id}")
    ]
    assert issue.learn_more_url == (
        "https://open.imou.com/consoleNew/resourceManage/myResource"
    )
