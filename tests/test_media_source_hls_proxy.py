"""Cloud/local recording playback prefers HLS and falls back to MP4."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from custom_components.imou_life.const import DOMAIN
from custom_components.imou_life.media_source import (
    KIND_CLOUD,
    KIND_LOCAL,
    MIME_MP4,
    ImouRecordMediaSource,
)
from custom_components.imou_life.record_hls_proxy import (
    DATA_RECORD_HLS_PROXY,
    ImouRecordHlsSegmentView,
    async_create_record_hls_proxy_playlist_url,
)
from homeassistant.components.media_source.models import MediaSourceItem
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from . import USER_INPUT

IDENTIFIER = (
    f"play|{KIND_CLOUD}|{{entry}}|SN1|0|2026-11-01 00:06:55|2026-11-01 00:08:10"
)
LOCAL_IDENTIFIER = (
    f"play|{KIND_LOCAL}|{{entry}}|SN1|0|2026-11-01 00:06:55|"
    "2026-11-01 00:08:10|L3JlY29yZC9maWxlLmRhdg"
)
HLS_UPSTREAM = (
    "https://mes502-online-hzali.lechange.com:7080/localvod/x.m3u8?proto=https&uid=abc"
)
HLS_PLAYLIST = """#EXTM3U
#EXT-X-MEDIA-SEQUENCE:42
#EXTINF:2,
https://media.example/segment.ts?token=old
#EXT-X-ENDLIST
"""
HLS_REFRESHED_PLAYLIST = HLS_PLAYLIST.replace("token=old", "token=fresh")


def _upstream_response(status: int, body: bytes = b"") -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        body=body,
        headers={"Content-Type": "video/mp2t"},
    )


def _segment_request(hass: HomeAssistant) -> SimpleNamespace:
    return SimpleNamespace(app={"hass": hass}, headers={})


async def _create_refreshable_session(
    hass: HomeAssistant,
    refresh: Callable[[], Awaitable[str]],
) -> str:
    with (
        patch(
            "custom_components.imou_life.record_hls_proxy._load_media_playlist",
            new_callable=AsyncMock,
            side_effect=[
                (
                    HLS_PLAYLIST,
                    ["https://media.example/segment.ts?token=old"],
                ),
            ],
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.async_sign_path",
            side_effect=lambda _hass, path, *_args, **_kwargs: path,
        ),
    ):
        signed = await async_create_record_hls_proxy_playlist_url(
            hass,
            HLS_UPSTREAM,
            refresh=refresh,
        )
    return signed.split("/record_hls/", 1)[1].split("/", 1)[0]


def _runtime_with_hls_url(
    data: dict[str, object] | None = None,
) -> MagicMock:
    runtime = MagicMock()
    runtime.coordinator.device_manager.delegate.async_create_device_record_hls = (
        AsyncMock(return_value=data or {"url": HLS_UPSTREAM})
    )
    return runtime


@pytest.mark.asyncio
async def test_expired_segment_refreshes_once_and_retries(
    hass: HomeAssistant,
) -> None:
    refresh = AsyncMock(return_value="https://media.example/fresh.m3u8")
    session_id = await _create_refreshable_session(hass, refresh)

    with (
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_response",
            new_callable=AsyncMock,
            side_effect=[
                _upstream_response(403),
                _upstream_response(200, b"fresh segment"),
            ],
        ) as fetch,
        patch(
            "custom_components.imou_life.record_hls_proxy._load_media_playlist",
            new_callable=AsyncMock,
            return_value=(
                HLS_REFRESHED_PLAYLIST,
                ["https://media.example/segment.ts?token=fresh"],
            ),
        ),
    ):
        response = await ImouRecordHlsSegmentView().get(
            _segment_request(hass), session_id, "0"
        )

    assert response.status == 200
    assert response.body == b"fresh segment"
    refresh.assert_awaited_once_with()
    assert [call.args[1] for call in fetch.await_args_list] == [
        "https://media.example/segment.ts?token=old",
        "https://media.example/segment.ts?token=fresh",
    ]


@pytest.mark.asyncio
async def test_failed_refreshed_segment_returns_502_without_second_refresh(
    hass: HomeAssistant,
) -> None:
    refresh = AsyncMock(return_value="https://media.example/fresh.m3u8")
    session_id = await _create_refreshable_session(hass, refresh)

    with (
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_response",
            new_callable=AsyncMock,
            side_effect=[
                _upstream_response(403),
                _upstream_response(403),
                _upstream_response(403),
            ],
        ) as fetch,
        patch(
            "custom_components.imou_life.record_hls_proxy._load_media_playlist",
            new_callable=AsyncMock,
            return_value=(
                HLS_REFRESHED_PLAYLIST,
                ["https://media.example/segment.ts?token=fresh"],
            ),
        ),
    ):
        with pytest.raises(Exception) as first:
            await ImouRecordHlsSegmentView().get(
                _segment_request(hass), session_id, "0"
            )
        with pytest.raises(Exception) as second:
            await ImouRecordHlsSegmentView().get(
                _segment_request(hass), session_id, "0"
            )

    assert getattr(first.value, "status", None) == 502
    assert getattr(second.value, "status", None) == 502
    refresh.assert_awaited_once_with()
    assert fetch.await_count == 3
    assert len(hass.data[DATA_RECORD_HLS_PROXY]) == 1


@pytest.mark.parametrize("status", [401, 404])
@pytest.mark.asyncio
async def test_other_expired_segment_statuses_refresh(
    hass: HomeAssistant, status: int
) -> None:
    refresh = AsyncMock(return_value="https://media.example/fresh.m3u8")
    session_id = await _create_refreshable_session(hass, refresh)

    with (
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_response",
            new_callable=AsyncMock,
            side_effect=[
                _upstream_response(status),
                _upstream_response(200, b"fresh segment"),
            ],
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy._load_media_playlist",
            new_callable=AsyncMock,
            return_value=(
                HLS_REFRESHED_PLAYLIST,
                ["https://media.example/segment.ts?token=fresh"],
            ),
        ),
    ):
        response = await ImouRecordHlsSegmentView().get(
            _segment_request(hass), session_id, "0"
        )

    assert response.body == b"fresh segment"
    refresh.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_segment_500_does_not_refresh(hass: HomeAssistant) -> None:
    refresh = AsyncMock(return_value="https://media.example/fresh.m3u8")
    session_id = await _create_refreshable_session(hass, refresh)

    with (
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_response",
            new_callable=AsyncMock,
            return_value=_upstream_response(500),
        ),
        pytest.raises(web.HTTPBadGateway),
    ):
        await ImouRecordHlsSegmentView().get(_segment_request(hass), session_id, "0")

    refresh.assert_not_awaited()


@pytest.mark.parametrize("empty_playlist", [False, True])
@pytest.mark.asyncio
async def test_refresh_failure_returns_502_and_is_not_repeated(
    hass: HomeAssistant, empty_playlist: bool
) -> None:
    refresh = AsyncMock(
        return_value="https://media.example/fresh.m3u8",
        side_effect=None if empty_playlist else OSError("refresh failed"),
    )
    session_id = await _create_refreshable_session(hass, refresh)

    with (
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_response",
            new_callable=AsyncMock,
            side_effect=[_upstream_response(403), _upstream_response(403)],
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy._load_media_playlist",
            new_callable=AsyncMock,
            return_value=("#EXTM3U\n", []),
        ),
    ):
        for _ in range(2):
            with pytest.raises(web.HTTPBadGateway):
                await ImouRecordHlsSegmentView().get(
                    _segment_request(hass), session_id, "0"
                )

    refresh.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_concurrent_expired_segments_share_one_refresh(
    hass: HomeAssistant,
) -> None:
    refresh_started = asyncio.Event()
    allow_refresh = asyncio.Event()

    async def refresh_source() -> str:
        refresh_started.set()
        await allow_refresh.wait()
        return "https://media.example/fresh.m3u8"

    session_id = await _create_refreshable_session(hass, refresh_source)
    old_fetches = 0
    both_old_fetched = asyncio.Event()

    async def fetch(
        _hass: HomeAssistant, url: str, *, range_header: str | None = None
    ) -> SimpleNamespace:
        nonlocal old_fetches
        if "token=old" in url:
            old_fetches += 1
            if old_fetches == 2:
                both_old_fetched.set()
            await both_old_fetched.wait()
            return _upstream_response(403)
        return _upstream_response(200, b"fresh segment")

    with (
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_response",
            side_effect=fetch,
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy._load_media_playlist",
            new_callable=AsyncMock,
            return_value=(
                HLS_REFRESHED_PLAYLIST,
                ["https://media.example/segment.ts?token=fresh"],
            ),
        ),
    ):
        requests = [
            asyncio.create_task(
                ImouRecordHlsSegmentView().get(_segment_request(hass), session_id, "0")
            )
            for _ in range(2)
        ]
        await refresh_started.wait()
        allow_refresh.set()
        responses = await asyncio.gather(*requests)

    assert [response.body for response in responses] == [
        b"fresh segment",
        b"fresh segment",
    ]
    assert old_fetches == 2


@pytest.mark.asyncio
async def test_refresh_maps_shifted_segment_by_media_sequence(
    hass: HomeAssistant,
) -> None:
    initial = """#EXTM3U
#EXT-X-MEDIA-SEQUENCE:42
#EXTINF:2,
https://media.example/42.ts?token=old
#EXTINF:2,
https://media.example/43.ts?token=old
"""
    refreshed = """#EXTM3U
#EXT-X-MEDIA-SEQUENCE:41
#EXTINF:2,
https://media.example/41.ts?token=fresh
#EXTINF:2,
https://media.example/42.ts?token=fresh
#EXTINF:2,
https://media.example/43.ts?token=fresh
"""
    refresh = AsyncMock(return_value="https://media.example/fresh.m3u8")
    with (
        patch(
            "custom_components.imou_life.record_hls_proxy._load_media_playlist",
            new_callable=AsyncMock,
            return_value=(
                initial,
                [
                    "https://media.example/42.ts?token=old",
                    "https://media.example/43.ts?token=old",
                ],
            ),
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.async_sign_path",
            side_effect=lambda _hass, path, *_args, **_kwargs: path,
        ),
    ):
        signed = await async_create_record_hls_proxy_playlist_url(
            hass, HLS_UPSTREAM, refresh=refresh
        )
    session_id = signed.split("/record_hls/", 1)[1].split("/", 1)[0]

    with (
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_response",
            new_callable=AsyncMock,
            side_effect=[
                _upstream_response(403),
                _upstream_response(200, b"segment 43"),
            ],
        ) as fetch,
        patch(
            "custom_components.imou_life.record_hls_proxy._load_media_playlist",
            new_callable=AsyncMock,
            return_value=(
                refreshed,
                [
                    "https://media.example/41.ts?token=fresh",
                    "https://media.example/42.ts?token=fresh",
                    "https://media.example/43.ts?token=fresh",
                ],
            ),
        ),
    ):
        response = await ImouRecordHlsSegmentView().get(
            _segment_request(hass), session_id, "1"
        )

    assert response.body == b"segment 43"
    assert fetch.await_args_list[1].args[1].endswith("43.ts?token=fresh")


@pytest.mark.asyncio
async def test_refresh_missing_original_segment_returns_502_without_index_error(
    hass: HomeAssistant,
) -> None:
    refresh = AsyncMock(return_value="https://media.example/fresh.m3u8")
    session_id = await _create_refreshable_session(hass, refresh)
    refreshed = """#EXTM3U
#EXT-X-MEDIA-SEQUENCE:43
#EXTINF:2,
https://media.example/43.ts?token=fresh
"""

    with (
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_response",
            new_callable=AsyncMock,
            return_value=_upstream_response(403),
        ) as fetch,
        patch(
            "custom_components.imou_life.record_hls_proxy._load_media_playlist",
            new_callable=AsyncMock,
            return_value=(
                refreshed,
                ["https://media.example/43.ts?token=fresh"],
            ),
        ),
        pytest.raises(web.HTTPBadGateway),
    ):
        await ImouRecordHlsSegmentView().get(_segment_request(hass), session_id, "0")

    assert fetch.await_count == 1


@pytest.mark.parametrize("index", ["https://attacker.example/x.ts", "-1", "999"])
@pytest.mark.asyncio
async def test_segment_handle_cannot_inject_upstream_url(
    hass: HomeAssistant, index: str
) -> None:
    refresh = AsyncMock(return_value="https://media.example/fresh.m3u8")
    session_id = await _create_refreshable_session(hass, refresh)

    with (
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_response",
            new_callable=AsyncMock,
        ) as fetch,
        pytest.raises((web.HTTPBadRequest, web.HTTPNotFound)),
    ):
        await ImouRecordHlsSegmentView().get(_segment_request(hass), session_id, index)

    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_range_and_upstream_response_headers_are_forwarded(
    hass: HomeAssistant,
) -> None:
    refresh = AsyncMock(return_value="https://media.example/fresh.m3u8")
    session_id = await _create_refreshable_session(hass, refresh)
    request = _segment_request(hass)
    request.headers = {"Range": "bytes=10-19"}
    upstream_response = SimpleNamespace(
        status=206,
        body=b"0123456789",
        headers={
            "Content-Type": "video/mp2t",
            "Content-Range": "bytes 10-19/100",
            "Accept-Ranges": "bytes",
            "Content-Length": "10",
        },
    )

    with (
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_response",
            new_callable=AsyncMock,
            return_value=upstream_response,
        ) as fetch,
    ):
        response = await ImouRecordHlsSegmentView().get(request, session_id, "0")

    fetch.assert_awaited_once_with(
        hass,
        "https://media.example/segment.ts?token=old",
        range_header="bytes=10-19",
    )
    assert response.status == 206
    assert response.headers["Content-Range"] == "bytes 10-19/100"
    assert response.headers["Accept-Ranges"] == "bytes"
    assert response.content_type == "video/mp2t"


@pytest.mark.asyncio
async def test_session_ttl_uses_default_and_earlier_upstream_expiry(
    hass: HomeAssistant,
) -> None:
    refresh = AsyncMock(return_value="https://media.example/fresh.m3u8")
    now = time.time()
    with (
        patch(
            "custom_components.imou_life.record_hls_proxy._load_media_playlist",
            new_callable=AsyncMock,
            return_value=(
                HLS_PLAYLIST,
                ["https://media.example/segment.ts?token=old"],
            ),
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.async_sign_path",
            side_effect=lambda _hass, path, *_args, **_kwargs: path,
        ),
    ):
        first = await async_create_record_hls_proxy_playlist_url(
            hass, HLS_UPSTREAM, refresh=refresh
        )
        second = await async_create_record_hls_proxy_playlist_url(
            hass,
            HLS_UPSTREAM,
            refresh=refresh,
            expires_at=now + 30,
        )
        expired = await async_create_record_hls_proxy_playlist_url(
            hass,
            f"https://media.example/record.m3u8?expire={int(now - 1)}",
            refresh=refresh,
        )

    first_id = first.split("/record_hls/", 1)[1].split("/", 1)[0]
    second_id = second.split("/record_hls/", 1)[1].split("/", 1)[0]
    expired_id = expired.split("/record_hls/", 1)[1].split("/", 1)[0]
    sessions = hass.data[DATA_RECORD_HLS_PROXY]
    assert now + 590 <= sessions[first_id].expires_at <= now + 601
    assert sessions[second_id].expires_at == pytest.approx(now + 30)
    assert sessions[expired_id].expires_at <= now + 1

    with (
        patch(
            "custom_components.imou_life.record_hls_proxy.time.time",
            return_value=now + 31,
        ),
        pytest.raises(web.HTTPNotFound),
    ):
        await ImouRecordHlsSegmentView().get(_segment_request(hass), second_id, "0")


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
    now = time.time()
    runtime = _runtime_with_hls_url({"url": HLS_UPSTREAM, "expireTime": 30})

    with (
        patch(
            "custom_components.imou_life.media_source.get_runtime_data",
            return_value=runtime,
        ),
        patch(
            "custom_components.imou_life.media_source.async_create_record_hls_proxy_playlist_url",
            new_callable=AsyncMock,
            return_value="/api/imou_life/record_hls/sid/playlist.m3u8?authSig=x",
        ) as proxy_mock,
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
        descriptor = await proxy_mock.await_args.kwargs["refresh"]()

    register_mock.assert_not_called()
    create_mock.assert_called_once()
    stream_mock.add_provider.assert_called_once()
    assert descriptor.playlist_url == HLS_UPSTREAM
    assert now + 29 <= proxy_mock.await_args.kwargs["expires_at"] <= now + 31
    assert (
        runtime.coordinator.device_manager.delegate.async_create_device_record_hls.await_count
        == 2
    )
    assert play.mime_type == "application/x-mpegURL"
    assert play.url == "/api/hls/imou_rec/playlist.m3u8"


@pytest.mark.asyncio
async def test_resolve_local_clip_refresh_rebuilds_same_descriptor(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    object.__setattr__(entry, "state", ConfigEntryState.LOADED)
    hass.config.components.add("stream")
    runtime = _runtime_with_hls_url()
    source = ImouRecordMediaSource(hass)
    item = MediaSourceItem(
        hass,
        DOMAIN,
        LOCAL_IDENTIFIER.format(entry=entry.entry_id),
        None,
    )
    stream_mock = MagicMock()
    stream_mock.endpoint_url.return_value = "/api/hls/imou_rec/playlist.m3u8"

    with (
        patch(
            "custom_components.imou_life.media_source.get_runtime_data",
            return_value=runtime,
        ),
        patch(
            "custom_components.imou_life.media_source.async_create_record_hls_proxy_playlist_url",
            new_callable=AsyncMock,
            return_value="/api/imou_life/record_hls/sid/playlist.m3u8?authSig=x",
        ) as proxy_mock,
        patch(
            "custom_components.imou_life.media_source.get_url",
            return_value="http://example.local:8123",
        ),
        patch(
            "custom_components.imou_life.media_source.create_stream",
            return_value=stream_mock,
        ),
    ):
        await source.async_resolve_media(item)
        await proxy_mock.await_args.kwargs["refresh"]()

    create_hls = (
        runtime.coordinator.device_manager.delegate.async_create_device_record_hls
    )
    calls = create_hls.await_args_list
    assert len(calls) == 2
    for call in calls:
        assert call.kwargs["record_type"] == "localRecord"
        assert call.kwargs["file_name"] == "/record/file.dav"


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
async def test_resolve_cloud_clip_mp4_when_create_stream_fails(
    hass: HomeAssistant,
) -> None:
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
