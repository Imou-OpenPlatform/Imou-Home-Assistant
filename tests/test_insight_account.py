"""AI Insight account helpers."""

from unittest.mock import MagicMock

import pytest
from custom_components.imou_life.const import (
    CONF_API_URL_HZ,
    CONF_API_URL_INTL,
    PARAM_API_URL,
)
from custom_components.imou_life.insight_account import (
    ai_insight_channel_devices,
    async_trial_dialog_copy,
    camera_select_options,
    is_china_api_entry,
)
from homeassistant.core import HomeAssistant


def _camera(device_id: str, channel_id: str = "0") -> MagicMock:
    device = MagicMock()
    device.device_id = device_id
    device.channel_id = channel_id
    device.product_id = "pid"
    device.channel_name = None
    device.device_name = device_id
    return device


@pytest.mark.asyncio
@pytest.mark.usefixtures("enable_custom_integrations")
async def test_trial_copy_china(hass: HomeAssistant) -> None:
    entry = MagicMock()
    entry.data = {PARAM_API_URL: CONF_API_URL_HZ}
    title, body = await async_trial_dialog_copy(hass, entry)
    assert title == "智见试用"
    assert "17799860785" in body


@pytest.mark.asyncio
@pytest.mark.usefixtures("enable_custom_integrations")
async def test_trial_copy_overseas(hass: HomeAssistant) -> None:
    entry = MagicMock()
    entry.data = {PARAM_API_URL: CONF_API_URL_INTL}
    title, body = await async_trial_dialog_copy(hass, entry)
    assert title == "AI Insight trial"
    assert "19106884129" in body
    assert is_china_api_entry(entry) is False


def test_ai_insight_select_filters_non_insight_cameras() -> None:
    insight = _camera("cam-insight")
    insight.ai_insight_active = True
    insight.insight_features_active = True
    other = _camera("cam-event")
    other.ai_insight_active = False
    other.insight_features_active = False
    filtered = ai_insight_channel_devices([insight, other])
    assert [d.device_id for d in filtered] == ["cam-insight"]
    labels, _ = camera_select_options([insight, other])
    assert labels == ["cam-insight"]


def test_ai_insight_select_includes_cloud_plan_only() -> None:
    cloud_only = _camera("cam-cloud")
    cloud_only.ai_insight_active = False
    cloud_only.insight_features_active = True
    filtered = ai_insight_channel_devices([cloud_only])
    assert [d.device_id for d in filtered] == ["cam-cloud"]
