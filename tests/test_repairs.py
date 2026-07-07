"""Tests for Imou Life repair issues."""

from __future__ import annotations

import pytest
from custom_components.imou_life.const import DOMAIN
from custom_components.imou_life.repairs import (
    ISSUE_EVENT_PUSH_NO_URL,
    async_create_event_push_no_url_issue,
    async_delete_event_push_issues,
)
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from . import USER_INPUT


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
