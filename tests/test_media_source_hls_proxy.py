"""Cloud/local recording playback prefers HLS and falls back to MP4."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.imou_life.const import DOMAIN
from custom_components.imou_life.media_source import (
    ImouRecordMediaSource,
    KIND_CLOUD,
    MIME_MP4,
)
from homeassistant.components.media_source.models import MediaSourceItem
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from . import USER_INPUT

IDENTIFIER = (
    f"play|{KIND_CLOUD}|{{entry}}|SN1|0|"
    "2026-11-01 00:06:55|2026-11-01 00:08:10"
)
HLS_UPSTREAM = (
    "https://mes502-online-hzali.lechange.com:7080/localvod/x.m3u8"
    "?proto=https&uid=abc"
)


def _runtime_with_hls_url() -> MagicMock:
    runtime = MagicMock()
    runtime.coordinator.device_manager.delegate.async_create_device_record_hls = (
        AsyncMock(return_value={"url": HLS_UPSTREAM})
    )
    return runtime


@pytest.mark.asyncio
async def test_resolve_cloud_clip_prefers_hls(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    object.__setattr__(entry, "state", ConfigEntryState.LOADED)
    hass.config.components.add("stream")

    source = ImouRecordMediaSource(hass)
    item = MediaSourceItem(hass, DOMAIN, IDENTIFIER.format(entry=entry.entry_id), None)
    stream_mock = MagicMock()
    stream_mock.endpoint_url.return_value = "/api/hls/imou_rec/playlist.m3u8"

    with (
        patch(
            "custom_components.imou_life.media_source.get_runtime_data",
            return_value=_runtime_with_hls_url(),
        ),
        patch(
            "custom_components.imou_life.media_source.async_create_record_hls_proxy_playlist_url",
            new_callable=AsyncMock,
            return_value="/api/imou_life/record_hls/sid/playlist.m3u8?authSig=x",
        ),
        patch(
            "custom_components.imou_life.media_source.get_url",
            return_value="http://example.local:8123",
        ),
        patch(
            "custom_components.imou_life.media_source.create_stream",
            return_value=stream_mock,
        ) as create_mock,
        patch(
            "custom_components.imou_life.media_source.register_playback_source",
        ) as register_mock,
    ):
        play = await source.async_resolve_media(item)

    register_mock.assert_not_called()
    create_mock.assert_called_once()
    stream_mock.add_provider.assert_called_once()
    assert play.mime_type == "application/x-mpegURL"
    assert play.url == "/api/hls/imou_rec/playlist.m3u8"


@pytest.mark.asyncio
async def test_resolve_cloud_clip_mp4_when_stream_missing(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    object.__setattr__(entry, "state", ConfigEntryState.LOADED)

    source = ImouRecordMediaSource(hass)
    item = MediaSourceItem(hass, DOMAIN, IDENTIFIER.format(entry=entry.entry_id), None)

    with (
        patch(
            "custom_components.imou_life.media_source.get_runtime_data",
            return_value=_runtime_with_hls_url(),
        ),
        patch(
            "custom_components.imou_life.media_source.register_playback_source",
        ) as register_mock,
        patch(
            "custom_components.imou_life.media_source.signed_record_playback_url",
            return_value="/api/imou_life/record_play/abc.mp4?authSig=x",
        ),
    ):
        play = await source.async_resolve_media(item)

    register_mock.assert_called_once()
    assert play.mime_type == MIME_MP4
    assert play.url.startswith("/api/imou_life/record_play/")


@pytest.mark.asyncio
async def test_resolve_cloud_clip_mp4_when_hls_setup_fails(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    object.__setattr__(entry, "state", ConfigEntryState.LOADED)
    hass.config.components.add("stream")

    source = ImouRecordMediaSource(hass)
    item = MediaSourceItem(hass, DOMAIN, IDENTIFIER.format(entry=entry.entry_id), None)

    with (
        patch(
            "custom_components.imou_life.media_source.get_runtime_data",
            return_value=_runtime_with_hls_url(),
        ),
        patch(
            "custom_components.imou_life.media_source.async_create_record_hls_proxy_playlist_url",
            new_callable=AsyncMock,
            side_effect=ValueError("no segments"),
        ),
        patch(
            "custom_components.imou_life.media_source.register_playback_source",
        ) as register_mock,
        patch(
            "custom_components.imou_life.media_source.signed_record_playback_url",
            return_value="/api/imou_life/record_play/abc.mp4?authSig=x",
        ),
    ):
        play = await source.async_resolve_media(item)

    register_mock.assert_called_once()
    assert play.mime_type == MIME_MP4


@pytest.mark.asyncio
async def test_resolve_cloud_clip_mp4_when_create_stream_fails(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    object.__setattr__(entry, "state", ConfigEntryState.LOADED)
    hass.config.components.add("stream")

    source = ImouRecordMediaSource(hass)
    item = MediaSourceItem(hass, DOMAIN, IDENTIFIER.format(entry=entry.entry_id), None)

    with (
        patch(
            "custom_components.imou_life.media_source.get_runtime_data",
            return_value=_runtime_with_hls_url(),
        ),
        patch(
            "custom_components.imou_life.media_source.async_create_record_hls_proxy_playlist_url",
            new_callable=AsyncMock,
            return_value="/api/imou_life/record_hls/sid/playlist.m3u8?authSig=x",
        ),
        patch(
            "custom_components.imou_life.media_source.get_url",
            return_value="http://example.local:8123",
        ),
        patch(
            "custom_components.imou_life.media_source.create_stream",
            side_effect=HomeAssistantError("Stream integration is not set up."),
        ),
        patch(
            "custom_components.imou_life.media_source.register_playback_source",
        ) as register_mock,
        patch(
            "custom_components.imou_life.media_source.signed_record_playback_url",
            return_value="/api/imou_life/record_play/abc.mp4?authSig=x",
        ),
    ):
        play = await source.async_resolve_media(item)

    register_mock.assert_called_once()
    assert play.mime_type == MIME_MP4
