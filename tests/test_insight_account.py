"""AI Insight account helpers."""

from unittest.mock import MagicMock

from custom_components.imou_life.const import CONF_API_URL_HZ, CONF_API_URL_INTL, PARAM_API_URL
from custom_components.imou_life.insight_account import (
    is_china_api_entry,
    trial_notification_copy,
)


def test_trial_copy_china() -> None:
    entry = MagicMock()
    entry.data = {PARAM_API_URL: CONF_API_URL_HZ}
    title, body = trial_notification_copy(entry)
    assert title == "智见试用"
    assert "17799860785" in body


def test_trial_copy_overseas() -> None:
    entry = MagicMock()
    entry.data = {PARAM_API_URL: CONF_API_URL_INTL}
    title, body = trial_notification_copy(entry)
    assert title == "AI Insight trial"
    assert "19106884129" in body
    assert is_china_api_entry(entry) is False
