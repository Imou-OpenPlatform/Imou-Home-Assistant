"""AI Insight account helpers."""

from unittest.mock import MagicMock

from custom_components.imou_life.const import CONF_API_URL_HZ, CONF_API_URL_INTL, PARAM_API_URL
from custom_components.imou_life.insight_account import (
    ai_insight_channel_devices,
    camera_select_options,
    is_china_api_entry,
    trial_notification_copy,
)
from pyimouapi.insight.cloud import DeviceCloudService


def _camera(device_id: str, channel_id: str = "0") -> MagicMock:
    device = MagicMock()
    device.device_id = device_id
    device.channel_id = channel_id
    device.product_id = "pid"
    device.channel_name = None
    device.device_name = device_id
    return device


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


def test_ai_insight_select_filters_non_insight_cameras() -> None:
    insight = _camera("cam-insight")
    other = _camera("cam-event")
    cloud = {
        "cam-insight_0": DeviceCloudService(
            True, "1", "plan", "6", 1, None, None
        ),
        "cam-event_0": DeviceCloudService(
            True, "2", "event", "2", 1, None, None
        ),
    }
    filtered = ai_insight_channel_devices([insight, other], cloud)
    assert [d.device_id for d in filtered] == ["cam-insight"]
    labels, _ = camera_select_options([insight, other], cloud_by_key=cloud)
    assert labels == ["cam-insight"]
