"""Tests that switching event push off also stops it in the Imou cloud.

Saving the options writes the new value before the reload runs, so by unload
time `entry.options` already says "off". Deciding from it means the cloud is
never told, and it keeps posting to a webhook that now drops everything.
"""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.imou_life import async_unload_entry
from custom_components.imou_life.const import (
    DOMAIN,
    PARAM_ENABLE_EVENT_PUSH,
    PARAM_NOTIFY_SERVICES,
    PARAM_WEBHOOK_ID,
)
from custom_components.imou_life.event_push import (
    _redact_callback_url,
    async_setup_event_push,
)
from custom_components.imou_life.runtime_data import ImouRuntimeData
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from . import USER_INPUT


def test_redact_callback_url_hides_webhook_id() -> None:
    """The webhook id is the only credential on the callback URL."""
    url = "https://ha.example.test/api/webhook/abcd1234efgh5678"
    redacted = _redact_callback_url(url)
    assert "abcd1234efgh5678" not in redacted
    assert "ha.example.test" in redacted
    assert "/api/webhook/" in redacted
    assert "abcd" in redacted


def _loaded_entry(
    hass: HomeAssistant, *, options: dict[str, Any], push_was_on: bool
) -> tuple[MockConfigEntry, MagicMock]:
    """Return an entry whose runtime records what setup actually enabled."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**USER_INPUT, PARAM_WEBHOOK_ID: "wh-teardown"},
        options=options,
        version=2,
    )
    entry.add_to_hass(hass)
    client = MagicMock()
    client.async_set_message_callback = AsyncMock()
    client.async_close = AsyncMock()
    entry.runtime_data = ImouRuntimeData(
        coordinator=MagicMock(),
        client=client,
        push_enabled=push_was_on,
    )
    return entry, client


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_turning_push_off_tells_the_cloud_to_stop(hass: HomeAssistant) -> None:
    """The options already read "off"; the runtime is what remembers it was on."""
    entry, client = _loaded_entry(
        hass, options={PARAM_ENABLE_EVENT_PUSH: False}, push_was_on=True
    )

    with patch.object(
        hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True)
    ):
        assert await async_unload_entry(hass, entry) is True

    client.async_set_message_callback.assert_awaited_once()
    assert client.async_set_message_callback.await_args.kwargs["status"] == "off"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_removing_an_entry_with_push_on_still_stops_the_cloud(
    hass: HomeAssistant,
) -> None:
    """The plain removal path, where the options never changed, keeps working."""
    entry, client = _loaded_entry(
        hass, options={PARAM_ENABLE_EVENT_PUSH: True}, push_was_on=True
    )

    with patch.object(
        hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True)
    ):
        assert await async_unload_entry(hass, entry) is True

    assert client.async_set_message_callback.await_args.kwargs["status"] == "off"


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_push_that_was_never_on_costs_no_cloud_call(hass: HomeAssistant) -> None:
    """Nothing was enabled, so unloading must not spend a call to disable it."""
    entry, client = _loaded_entry(
        hass, options={PARAM_ENABLE_EVENT_PUSH: False}, push_was_on=False
    )

    with patch.object(
        hass.config_entries, "async_unload_platforms", AsyncMock(return_value=True)
    ):
        assert await async_unload_entry(hass, entry) is True

    client.async_set_message_callback.assert_not_awaited()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_turning_push_on_and_off_leaves_the_cloud_off(
    hass: HomeAssistant,
) -> None:
    """End to end through the options flow, which is how users hit this."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**USER_INPUT, PARAM_WEBHOOK_ID: "wh-roundtrip"},
        options={PARAM_ENABLE_EVENT_PUSH: True},
        version=2,
    )
    entry.add_to_hass(hass)
    client = MagicMock()
    client.async_set_message_callback = AsyncMock()
    client.async_close = AsyncMock()
    manager = MagicMock()
    manager.async_get_devices = AsyncMock(return_value=[])
    manager.async_update_devices_status = AsyncMock(return_value=None)

    with (
        patch("custom_components.imou_life.ImouOpenApiClient", return_value=client),
        patch("custom_components.imou_life.ImouDeviceManager"),
        patch("custom_components.imou_life.ImouHaDeviceManager", return_value=manager),
        patch(
            "custom_components.imou_life.event_push.async_register_imou_webhook",
            return_value="https://example.test/api/webhook/wh-roundtrip",
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id) is True
        await hass.async_block_till_done()
        assert client.async_set_message_callback.await_args.kwargs["status"] == "on"

        hass.config_entries.async_update_entry(
            entry, options={PARAM_ENABLE_EVENT_PUSH: False}
        )
        await hass.async_block_till_done()

    statuses = [
        call.kwargs["status"]
        for call in client.async_set_message_callback.await_args_list
    ]
    assert "off" in statuses, (
        "the reload after saving the options never told the cloud to stop, so it "
        f"keeps pushing; calls were {statuses}"
    )


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        (["notify.mobile_app_phone"], ["notify.mobile_app_phone"]),
        (
            "notify.mobile_app_phone, qiyewechat.send",
            ["notify.mobile_app_phone", "qiyewechat.send"],
        ),
    ],
)
async def test_setup_event_push_loads_notify_services(
    hass: HomeAssistant, stored: Any, expected: list[str]
) -> None:
    """Notify options may be a list or a legacy comma-separated string."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**USER_INPUT, PARAM_WEBHOOK_ID: "wh-notify"},
        options={PARAM_NOTIFY_SERVICES: stored},
        version=2,
    )
    entry.add_to_hass(hass)
    runtime = ImouRuntimeData(coordinator=MagicMock())
    with patch(
        "custom_components.imou_life.event_push.async_register_imou_webhook",
        return_value="https://example.test/hook",
    ):
        await async_setup_event_push(hass, entry, MagicMock(), runtime)

    assert runtime.notify_services == expected


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_setup_event_push_does_not_log_full_callback_url(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """INFO logs must not leak the webhook id that authenticates the endpoint."""
    webhook_id = "abcd1234efgh5678ijkl"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={**USER_INPUT, PARAM_WEBHOOK_ID: webhook_id},
        options={PARAM_ENABLE_EVENT_PUSH: True},
        version=2,
    )
    entry.add_to_hass(hass)
    client = MagicMock()
    client.async_set_message_callback = AsyncMock()
    runtime = ImouRuntimeData(coordinator=MagicMock())
    callback = f"https://ha.example.test/api/webhook/{webhook_id}"
    with (
        patch(
            "custom_components.imou_life.event_push.async_register_imou_webhook",
            return_value=callback,
        ),
        caplog.at_level(logging.INFO, logger="custom_components.imou_life.event_push"),
    ):
        await async_setup_event_push(hass, entry, client, runtime)

    client.async_set_message_callback.assert_awaited()
    assert (
        callback == client.async_set_message_callback.await_args.kwargs["callback_url"]
    )
    assert webhook_id not in caplog.text
