"""Recording playback through the Imou HLS proxy and the HLS remux cache."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from custom_components.imou_life import async_unload_entry
from custom_components.imou_life.const import DOMAIN
from custom_components.imou_life.media_source import (
    KIND_CLOUD,
    KIND_LOCAL,
    MIME_HLS,
    SOURCE_REUSE_SECONDS,
    ImouRecordMediaSource,
    _local_live_timeout_seconds,
    _local_stream_ttl_seconds,
)
from custom_components.imou_life.record_hls_proxy import (
    _REFRESH_MAX_BACKOFF_SECONDS,
    _REFRESH_MAX_FAILURES,
    DATA_RECORD_HLS_PROXY,
    ImouRecordHlsPlaylistView,
    ImouRecordHlsResourceView,
    ImouRecordHlsSegmentView,
    RecordHlsDescriptor,
    async_create_record_hls_proxy_playlist_url,
    async_prepare_local_record_playback,
    async_remove_record_hls_proxy_sessions,
    record_hls_proxy_origin,
)
from custom_components.imou_life.record_playback_hls import (
    CACHE_SUBDIR,
    DATA_RECORD_PLAYBACK_HLS,
    RECORD_REMUX_TIMEOUT_SECONDS,
    HlsPlaybackJob,
    ImouRecordPlaybackFileView,
    ImouRecordPlaybackHlsView,
    _async_stop_idle_job,
    _async_wait_for_first_segments,
    _hls_command,
    _prune_playback_cache,
    _validate_proxy_source,
    async_remove_record_playback_sources,
    record_playback_cache_id,
    register_playback_source,
    signed_record_playlist_url,
)
from custom_components.imou_life.repairs import (
    ISSUE_RECORD_PLAYBACK,
    async_create_record_playback_issue,
)
from homeassistant.components.media_source import Unresolvable
from homeassistant.components.media_source.models import MediaSourceItem
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from pyimouapi.const import ERROR_CODE_NO_PERMISSION
from pyimouapi.exceptions import RequestFailedException
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
LOCAL_HLS_PROXY = (
    "http://ha.local:8123/api/imou_life/record_hls/"
    "0123456789abcdef0123456789abcdef/playlist.m3u8?authSig=x"
)


@pytest.fixture(autouse=True)
def _skip_codec_probe():
    """Do not run ffprobe against fake sources."""
    with patch(
        "custom_components.imou_life.record_playback_hls._async_probe_video_codec",
        new_callable=AsyncMock,
        return_value=None,
    ) as probe:
        yield probe


HLS_PLAYLIST = """#EXTM3U
#EXT-X-MEDIA-SEQUENCE:42
#EXTINF:2,
https://media.example/segment.ts?token=old
#EXT-X-ENDLIST
"""
HLS_REFRESHED_PLAYLIST = HLS_PLAYLIST.replace("token=old", "token=fresh")


def _local_hls_descriptor(
    marker: str = "", expires_at: float | None = None
) -> RecordHlsDescriptor:
    suffix = f"&marker={marker}" if marker else ""
    return RecordHlsDescriptor(
        f"{LOCAL_HLS_PROXY}{suffix}",
        expires_at,
        "http://ha.local:8123",
    )


IOS_CLIENT_PLAYLIST = (
    "/api/imou_life/record_hls/0123456789abcdef0123456789abcdef/"
    "client.m3u8?authSig=client"
)


@pytest.fixture(autouse=True)
async def cleanup_record_hls_sessions(hass: HomeAssistant):
    yield
    sessions = hass.data.get(DATA_RECORD_HLS_PROXY, {})
    for entry_id in {session.entry_id for session in sessions.values()}:
        await async_remove_record_hls_proxy_sessions(hass, entry_id)


def _upstream_response(status: int, body: bytes = b"") -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        body=body,
        headers={"Content-Type": "video/mp2t"},
    )


def _range_response(start: int, end: int, body: bytes) -> SimpleNamespace:
    return SimpleNamespace(
        status=206,
        body=body,
        headers={
            "Content-Type": "video/mp2t",
            "Content-Range": f"bytes {start}-{end}/1000",
            "Accept-Ranges": "bytes",
            "Content-Length": str(len(body)),
        },
    )


def _segment_request(hass: HomeAssistant) -> SimpleNamespace:
    return SimpleNamespace(app={"hass": hass}, headers={})


async def _create_refreshable_session(
    hass: HomeAssistant,
    refresh: Callable[[], Awaitable[str]],
    *,
    entry_id: str = "entry-a",
    stream_ttl_seconds: float | None = None,
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
            "custom_components.imou_life.record_hls_proxy._async_prefetch_upstream_segments",
            new_callable=AsyncMock,
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
            entry_id=entry_id,
            stream_ttl_seconds=stream_ttl_seconds,
        )
    return signed.split("/record_hls/", 1)[1].split("/", 1)[0]


async def _create_session_from_playlist(
    hass: HomeAssistant,
    playlist: str,
    segment_urls: list[str],
    refresh: Callable[[], Awaitable[str]] | None = None,
) -> str:
    refresh_source = refresh or AsyncMock(
        return_value="https://media.example/fresh.m3u8"
    )
    with (
        patch(
            "custom_components.imou_life.record_hls_proxy._load_media_playlist",
            new_callable=AsyncMock,
            return_value=(playlist, segment_urls),
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy._async_prefetch_upstream_segments",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.async_sign_path",
            side_effect=lambda _hass, path, *_args, **_kwargs: path,
        ),
    ):
        signed = await async_create_record_hls_proxy_playlist_url(
            hass,
            HLS_UPSTREAM,
            refresh=refresh_source,
            entry_id="entry-a",
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
async def test_vod_refresh_preserves_progressive_stream_lifetime(
    hass: HomeAssistant,
) -> None:
    refresh = AsyncMock(return_value="https://media.example/fresh.m3u8")
    before = time.time()
    session_id = await _create_refreshable_session(
        hass, refresh, stream_ttl_seconds=RECORD_REMUX_TIMEOUT_SECONDS
    )
    session = hass.data[DATA_RECORD_HLS_PROXY][session_id]
    assert not session.live
    assert session.expires_at >= before + RECORD_REMUX_TIMEOUT_SECONDS - 1

    with (
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_response",
            new_callable=AsyncMock,
            side_effect=[
                _upstream_response(403),
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
    assert session.expires_at >= before + RECORD_REMUX_TIMEOUT_SECONDS - 1


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


@pytest.mark.asyncio
async def test_expired_segment_refreshes_again_after_cooldown(
    hass: HomeAssistant,
) -> None:
    """Cloud segment tokens keep expiring; a fresh request must be repeatable."""
    refresh = AsyncMock(return_value="https://media.example/fresh.m3u8")
    session_id = await _create_refreshable_session(hass, refresh)
    session = hass.data[DATA_RECORD_HLS_PROXY][session_id]

    with (
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_response",
            new_callable=AsyncMock,
            return_value=_upstream_response(403),
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
        for _ in range(2):
            with pytest.raises(web.HTTPBadGateway):
                await ImouRecordHlsSegmentView().get(
                    _segment_request(hass), session_id, "0"
                )
            session.last_refresh_at = 0.0

    assert refresh.await_count == 2


@pytest.mark.parametrize("status", [401, 404, 502])
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
    assert (
        DOMAIN,
        f"{ISSUE_RECORD_PLAYBACK}_entry-a",
    ) not in ir.async_get(hass).issues


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
async def test_repeated_refresh_failures_stop_after_the_cap(
    hass: HomeAssistant,
) -> None:
    """An unrecoverable clip stops spending API calls on refreshes."""
    refresh = AsyncMock(side_effect=OSError("refresh failed"))
    session_id = await _create_refreshable_session(hass, refresh)
    session = hass.data[DATA_RECORD_HLS_PROXY][session_id]

    with patch(
        "custom_components.imou_life.record_hls_proxy.async_fetch_url_response",
        new_callable=AsyncMock,
        return_value=_upstream_response(403),
    ):
        for _ in range(_REFRESH_MAX_FAILURES + 2):
            session.last_refresh_at = 0.0
            with pytest.raises(web.HTTPBadGateway):
                await ImouRecordHlsSegmentView().get(
                    _segment_request(hass), session_id, "0"
                )

    assert refresh.await_count == _REFRESH_MAX_FAILURES
    assert session.refresh_backoff == _REFRESH_MAX_BACKOFF_SECONDS


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
            "custom_components.imou_life.record_hls_proxy._async_prefetch_upstream_segments",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.async_sign_path",
            side_effect=lambda _hass, path, *_args, **_kwargs: path,
        ),
    ):
        signed = await async_create_record_hls_proxy_playlist_url(
            hass, HLS_UPSTREAM, refresh=refresh, entry_id="entry-a"
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
async def test_refresh_rejects_ambiguous_repeated_path_after_playlist_shift(
    hass: HomeAssistant,
) -> None:
    initial = """#EXTM3U
#EXTINF:2,
https://media.example/segment.ts?part=a&token=old
#EXTINF:2,
https://media.example/segment.ts?part=b&token=old
"""
    refreshed = """#EXTM3U
#EXTINF:2,
https://media.example/segment.ts?part=new&token=fresh
#EXTINF:2,
https://media.example/segment.ts?part=a&token=fresh
#EXTINF:2,
https://media.example/segment.ts?part=b&token=fresh
"""
    refresh = AsyncMock(return_value="https://media.example/fresh.m3u8")
    with (
        patch(
            "custom_components.imou_life.record_hls_proxy._load_media_playlist",
            new_callable=AsyncMock,
            return_value=(
                initial,
                [
                    "https://media.example/segment.ts?part=a&token=old",
                    "https://media.example/segment.ts?part=b&token=old",
                ],
            ),
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.async_sign_path",
            side_effect=lambda _hass, path, *_args, **_kwargs: path,
        ),
    ):
        signed = await async_create_record_hls_proxy_playlist_url(
            hass, HLS_UPSTREAM, refresh=refresh, entry_id="entry-a"
        )
    session_id = signed.split("/record_hls/", 1)[1].split("/", 1)[0]

    with (
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_response",
            new_callable=AsyncMock,
            side_effect=[
                _upstream_response(403),
                _upstream_response(200, b"wrong segment"),
            ],
        ) as fetch,
        patch(
            "custom_components.imou_life.record_hls_proxy._load_media_playlist",
            new_callable=AsyncMock,
            return_value=(
                refreshed,
                [
                    "https://media.example/segment.ts?part=new&token=fresh",
                    "https://media.example/segment.ts?part=a&token=fresh",
                    "https://media.example/segment.ts?part=b&token=fresh",
                ],
            ),
        ),
        pytest.raises(web.HTTPBadGateway),
    ):
        await ImouRecordHlsSegmentView().get(_segment_request(hass), session_id, "0")

    assert fetch.await_count == 1


@pytest.mark.asyncio
async def test_refresh_maps_repeated_path_with_explicit_byteranges(
    hass: HomeAssistant,
) -> None:
    initial = """#EXTM3U
#EXT-X-BYTERANGE:100@100
#EXTINF:2,
https://media.example/segment.ts?token=old
#EXT-X-BYTERANGE:100@200
#EXTINF:2,
https://media.example/segment.ts?token=old
"""
    refreshed = """#EXTM3U
#EXT-X-BYTERANGE:100@0
#EXTINF:2,
https://media.example/segment.ts?token=fresh
#EXT-X-BYTERANGE:100@100
#EXTINF:2,
https://media.example/segment.ts?token=fresh
#EXT-X-BYTERANGE:100@200
#EXTINF:2,
https://media.example/segment.ts?token=fresh
"""
    refresh = AsyncMock(return_value="https://media.example/fresh.m3u8")
    with (
        patch(
            "custom_components.imou_life.record_hls_proxy._load_media_playlist",
            new_callable=AsyncMock,
            return_value=(
                initial,
                [
                    "https://media.example/segment.ts?token=old",
                    "https://media.example/segment.ts?token=old",
                ],
            ),
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.async_sign_path",
            side_effect=lambda _hass, path, *_args, **_kwargs: path,
        ),
    ):
        signed = await async_create_record_hls_proxy_playlist_url(
            hass, HLS_UPSTREAM, refresh=refresh, entry_id="entry-a"
        )
    session_id = signed.split("/record_hls/", 1)[1].split("/", 1)[0]

    with (
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_response",
            new_callable=AsyncMock,
            side_effect=[
                _upstream_response(403),
                _range_response(100, 199, b"range 100"),
            ],
        ) as fetch,
        patch(
            "custom_components.imou_life.record_hls_proxy._load_media_playlist",
            new_callable=AsyncMock,
            return_value=(
                refreshed,
                [
                    "https://media.example/segment.ts?token=fresh",
                    "https://media.example/segment.ts?token=fresh",
                    "https://media.example/segment.ts?token=fresh",
                ],
            ),
        ),
    ):
        response = await ImouRecordHlsSegmentView().get(
            _segment_request(hass), session_id, "0"
        )

    assert response.body == b"range 100"
    assert fetch.await_count == 2
    assert [call.kwargs["range_header"] for call in fetch.await_args_list] == [
        "bytes=100-199",
        "bytes=100-199",
    ]


@pytest.mark.asyncio
async def test_implicit_byterange_offset_is_resolved_and_rewritten(
    hass: HomeAssistant,
) -> None:
    playlist = """#EXTM3U
#EXT-X-BYTERANGE:100@50
#EXTINF:2,
https://media.example/segment.ts
#EXT-X-BYTERANGE:25
#EXTINF:2,
https://media.example/segment.ts
"""
    session_id = await _create_session_from_playlist(
        hass,
        playlist,
        [
            "https://media.example/segment.ts",
            "https://media.example/segment.ts",
        ],
    )

    with (
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_response",
            new_callable=AsyncMock,
            return_value=_range_response(150, 174, b"second range"),
        ) as fetch,
        patch(
            "custom_components.imou_life.record_hls_proxy.async_sign_path",
            side_effect=lambda _hass, path, *_args, **_kwargs: path,
        ),
    ):
        playlist_response = await ImouRecordHlsPlaylistView().get(
            _segment_request(hass), session_id
        )
        segment_response = await ImouRecordHlsSegmentView().get(
            _segment_request(hass), session_id, "1"
        )

    assert "#EXT-X-VERSION:" not in playlist_response.text
    assert "#EXT-X-BYTERANGE:100@50" in playlist_response.text
    assert "#EXT-X-BYTERANGE:25@150" in playlist_response.text
    assert segment_response.body == b"second range"
    fetch.assert_awaited_once_with(
        hass,
        "https://media.example/segment.ts",
        range_header="bytes=150-174",
    )


@pytest.mark.asyncio
async def test_playlist_reuses_signed_segment_urls_until_they_near_expiry(
    hass: HomeAssistant,
) -> None:
    """Repeated playlist reads reuse signatures instead of signing every line."""
    playlist = """#EXTM3U
#EXTINF:2,
https://media.example/segment.ts?token=old
#EXT-X-ENDLIST
"""
    session_id = await _create_session_from_playlist(
        hass,
        playlist,
        ["https://media.example/segment.ts?token=old"],
    )
    session = hass.data[DATA_RECORD_HLS_PROXY][session_id]
    view = ImouRecordHlsPlaylistView()

    with patch(
        "custom_components.imou_life.record_hls_proxy.async_sign_path",
        side_effect=lambda _hass, path, *_args, **_kwargs: path,
    ) as sign:
        await view.get(_segment_request(hass), session_id)
        await view.get(_segment_request(hass), session_id)
        assert sign.call_count == 1
        signed_url, _valid_until = session.signed_paths[("segment", 0)]
        session.signed_paths[("segment", 0)] = (signed_url, time.time() + 5)
        await view.get(_segment_request(hass), session_id)

    assert sign.call_count == 2


@pytest.mark.asyncio
async def test_playlist_rewrite_preserves_media_semantics_and_proxies_key_map(
    hass: HomeAssistant,
) -> None:
    playlist = """#EXTM3U
#EXT-X-VERSION:7
#EXT-X-TARGETDURATION:6
#EXT-X-MEDIA-SEQUENCE:42
#EXT-X-PLAYLIST-TYPE:VOD
#EXT-X-KEY:METHOD=AES-128,URI="key.bin?token=secret",IV=0x1
#EXT-X-MAP:URI="init.mp4?token=secret",BYTERANGE="100@20"
#EXT-X-PROGRAM-DATE-TIME:2026-11-01T00:00:00Z
#EXTINF:1.25,first title
one.m4s?token=secret
#EXT-X-DISCONTINUITY
#EXTINF:4.75,second title
two.m4s?token=secret
#EXT-X-KEY:METHOD=NONE
#EXT-X-ENDLIST
"""
    base = "https://media.example/path/index.m3u8?token=playlist"
    with (
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_text",
            new_callable=AsyncMock,
            return_value=playlist,
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.async_pin_public_url",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.async_sign_path",
            side_effect=lambda _hass, path, *_args, **_kwargs: path,
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.get_url",
            return_value="http://ha.local:8123",
        ),
    ):
        signed = await async_create_record_hls_proxy_playlist_url(
            hass,
            base,
            refresh=AsyncMock(return_value=base),
            entry_id="entry-a",
        )
        session_id = signed.split("/record_hls/", 1)[1].split("/", 1)[0]
        response = await ImouRecordHlsPlaylistView().get(
            _segment_request(hass), session_id
        )

    origin = "http://ha.local:8123"
    assert (
        response.text
        == f"""#EXTM3U
#EXT-X-VERSION:7
#EXT-X-TARGETDURATION:6
#EXT-X-MEDIA-SEQUENCE:42
#EXT-X-PLAYLIST-TYPE:VOD
#EXT-X-KEY:METHOD=AES-128,URI="{origin}/api/imou_life/record_hls/{session_id}/resource/key/0",IV=0x1
#EXT-X-MAP:URI="{origin}/api/imou_life/record_hls/{session_id}/resource/map/0",BYTERANGE="100@20"
#EXT-X-PROGRAM-DATE-TIME:2026-11-01T00:00:00Z
#EXTINF:1.25,first title
{origin}/api/imou_life/record_hls/{session_id}/segment/0.ts
#EXT-X-DISCONTINUITY
#EXTINF:4.75,second title
{origin}/api/imou_life/record_hls/{session_id}/segment/1.ts
#EXT-X-KEY:METHOD=NONE
#EXT-X-ENDLIST
"""
    )


@pytest.mark.asyncio
async def test_live_playlist_follows_the_public_window(hass: HomeAssistant) -> None:
    """A moving public playlist keeps the same Home Assistant segment indexes."""
    first = """#EXTM3U
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-TARGETDURATION:4
#EXTINF:4,
https://media.example/clip?userid=seg_0.ts&proto=https
#EXTINF:4,
https://media.example/clip?userid=seg_1.ts&proto=https
#EXTINF:4,
https://media.example/clip?userid=seg_2.ts&proto=https
"""
    second = """#EXTM3U
#EXT-X-MEDIA-SEQUENCE:2
#EXT-X-TARGETDURATION:4
#EXTINF:4,
https://media.example/clip?userid=seg_2.ts&proto=https
#EXTINF:4,
https://media.example/clip?userid=seg_3.ts&proto=https
#EXTINF:4,
https://media.example/clip?userid=seg_4.ts&proto=https
#EXT-X-ENDLIST
"""
    first_urls = [
        "https://media.example/clip?userid=seg_0.ts&proto=https",
        "https://media.example/clip?userid=seg_1.ts&proto=https",
        "https://media.example/clip?userid=seg_2.ts&proto=https",
    ]
    with (
        patch(
            "custom_components.imou_life.record_hls_proxy._load_media_playlist",
            new_callable=AsyncMock,
            return_value=(first, first_urls),
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_text",
            new_callable=AsyncMock,
            return_value=second,
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_response",
            new_callable=AsyncMock,
            return_value=_upstream_response(200, b"warm"),
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.async_pin_public_url",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.async_sign_path",
            side_effect=lambda _hass, path, *_args, **_kwargs: path,
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.get_url",
            return_value="http://ha.local:8123",
        ),
    ):
        signed = await async_create_record_hls_proxy_playlist_url(
            hass,
            "https://media.example/local.m3u8",
            refresh=AsyncMock(return_value="https://media.example/local.m3u8"),
            entry_id="entry-a",
        )
        session_id = signed.split("/record_hls/", 1)[1].split("/", 1)[0]
        hass.data[DATA_RECORD_HLS_PROXY][session_id].last_playlist_fetch_at = 0.0
        response = await ImouRecordHlsPlaylistView().get(
            _segment_request(hass), session_id
        )

    origin = "http://ha.local:8123"
    assert (
        response.text
        == f"""#EXTM3U
#EXT-X-MEDIA-SEQUENCE:2
#EXT-X-TARGETDURATION:4
#EXTINF:4,
{origin}/api/imou_life/record_hls/{session_id}/segment/2.ts
#EXTINF:4,
{origin}/api/imou_life/record_hls/{session_id}/segment/3.ts
#EXTINF:4,
{origin}/api/imou_life/record_hls/{session_id}/segment/4.ts
#EXT-X-ENDLIST
"""
    )


@pytest.mark.asyncio
async def test_prepare_local_playback_polls_until_first_segment(
    hass: HomeAssistant,
) -> None:
    sliding = """#EXTM3U
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-TARGETDURATION:4
#EXTINF:4,
https://media.example/clip?userid=seg_0.ts&proto=https
"""
    waiting = (sliding, [])
    ready = (
        sliding,
        ["https://media.example/clip?userid=seg_0.ts&proto=https"],
    )
    with (
        patch(
            "custom_components.imou_life.record_hls_proxy._load_media_playlist",
            new_callable=AsyncMock,
            side_effect=[
                type(
                    "Loaded",
                    (),
                    {"body": waiting[0], "segment_urls": waiting[1], "base_url": "x"},
                )(),
                type(
                    "Loaded",
                    (),
                    {"body": ready[0], "segment_urls": ready[1], "base_url": "x"},
                )(),
            ],
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_response",
            new_callable=AsyncMock,
            return_value=_upstream_response(200, b"segment-ready"),
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.async_pin_public_url",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.asyncio.sleep",
            new_callable=AsyncMock,
        ) as sleep_mock,
    ):
        prepared = await async_prepare_local_record_playback(
            hass,
            "https://media.example/local.m3u8",
        )

    assert prepared.segment_prefetch[0] == b"segment-ready"
    sleep_mock.assert_awaited()


@pytest.mark.asyncio
async def test_session_create_prefetches_and_serves_cached_segment(
    hass: HomeAssistant,
) -> None:
    playlist = """#EXTM3U
#EXT-X-MEDIA-SEQUENCE:0
#EXTINF:2,
https://media.example/seg0.ts
#EXTINF:2,
https://media.example/seg1.ts
#EXT-X-ENDLIST
"""
    fetch = AsyncMock(
        side_effect=[
            _upstream_response(200, b"prefetched-0"),
        ]
    )
    with (
        patch(
            "custom_components.imou_life.record_hls_proxy._load_media_playlist",
            new_callable=AsyncMock,
            return_value=(
                playlist,
                [
                    "https://media.example/seg0.ts",
                    "https://media.example/seg1.ts",
                ],
            ),
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.async_pin_public_url",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_response",
            fetch,
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.async_sign_path",
            side_effect=lambda _hass, path, *_args, **_kwargs: path,
        ),
    ):
        signed = await async_create_record_hls_proxy_playlist_url(
            hass,
            "https://media.example/vod.m3u8",
            refresh=AsyncMock(return_value="https://media.example/vod.m3u8"),
            entry_id="entry-a",
        )
        await hass.async_block_till_done()
        session_id = signed.split("/record_hls/", 1)[1].split("/", 1)[0]
        assert fetch.await_count == 1
        fetch.reset_mock()
        response = await ImouRecordHlsSegmentView().get(
            _segment_request(hass), session_id, "0"
        )

    assert response.body == b"prefetched-0"
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_variant_key_and_map_resolve_from_media_playlist_url(
    hass: HomeAssistant,
) -> None:
    class RedirectedText(str):
        def __new__(cls, value: str, final_url: str):
            instance = super().__new__(cls, value)
            instance.final_url = final_url
            return instance

    master = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1
variants/media/index.m3u8
"""
    media = """#EXTM3U
#EXT-X-KEY:METHOD=AES-128,URI="keys/key.bin"
#EXT-X-MAP:URI="init/init.mp4"
#EXTINF:2,
https://segments.example/chunks/one.m4s
"""
    with (
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_text",
            new_callable=AsyncMock,
            side_effect=[
                RedirectedText(
                    master,
                    "https://redirected.example/canonical/master.m3u8",
                ),
                RedirectedText(
                    media,
                    "https://cdn.example/final/media/index.m3u8",
                ),
            ],
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.async_pin_public_url",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.async_sign_path",
            side_effect=lambda _hass, path, *_args, **_kwargs: path,
        ),
    ):
        signed = await async_create_record_hls_proxy_playlist_url(
            hass,
            "https://playlists.example/start.m3u8",
            refresh=AsyncMock(),
            entry_id="entry-a",
        )

    session_id = signed.split("/record_hls/", 1)[1].split("/", 1)[0]
    resources = hass.data[DATA_RECORD_HLS_PROXY][session_id].resources
    assert resources["key"][0].url == ("https://cdn.example/final/media/keys/key.bin")
    assert resources["map"][0].url == ("https://cdn.example/final/media/init/init.mp4")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "playlist",
    [
        "#EXTM3U\n#EXTINF:2,\nhttp://10.0.0.1/segment.ts\n",
        (
            '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="http://10.0.0.1/key"\n'
            "#EXTINF:2,\nhttps://media.example/segment.ts\n"
        ),
        (
            '#EXTM3U\n#EXT-X-MAP:URI="http://[fd00::1]/init.mp4"\n'
            "#EXTINF:2,\nhttps://media.example/segment.m4s\n"
        ),
    ],
)
async def test_private_playlist_resources_fail_closed(
    hass: HomeAssistant, playlist: str
) -> None:
    async def validate(_hass: HomeAssistant, url: str):
        if "10.0.0.1" in url or "fd00::1" in url:
            raise ValueError("private")
        return MagicMock()

    with (
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_text",
            new_callable=AsyncMock,
            return_value=playlist,
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.async_pin_public_url",
            side_effect=validate,
        ),
        pytest.raises(ValueError, match="private"),
    ):
        await async_create_record_hls_proxy_playlist_url(
            hass,
            "https://media.example/index.m3u8",
            refresh=AsyncMock(),
            entry_id="entry-a",
        )


@pytest.mark.asyncio
async def test_private_variant_fails_before_variant_fetch(
    hass: HomeAssistant,
) -> None:
    master = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nhttp://192.168.1.2/private.m3u8\n"
    with (
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_text",
            new_callable=AsyncMock,
            return_value=master,
        ) as fetch,
        patch(
            "custom_components.imou_life.record_hls_proxy.async_pin_public_url",
            new_callable=AsyncMock,
            side_effect=ValueError("private variant"),
        ),
        pytest.raises(ValueError, match="private variant"),
    ):
        await async_create_record_hls_proxy_playlist_url(
            hass,
            "https://media.example/master.m3u8",
            refresh=AsyncMock(),
            entry_id="entry-a",
        )

    fetch.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "index", "expected_range", "content_type"),
    [
        ("key", "0", None, "application/octet-stream"),
        ("map", "0", "bytes=20-119", "video/mp4"),
    ],
)
async def test_key_and_map_requests_use_safe_resource_handles(
    hass: HomeAssistant,
    kind: str,
    index: str,
    expected_range: str | None,
    content_type: str,
) -> None:
    playlist = """#EXTM3U
#EXT-X-KEY:METHOD=AES-128,URI="key.bin"
#EXT-X-MAP:URI="init.mp4",BYTERANGE="100@20"
#EXTINF:2,
segment.m4s
"""
    base = "https://media.example/path/index.m3u8"
    with (
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_text",
            new_callable=AsyncMock,
            return_value=playlist,
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.async_pin_public_url",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.async_sign_path",
            side_effect=lambda _hass, path, *_args, **_kwargs: path,
        ),
    ):
        signed = await async_create_record_hls_proxy_playlist_url(
            hass, base, refresh=AsyncMock(return_value=base), entry_id="entry-a"
        )
    session_id = signed.split("/record_hls/", 1)[1].split("/", 1)[0]
    upstream = (
        _range_response(20, 119, b"resource")
        if expected_range
        else SimpleNamespace(status=200, body=b"resource", headers={})
    )
    with patch(
        "custom_components.imou_life.record_hls_proxy.async_fetch_url_response",
        new_callable=AsyncMock,
        return_value=upstream,
    ) as fetch:
        response = await ImouRecordHlsResourceView().get(
            _segment_request(hass), session_id, kind, index
        )

    fetch.assert_awaited_once_with(
        hass,
        f"https://media.example/path/{'key.bin' if kind == 'key' else 'init.mp4'}",
        range_header=expected_range,
    )
    assert response.content_type == content_type


@pytest.mark.asyncio
async def test_expired_key_refreshes_once_and_keeps_resource_mapping(
    hass: HomeAssistant,
) -> None:
    initial = """#EXTM3U
#EXT-X-KEY:METHOD=AES-128,URI="key.bin?token=old"
#EXTINF:2,
segment.ts?token=old
"""
    refreshed = initial.replace("token=old", "token=fresh")
    refresh = AsyncMock(
        return_value=RecordHlsDescriptor("https://media.example/fresh.m3u8")
    )
    with (
        patch(
            "custom_components.imou_life.record_hls_proxy._load_media_playlist",
            new_callable=AsyncMock,
            return_value=(
                initial,
                ["https://media.example/segment.ts?token=old"],
            ),
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.async_pin_public_url",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.async_sign_path",
            side_effect=lambda _hass, path, *_args, **_kwargs: path,
        ),
    ):
        signed = await async_create_record_hls_proxy_playlist_url(
            hass,
            "https://media.example/index.m3u8",
            refresh=refresh,
            entry_id="entry-a",
        )
    session_id = signed.split("/record_hls/", 1)[1].split("/", 1)[0]

    with (
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_response",
            new_callable=AsyncMock,
            side_effect=[
                _upstream_response(403),
                SimpleNamespace(status=200, body=b"fresh key", headers={}),
            ],
        ) as fetch,
        patch(
            "custom_components.imou_life.record_hls_proxy._load_media_playlist",
            new_callable=AsyncMock,
            return_value=(
                refreshed,
                ["https://media.example/segment.ts?token=fresh"],
            ),
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.async_pin_public_url",
            new_callable=AsyncMock,
        ),
    ):
        response = await ImouRecordHlsResourceView().get(
            _segment_request(hass), session_id, "key", "0"
        )

    assert response.body == b"fresh key"
    refresh.assert_awaited_once_with()
    assert [call.args[1] for call in fetch.await_args_list] == [
        "https://media.example/key.bin?token=old",
        "https://media.example/key.bin?token=fresh",
    ]


@pytest.mark.asyncio
async def test_map_implicit_byterange_offset_is_resolved(
    hass: HomeAssistant,
) -> None:
    playlist = """#EXTM3U
#EXT-X-MAP:URI="init.mp4",BYTERANGE="100@20"
#EXT-X-MAP:URI="init.mp4",BYTERANGE="25"
#EXTINF:2,
segment.m4s
"""
    with (
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_text",
            new_callable=AsyncMock,
            return_value=playlist,
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.async_pin_public_url",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.async_sign_path",
            side_effect=lambda _hass, path, *_args, **_kwargs: path,
        ),
    ):
        signed = await async_create_record_hls_proxy_playlist_url(
            hass,
            "https://media.example/index.m3u8",
            refresh=AsyncMock(),
            entry_id="entry-a",
        )
    session_id = signed.split("/record_hls/", 1)[1].split("/", 1)[0]

    with patch(
        "custom_components.imou_life.record_hls_proxy.async_fetch_url_response",
        new_callable=AsyncMock,
        return_value=_range_response(120, 144, b"map"),
    ) as fetch:
        await ImouRecordHlsResourceView().get(
            _segment_request(hass), session_id, "map", "1"
        )

    assert fetch.await_args.kwargs["range_header"] == "bytes=120-144"


@pytest.mark.asyncio
async def test_implicit_byterange_rejects_same_path_with_different_query(
    hass: HomeAssistant,
) -> None:
    playlist = """#EXTM3U
#EXT-X-BYTERANGE:100@50
#EXTINF:2,
https://media.example/segment.ts?part=a
#EXT-X-BYTERANGE:25
#EXTINF:2,
https://media.example/segment.ts?part=b
"""
    session_id = await _create_session_from_playlist(
        hass,
        playlist,
        [
            "https://media.example/segment.ts?part=a",
            "https://media.example/segment.ts?part=b",
        ],
    )

    with (
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_response",
            new_callable=AsyncMock,
        ) as fetch,
        pytest.raises(web.HTTPBadGateway),
    ):
        await ImouRecordHlsSegmentView().get(_segment_request(hass), session_id, "1")

    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_implicit_byterange_normalizes_relative_and_absolute_uri(
    hass: HomeAssistant,
) -> None:
    absolute = (
        "https://mes502-online-hzali.lechange.com:7080/localvod/segment.m4s?part=a"
    )
    playlist = f"""#EXTM3U
#EXT-X-BYTERANGE:100@50
#EXTINF:2,
segment.m4s?part=a#first
#EXT-X-BYTERANGE:25
#EXTINF:2,
{absolute}#second
"""
    with (
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_text",
            new_callable=AsyncMock,
            return_value=playlist,
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.async_sign_path",
            side_effect=lambda _hass, path, *_args, **_kwargs: path,
        ),
    ):
        signed = await async_create_record_hls_proxy_playlist_url(
            hass,
            HLS_UPSTREAM,
            refresh=AsyncMock(return_value="https://media.example/fresh.m3u8"),
            entry_id="entry-a",
        )
    session_id = signed.split("/record_hls/", 1)[1].split("/", 1)[0]

    with patch(
        "custom_components.imou_life.record_hls_proxy.async_fetch_url_response",
        new_callable=AsyncMock,
        return_value=_range_response(150, 174, b"normalized range"),
    ) as fetch:
        response = await ImouRecordHlsSegmentView().get(
            _segment_request(hass), session_id, "1"
        )

    assert response.body == b"normalized range"
    fetch.assert_awaited_once_with(
        hass,
        f"{absolute}#second",
        range_header="bytes=150-174",
    )


@pytest.mark.parametrize(
    ("first_url", "second_url"),
    [
        (
            "https://media-a.example/segment.ts",
            "https://media-b.example/segment.ts",
        ),
        (
            "https://media.example/a.ts",
            "https://media.example/b.ts",
        ),
    ],
)
@pytest.mark.asyncio
async def test_implicit_byterange_rejects_different_resource(
    hass: HomeAssistant,
    first_url: str,
    second_url: str,
) -> None:
    playlist = f"""#EXTM3U
#EXT-X-BYTERANGE:100@50
#EXTINF:2,
{first_url}
#EXT-X-BYTERANGE:25
#EXTINF:2,
{second_url}
"""
    session_id = await _create_session_from_playlist(
        hass,
        playlist,
        [first_url, second_url],
    )

    with (
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_response",
            new_callable=AsyncMock,
        ) as fetch,
        pytest.raises(web.HTTPBadGateway),
    ):
        await ImouRecordHlsSegmentView().get(_segment_request(hass), session_id, "1")

    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_rejects_changed_absolute_byterange(
    hass: HomeAssistant,
) -> None:
    initial = """#EXTM3U
#EXT-X-BYTERANGE:100@100
#EXTINF:2,
https://media.example/segment.ts?token=old
"""
    refreshed = initial.replace("100@100", "100@150").replace(
        "token=old", "token=fresh"
    )
    refresh = AsyncMock(return_value="https://media.example/fresh.m3u8")
    session_id = await _create_session_from_playlist(
        hass,
        initial,
        ["https://media.example/segment.ts?token=old"],
        refresh,
    )

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
                ["https://media.example/segment.ts?token=fresh"],
            ),
        ),
        pytest.raises(web.HTTPBadGateway),
    ):
        await ImouRecordHlsSegmentView().get(_segment_request(hass), session_id, "0")

    fetch.assert_awaited_once_with(
        hass,
        "https://media.example/segment.ts?token=old",
        range_header="bytes=100-199",
    )


@pytest.mark.asyncio
async def test_malformed_byterange_returns_502_without_fetch(
    hass: HomeAssistant,
) -> None:
    playlist = """#EXTM3U
#EXT-X-BYTERANGE:not-a-range
#EXTINF:2,
https://media.example/segment.ts
"""
    session_id = await _create_session_from_playlist(
        hass,
        playlist,
        ["https://media.example/segment.ts"],
    )

    with (
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_response",
            new_callable=AsyncMock,
        ) as fetch,
        pytest.raises(web.HTTPBadGateway),
    ):
        await ImouRecordHlsSegmentView().get(_segment_request(hass), session_id, "0")

    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_client_range_cannot_cross_byterange_descriptor(
    hass: HomeAssistant,
) -> None:
    playlist = """#EXTM3U
#EXT-X-BYTERANGE:100@100
#EXTINF:2,
https://media.example/segment.ts
"""
    session_id = await _create_session_from_playlist(
        hass,
        playlist,
        ["https://media.example/segment.ts"],
    )
    request = _segment_request(hass)
    request.headers = {"Range": "bytes=50-250"}

    with (
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_response",
            new_callable=AsyncMock,
        ) as fetch,
        pytest.raises(web.HTTPBadGateway),
    ):
        await ImouRecordHlsSegmentView().get(request, session_id, "0")

    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_maps_unique_path_without_media_sequence(
    hass: HomeAssistant,
) -> None:
    initial = """#EXTM3U
#EXTINF:2,
https://media.example/a.ts?token=old
#EXTINF:2,
https://media.example/b.ts?token=old
"""
    refreshed = initial.replace("token=old", "token=fresh")
    refresh = AsyncMock(return_value="https://media.example/fresh.m3u8")
    with (
        patch(
            "custom_components.imou_life.record_hls_proxy._load_media_playlist",
            new_callable=AsyncMock,
            return_value=(
                initial,
                [
                    "https://media.example/a.ts?token=old",
                    "https://media.example/b.ts?token=old",
                ],
            ),
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.async_sign_path",
            side_effect=lambda _hass, path, *_args, **_kwargs: path,
        ),
    ):
        signed = await async_create_record_hls_proxy_playlist_url(
            hass, HLS_UPSTREAM, refresh=refresh, entry_id="entry-a"
        )
    session_id = signed.split("/record_hls/", 1)[1].split("/", 1)[0]

    with (
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_response",
            new_callable=AsyncMock,
            side_effect=[
                _upstream_response(403),
                _upstream_response(200, b"segment b"),
            ],
        ) as fetch,
        patch(
            "custom_components.imou_life.record_hls_proxy._load_media_playlist",
            new_callable=AsyncMock,
            return_value=(
                refreshed,
                [
                    "https://media.example/a.ts?token=fresh",
                    "https://media.example/b.ts?token=fresh",
                ],
            ),
        ),
    ):
        response = await ImouRecordHlsSegmentView().get(
            _segment_request(hass), session_id, "1"
        )

    assert response.body == b"segment b"
    assert fetch.await_args_list[1].args[1].endswith("b.ts?token=fresh")


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
            hass, HLS_UPSTREAM, refresh=refresh, entry_id="entry-a"
        )
        second = await async_create_record_hls_proxy_playlist_url(
            hass,
            HLS_UPSTREAM,
            refresh=refresh,
            entry_id="entry-a",
            expires_at=now + 30,
        )
        expired = await async_create_record_hls_proxy_playlist_url(
            hass,
            f"https://media.example/record.m3u8?expire={int(now - 1)}",
            refresh=refresh,
            entry_id="entry-a",
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
async def test_session_expiry_timer_releases_refresh_state(
    hass: HomeAssistant,
) -> None:
    scheduled: list[tuple[float, object, MagicMock]] = []

    def schedule(_hass, delay, action):
        cancel = MagicMock()
        scheduled.append((float(delay), action, cancel))
        return cancel

    refresh = AsyncMock(return_value="https://media.example/fresh.m3u8")
    now = time.time()
    with patch(
        "custom_components.imou_life.record_hls_proxy.async_call_later",
        create=True,
        side_effect=schedule,
    ):
        session_id = await _create_refreshable_session(hass, refresh)
        record = hass.data[DATA_RECORD_HLS_PROXY][session_id]
        assert len(scheduled) == 1
        assert 590 <= scheduled[0][0] <= 600

        with patch(
            "custom_components.imou_life.record_hls_proxy.time.time",
            return_value=now + 601,
        ):
            scheduled[0][1](MagicMock())

    assert session_id not in hass.data[DATA_RECORD_HLS_PROXY]
    assert record.active is False
    assert record.refresh is None


@pytest.mark.asyncio
async def test_stale_cleanup_timer_is_safe_after_reschedule(
    hass: HomeAssistant,
) -> None:
    scheduled: list[tuple[object, MagicMock]] = []

    def schedule(_hass, _delay, action):
        cancel = MagicMock()
        scheduled.append((action, cancel))
        return cancel

    refresh = AsyncMock(return_value="https://media.example/fresh.m3u8")
    with patch(
        "custom_components.imou_life.record_hls_proxy.async_call_later",
        create=True,
        side_effect=schedule,
    ):
        first_id = await _create_refreshable_session(hass, refresh)
        second_id = await _create_refreshable_session(hass, refresh)
        assert scheduled[0][1].call_count == 1

        with patch(
            "custom_components.imou_life.record_hls_proxy.time.time",
            return_value=time.time() + 601,
        ):
            scheduled[0][0](MagicMock())

    assert set(hass.data[DATA_RECORD_HLS_PROXY]) == {first_id, second_id}


@pytest.mark.asyncio
async def test_unload_removes_only_entry_sessions_and_reschedules_timer(
    hass: HomeAssistant,
) -> None:
    scheduled: list[tuple[object, MagicMock]] = []

    def schedule(_hass, _delay, action):
        cancel = MagicMock()
        scheduled.append((action, cancel))
        return cancel

    refresh_a = AsyncMock(return_value="https://media.example/a.m3u8")
    refresh_b = AsyncMock(return_value="https://media.example/b.m3u8")
    with patch(
        "custom_components.imou_life.record_hls_proxy.async_call_later",
        create=True,
        side_effect=schedule,
    ):
        session_a = await _create_refreshable_session(hass, refresh_a)
        session_b = await _create_refreshable_session(
            hass,
            refresh_b,
            entry_id="entry-b",
        )
        sessions = hass.data[DATA_RECORD_HLS_PROXY]
        record_a = sessions[session_a]
        record_b = sessions[session_b]

        entry_a = MockConfigEntry(
            domain=DOMAIN,
            data=USER_INPUT,
            entry_id="entry-a",
        )
        entry_a.add_to_hass(hass)
        entry_b = MockConfigEntry(
            domain=DOMAIN,
            data=USER_INPUT,
            entry_id="entry-b",
        )
        entry_b.add_to_hass(hass)
        register_playback_source(
            hass,
            "hls-a",
            entry_a.entry_id,
            AsyncMock(return_value=RecordHlsDescriptor("source-a")),
        )
        register_playback_source(
            hass,
            "hls-b",
            entry_b.entry_id,
            AsyncMock(return_value=RecordHlsDescriptor("source-b")),
        )
        with patch(
            "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
            AsyncMock(return_value=True),
        ):
            assert await async_unload_entry(hass, entry_a) is True

    assert session_a not in hass.data[DATA_RECORD_HLS_PROXY]
    assert session_b in hass.data[DATA_RECORD_HLS_PROXY]
    assert record_a.active is False
    assert record_a.refresh is None
    assert record_b.active is True
    assert record_b.refresh is refresh_b
    assert scheduled[-2][1].call_count == 1
    assert "hls-a" not in hass.data[DATA_RECORD_PLAYBACK_HLS]["owners"]
    assert hass.data[DATA_RECORD_PLAYBACK_HLS]["owners"]["hls-b"] == "entry-b"


@pytest.mark.asyncio
async def test_failed_platform_unload_preserves_all_playback_state(
    hass: HomeAssistant,
) -> None:
    entry_a = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, entry_id="entry-a")
    entry_b = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, entry_id="entry-b")
    entry_a.add_to_hass(hass)
    entry_b.add_to_hass(hass)
    hls_session = await _create_refreshable_session(
        hass,
        AsyncMock(return_value="https://media.example/a.m3u8"),
        entry_id=entry_a.entry_id,
    )
    register_playback_source(
        hass,
        "hls-a",
        entry_a.entry_id,
        AsyncMock(return_value=RecordHlsDescriptor("source-a")),
    )
    register_playback_source(
        hass,
        "hls-b",
        entry_b.entry_id,
        AsyncMock(return_value=RecordHlsDescriptor("source-b")),
    )

    with patch(
        "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
        AsyncMock(return_value=False),
    ):
        assert await async_unload_entry(hass, entry_a) is False

    store = hass.data[DATA_RECORD_PLAYBACK_HLS]
    assert store["owners"] == {"hls-a": "entry-a", "hls-b": "entry-b"}
    assert {"hls-a", "hls-b"} <= store["sources"].keys()
    assert hls_session in hass.data[DATA_RECORD_HLS_PROXY]


@pytest.mark.asyncio
async def test_platform_unload_exception_preserves_playback_state(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, entry_id="entry-a")
    entry.add_to_hass(hass)
    hls_session = await _create_refreshable_session(
        hass,
        AsyncMock(return_value="https://media.example/a.m3u8"),
        entry_id=entry.entry_id,
    )
    provider = AsyncMock(return_value=RecordHlsDescriptor("source-a"))
    register_playback_source(hass, "hls-a", entry.entry_id, provider)

    with (
        patch(
            "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
            AsyncMock(side_effect=RuntimeError("unload failed")),
        ),
        pytest.raises(RuntimeError, match="unload failed"),
    ):
        await async_unload_entry(hass, entry)

    store = hass.data[DATA_RECORD_PLAYBACK_HLS]
    assert store["owners"]["hls-a"] == entry.entry_id
    assert "hls-a" in store["sources"]
    assert hls_session in hass.data[DATA_RECORD_HLS_PROXY]


@pytest.mark.asyncio
async def test_unload_awaits_in_progress_refresh_cancellation(
    hass: HomeAssistant,
) -> None:
    refresh_started = asyncio.Event()
    refresh_cancelled = asyncio.Event()

    async def refresh_source() -> str:
        refresh_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            refresh_cancelled.set()
        return "https://media.example/fresh.m3u8"

    session_id = await _create_refreshable_session(hass, refresh_source)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=USER_INPUT,
        entry_id="entry-a",
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_response",
            new_callable=AsyncMock,
            return_value=_upstream_response(403),
        ),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
            AsyncMock(return_value=True),
        ),
    ):
        request = asyncio.create_task(
            ImouRecordHlsSegmentView().get(_segment_request(hass), session_id, "0")
        )
        await refresh_started.wait()
        assert await async_unload_entry(hass, entry) is True
        assert refresh_cancelled.is_set()
        with pytest.raises(web.HTTPNotFound):
            await request


@pytest.mark.asyncio
async def test_expiry_during_segment_fetch_does_not_refresh(
    hass: HomeAssistant,
) -> None:
    scheduled: list[object] = []

    def schedule(_hass, _delay, action):
        scheduled.append(action)
        return MagicMock()

    refresh = AsyncMock(return_value="https://media.example/fresh.m3u8")
    fetch_started = asyncio.Event()
    allow_fetch = asyncio.Event()

    async def fetch(*_args, **_kwargs):
        fetch_started.set()
        await allow_fetch.wait()
        return _upstream_response(403)

    now = time.time()
    with (
        patch(
            "custom_components.imou_life.record_hls_proxy.async_call_later",
            create=True,
            side_effect=schedule,
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_response",
            side_effect=fetch,
        ),
    ):
        session_id = await _create_refreshable_session(hass, refresh)
        request = asyncio.create_task(
            ImouRecordHlsSegmentView().get(_segment_request(hass), session_id, "0")
        )
        await fetch_started.wait()
        with patch(
            "custom_components.imou_life.record_hls_proxy.time.time",
            return_value=now + 601,
        ):
            scheduled[-1](MagicMock())
        allow_fetch.set()
        with pytest.raises(web.HTTPNotFound):
            await request

    refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_cloud_clip_uses_hls(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    object.__setattr__(entry, "state", ConfigEntryState.LOADED)

    source = ImouRecordMediaSource(hass)
    item = MediaSourceItem(hass, DOMAIN, IDENTIFIER.format(entry=entry.entry_id), None)
    now = time.time()
    runtime = _runtime_with_hls_url({"url": HLS_UPSTREAM, "expireTime": 30})
    signed_playlist = "/api/imou_life/record_hls/sid/playlist.m3u8?authSig=x"

    with (
        patch(
            "custom_components.imou_life.media_source.get_runtime_data",
            return_value=runtime,
        ),
        patch(
            "custom_components.imou_life.media_source.async_create_record_hls_proxy_playlist_url",
            new_callable=AsyncMock,
            return_value=signed_playlist,
        ) as proxy_mock,
        patch(
            "custom_components.imou_life.media_source.register_playback_source",
        ) as register_mock,
        patch(
            "custom_components.imou_life.media_source.signed_record_playlist_url",
            return_value="/api/imou_life/record_play/abc.m3u8?authSig=x",
        ) as signed_mock,
        patch(
            "custom_components.imou_life.media_source.get_url",
            return_value="http://ha.local:8123",
        ),
    ):
        play = await source.async_resolve_media(item)
        descriptor = await register_mock.call_args.args[3]()

    register_mock.assert_called_once()
    signed_mock.assert_called_once()
    assert register_mock.call_args.kwargs["live_timeout_seconds"] == (
        RECORD_REMUX_TIMEOUT_SECONDS
    )
    assert proxy_mock.await_args.kwargs["stream_ttl_seconds"] == (
        RECORD_REMUX_TIMEOUT_SECONDS
    )
    assert now + 29 <= proxy_mock.await_args.kwargs["expires_at"] <= now + 31
    assert descriptor.playlist_url == f"http://ha.local:8123{signed_playlist}"
    create_hls = (
        runtime.coordinator.device_manager.delegate.async_create_device_record_hls
    )
    assert create_hls.await_count == 1
    assert play.mime_type == MIME_HLS
    assert play.url == "/api/imou_life/record_play/abc.m3u8?authSig=x"


@pytest.mark.asyncio
async def test_resolve_cloud_clip_mints_fresh_source_when_play_is_delayed(
    hass: HomeAssistant,
) -> None:
    """A player that asks for the playlist long after resolving gets a fresh URL."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    object.__setattr__(entry, "state", ConfigEntryState.LOADED)

    source = ImouRecordMediaSource(hass)
    item = MediaSourceItem(hass, DOMAIN, IDENTIFIER.format(entry=entry.entry_id), None)
    now = time.time()
    runtime = _runtime_with_hls_url()

    with (
        patch(
            "custom_components.imou_life.media_source.get_runtime_data",
            return_value=runtime,
        ),
        patch(
            "custom_components.imou_life.media_source.async_create_record_hls_proxy_playlist_url",
            new_callable=AsyncMock,
            return_value="/api/imou_life/record_hls/sid/playlist.m3u8?authSig=x",
        ),
        patch(
            "custom_components.imou_life.media_source.register_playback_source",
        ) as register_mock,
        patch(
            "custom_components.imou_life.media_source.signed_record_playlist_url",
            return_value="/api/imou_life/record_play/abc.m3u8?authSig=x",
        ),
        patch(
            "custom_components.imou_life.media_source.get_url",
            return_value="http://ha.local:8123",
        ),
    ):
        await source.async_resolve_media(item)
        provider = register_mock.call_args.args[3]
        with patch(
            "custom_components.imou_life.media_source.time.time",
            return_value=now + SOURCE_REUSE_SECONDS + 1,
        ):
            descriptor = await provider()

    create_hls = (
        runtime.coordinator.device_manager.delegate.async_create_device_record_hls
    )
    assert create_hls.await_count == 2
    assert descriptor.playlist_url == (
        "http://ha.local:8123/api/imou_life/record_hls/sid/playlist.m3u8?authSig=x"
    )


@pytest.mark.asyncio
async def test_resolve_cloud_clip_mints_fresh_source_before_url_expiry(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    object.__setattr__(entry, "state", ConfigEntryState.LOADED)
    source = ImouRecordMediaSource(hass)
    item = MediaSourceItem(hass, DOMAIN, IDENTIFIER.format(entry=entry.entry_id), None)
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
        ),
        patch(
            "custom_components.imou_life.media_source.register_playback_source",
        ) as register_mock,
        patch(
            "custom_components.imou_life.media_source.signed_record_playlist_url",
            return_value="/api/imou_life/record_play/abc.m3u8?authSig=x",
        ),
        patch(
            "custom_components.imou_life.media_source.get_url",
            return_value="http://ha.local:8123",
        ),
    ):
        await source.async_resolve_media(item)
        provider = register_mock.call_args.args[3]
        with patch(
            "custom_components.imou_life.media_source.time.time",
            return_value=now + 26,
        ):
            await provider()

    create_hls = (
        runtime.coordinator.device_manager.delegate.async_create_device_record_hls
    )
    assert create_hls.await_count == 2


@pytest.mark.asyncio
async def test_resolve_cloud_clip_does_not_register_after_entry_unload(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    object.__setattr__(entry, "state", ConfigEntryState.LOADED)
    source = ImouRecordMediaSource(hass)
    item = MediaSourceItem(hass, DOMAIN, IDENTIFIER.format(entry=entry.entry_id), None)
    runtime = _runtime_with_hls_url()
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed_source(*_args: object, **_kwargs: object) -> dict[str, str]:
        started.set()
        await release.wait()
        return {"url": HLS_UPSTREAM}

    create_hls = (
        runtime.coordinator.device_manager.delegate.async_create_device_record_hls
    )
    create_hls.side_effect = delayed_source

    with (
        patch(
            "custom_components.imou_life.media_source.get_runtime_data",
            return_value=runtime,
        ),
        patch(
            "custom_components.imou_life.media_source.register_playback_source",
        ) as register_mock,
    ):
        resolve = asyncio.create_task(source.async_resolve_media(item))
        await started.wait()
        object.__setattr__(entry, "state", ConfigEntryState.SETUP_ERROR)
        release.set()
        with pytest.raises(Unresolvable):
            await resolve

    register_mock.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_local_clip_uses_hls(hass: HomeAssistant) -> None:
    """Local clips also play as HLS; the source is minted when the player asks."""
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    object.__setattr__(entry, "state", ConfigEntryState.LOADED)
    runtime = _runtime_with_hls_url()
    source = ImouRecordMediaSource(hass)
    item = MediaSourceItem(
        hass,
        DOMAIN,
        LOCAL_IDENTIFIER.format(entry=entry.entry_id),
        None,
    )
    create_hls = (
        runtime.coordinator.device_manager.delegate.async_create_device_record_hls
    )
    prepared = object()

    with (
        patch(
            "custom_components.imou_life.media_source.get_runtime_data",
            return_value=runtime,
        ),
        patch(
            "custom_components.imou_life.media_source.async_prepare_local_record_playback",
            new_callable=AsyncMock,
            return_value=prepared,
        ) as prepare_mock,
        patch(
            "custom_components.imou_life.media_source.async_create_record_hls_proxy_playlist_url",
            new_callable=AsyncMock,
            return_value="/api/imou_life/record_hls/sid/playlist.m3u8?authSig=x",
        ) as proxy_mock,
        patch(
            "custom_components.imou_life.media_source.get_url",
            return_value="http://ha.local:8123",
        ),
        patch(
            "custom_components.imou_life.media_source.register_playback_source",
        ) as register_mock,
        patch(
            "custom_components.imou_life.media_source.signed_record_playlist_url",
            return_value="/api/imou_life/record_play/abc.m3u8?authSig=x",
        ) as signed_mock,
    ):
        play = await source.async_resolve_media(item)
        assert create_hls.await_count == 0
        descriptor = await register_mock.call_args.args[3]()

    register_mock.assert_called_once()
    assert register_mock.call_args.kwargs["live_timeout_seconds"] == (
        _local_live_timeout_seconds("2026-11-01 00:06:55", "2026-11-01 00:08:10")
    )
    signed_mock.assert_called_once()
    create_hls.assert_awaited_once()
    prepare_mock.assert_awaited_once()
    assert create_hls.await_args.kwargs["record_type"] == "localRecord"
    assert create_hls.await_args.kwargs["file_name"] == "/record/file.dav"
    # 75 s clip plus a safety margin, never below 30 minutes.
    assert proxy_mock.await_args.kwargs["stream_ttl_seconds"] == 1875.0
    assert proxy_mock.await_args.kwargs["expires_at"] is None
    assert proxy_mock.await_args.kwargs["prepared_playback"] is prepared
    assert descriptor.playlist_url == (
        "http://ha.local:8123/api/imou_life/record_hls/sid/playlist.m3u8?authSig=x"
    )
    assert play.mime_type == MIME_HLS
    assert play.url == "/api/imou_life/record_play/abc.m3u8?authSig=x"


def test_local_live_timeout_uses_duration_for_historical_clips() -> None:
    assert (
        _local_live_timeout_seconds("2026-11-01 00:00:00", "2026-11-01 00:00:30")
        == 300.0
    )
    assert (
        _local_live_timeout_seconds("2026-11-01 00:00:00", "2026-11-01 01:00:00")
        == 3720.0
    )
    assert _local_live_timeout_seconds("not-a-date", "2026-11-01 00:00:00") == (
        4 * 3600.0
    )


def test_local_live_ttl_covers_the_clip() -> None:
    assert _local_stream_ttl_seconds("2026-11-01 00:00:00", "2026-11-01 00:00:30") == (
        1830.0
    )
    assert _local_stream_ttl_seconds("2026-11-01 00:00:00", "2026-11-01 01:00:00") == (
        5400.0
    )
    assert _local_stream_ttl_seconds("2026-11-01 00:00:00", "2026-11-02 23:00:00") == (
        86400.0
    )
    assert _local_stream_ttl_seconds("not-a-date", "2026-11-01 00:00:00") == 14400.0


@pytest.mark.asyncio
async def test_resolve_cloud_clip_hls_without_stream_component(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    object.__setattr__(entry, "state", ConfigEntryState.LOADED)
    assert "stream" not in hass.config.components

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
            "custom_components.imou_life.media_source.signed_record_playlist_url",
            return_value="/api/imou_life/record_play/abc.m3u8?authSig=x",
        ),
    ):
        play = await source.async_resolve_media(item)

    register_mock.assert_called_once()
    assert play.mime_type == MIME_HLS
    assert play.url == "/api/imou_life/record_play/abc.m3u8?authSig=x"


@pytest.mark.asyncio
async def test_resolve_local_clip_uses_home_assistant_address(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    object.__setattr__(entry, "state", ConfigEntryState.LOADED)
    runtime = _runtime_with_hls_url()
    source = ImouRecordMediaSource(hass)
    item = MediaSourceItem(
        hass, DOMAIN, LOCAL_IDENTIFIER.format(entry=entry.entry_id), None
    )
    with (
        patch(
            "custom_components.imou_life.media_source.get_runtime_data",
            return_value=runtime,
        ),
        patch(
            "custom_components.imou_life.media_source.register_playback_source",
        ) as register_mock,
        patch(
            "custom_components.imou_life.media_source.async_prepare_local_record_playback",
            new_callable=AsyncMock,
            return_value=object(),
        ),
        patch(
            "custom_components.imou_life.media_source.signed_record_playlist_url",
            return_value="/api/imou_life/record_play/abc.m3u8?authSig=x",
        ),
        patch(
            "custom_components.imou_life.media_source.async_create_record_hls_proxy_playlist_url",
            new_callable=AsyncMock,
            return_value="/api/imou_life/record_hls/sid/playlist.m3u8?authSig=y",
        ),
        patch(
            "custom_components.imou_life.media_source.get_url",
            return_value="http://ha.local:8123",
        ),
    ):
        play = await source.async_resolve_media(item)
        descriptor = await register_mock.call_args.args[3]()

    assert "lechange.com" not in play.url
    assert play.url.startswith("/api/imou_life/record_play/")
    assert descriptor.playlist_url == (
        "http://ha.local:8123/api/imou_life/record_hls/sid/playlist.m3u8?authSig=y"
    )


@pytest.mark.asyncio
async def test_resolve_local_clip_hls_needs_no_stream_component(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    object.__setattr__(entry, "state", ConfigEntryState.LOADED)
    assert "stream" not in hass.config.components

    source = ImouRecordMediaSource(hass)
    item = MediaSourceItem(
        hass, DOMAIN, LOCAL_IDENTIFIER.format(entry=entry.entry_id), None
    )

    with (
        patch(
            "custom_components.imou_life.media_source.get_runtime_data",
            return_value=_runtime_with_hls_url(),
        ),
        patch(
            "custom_components.imou_life.media_source.register_playback_source",
        ) as register_mock,
        patch(
            "custom_components.imou_life.media_source.signed_record_playlist_url",
            return_value="/api/imou_life/record_play/abc.m3u8?authSig=x",
        ),
    ):
        play = await source.async_resolve_media(item)

    register_mock.assert_called_once()
    assert play.mime_type == MIME_HLS
    assert play.url == "/api/imou_life/record_play/abc.m3u8?authSig=x"


@pytest.mark.asyncio
async def test_proxy_origin_prefers_loopback_for_plain_http(
    hass: HomeAssistant,
) -> None:
    hass.config.api = SimpleNamespace(
        local_ip="192.168.1.50",
        host="192.168.1.50",
        port=8123,
        use_ssl=False,
    )
    with patch(
        "custom_components.imou_life.record_hls_proxy.get_url",
        return_value="https://ha.example.com",
    ):
        assert record_hls_proxy_origin(hass) == "http://127.0.0.1:8123"

    hass.config.api = SimpleNamespace(
        local_ip="192.168.1.50",
        host="192.168.1.50",
        port=8123,
        use_ssl=True,
    )
    with patch(
        "custom_components.imou_life.record_hls_proxy.get_url",
        return_value="https://ha.example.com",
    ):
        assert record_hls_proxy_origin(hass) == "https://127.0.0.1:8123"


@pytest.mark.asyncio
async def test_load_media_playlist_pins_same_segment_host_once(
    hass: HomeAssistant,
) -> None:
    playlist = """#EXTM3U
#EXTINF:2,
https://media.example:7080/a.ts
#EXTINF:2,
https://media.example:7080/b.ts
#EXTINF:2,
https://media.example:7080/c.ts
"""
    with (
        patch(
            "custom_components.imou_life.record_hls_proxy.async_fetch_url_text",
            new_callable=AsyncMock,
            return_value=playlist,
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy._async_prefetch_upstream_segments",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.imou_life.record_hls_proxy.async_pin_public_url",
            new_callable=AsyncMock,
        ) as pin_mock,
        patch(
            "custom_components.imou_life.record_hls_proxy.async_sign_path",
            side_effect=lambda _hass, path, *_args, **_kwargs: path,
        ),
    ):
        await async_create_record_hls_proxy_playlist_url(
            hass,
            "https://media.example:7080/index.m3u8",
            refresh=AsyncMock(),
            entry_id="entry-a",
        )

    assert pin_mock.await_count == 1


@pytest.mark.asyncio
async def test_stable_resolve_failure_creates_playback_issue(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, entry_id="entry-a")
    entry.add_to_hass(hass)
    object.__setattr__(entry, "state", ConfigEntryState.LOADED)
    runtime = _runtime_with_hls_url()
    runtime.coordinator.device_manager.delegate.async_create_device_record_hls = (
        AsyncMock(
            side_effect=RequestFailedException(
                "No permission", api_code=ERROR_CODE_NO_PERMISSION
            )
        )
    )
    item = MediaSourceItem(hass, DOMAIN, IDENTIFIER.format(entry=entry.entry_id), None)

    with (
        patch(
            "custom_components.imou_life.media_source.get_runtime_data",
            return_value=runtime,
        ),
        pytest.raises(Unresolvable) as captured,
    ):
        await ImouRecordMediaSource(hass).async_resolve_media(item)

    assert captured.value.translation_placeholders is None
    assert (
        DOMAIN,
        f"{ISSUE_RECORD_PLAYBACK}_{entry.entry_id}",
    ) in ir.async_get(hass).issues


@pytest.mark.asyncio
async def test_empty_hls_resolve_creates_playback_issue(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, entry_id="entry-a")
    entry.add_to_hass(hass)
    object.__setattr__(entry, "state", ConfigEntryState.LOADED)
    item = MediaSourceItem(hass, DOMAIN, IDENTIFIER.format(entry=entry.entry_id), None)

    with (
        patch(
            "custom_components.imou_life.media_source.get_runtime_data",
            return_value=_runtime_with_hls_url({"url": ""}),
        ),
        pytest.raises(Unresolvable) as captured,
    ):
        await ImouRecordMediaSource(hass).async_resolve_media(item)

    assert captured.value.translation_placeholders is None
    assert (
        DOMAIN,
        f"{ISSUE_RECORD_PLAYBACK}_{entry.entry_id}",
    ) in ir.async_get(hass).issues


@pytest.mark.asyncio
async def test_successful_resolve_clears_playback_issue(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, entry_id="entry-a")
    entry.add_to_hass(hass)
    object.__setattr__(entry, "state", ConfigEntryState.LOADED)
    async_create_record_playback_issue(
        hass,
        entry.entry_id,
        RequestFailedException("No permission", api_code=ERROR_CODE_NO_PERMISSION),
    )
    item = MediaSourceItem(hass, DOMAIN, IDENTIFIER.format(entry=entry.entry_id), None)

    with (
        patch(
            "custom_components.imou_life.media_source.get_runtime_data",
            return_value=_runtime_with_hls_url(),
        ),
        patch(
            "custom_components.imou_life.media_source.signed_record_playlist_url",
            return_value="/api/imou_life/record_play/abc.m3u8?authSig=x",
        ),
    ):
        await ImouRecordMediaSource(hass).async_resolve_media(item)

    assert (
        DOMAIN,
        f"{ISSUE_RECORD_PLAYBACK}_{entry.entry_id}",
    ) not in ir.async_get(hass).issues


PLAYBACK_PLAYLIST = """#EXTM3U
#EXT-X-VERSION:7
#EXT-X-TARGETDURATION:2
#EXT-X-PLAYLIST-TYPE:EVENT
#EXT-X-MAP:URI="init.mp4"
#EXTINF:2.000000,
seg_00000.m4s
#EXT-X-ENDLIST
"""
LIVE_PLAYBACK_PLAYLIST = PLAYBACK_PLAYLIST.removesuffix("#EXT-X-ENDLIST\n")


def _playback_cache_dir(hass: HomeAssistant, cache_id: str) -> Path:
    return Path(hass.config.path()) / CACHE_SUBDIR / cache_id


def _write_playback_cache(
    hass: HomeAssistant, cache_id: str, playlist: str = PLAYBACK_PLAYLIST
) -> Path:
    directory = _playback_cache_dir(hass, cache_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "index.m3u8").write_text(playlist, encoding="utf-8")
    (directory / "init.mp4").write_bytes(b"init")
    (directory / "seg_00000.m4s").write_bytes(b"segment")
    return directory


def test_hls_command_remuxes_to_fmp4_and_pins_hvc1(tmp_path: Path) -> None:
    command = _hls_command("http://127.0.0.1:8123/x.m3u8", tmp_path, hevc=True)

    assert command[command.index("-c") + 1] == "copy"
    assert command[command.index("-tag:v") + 1] == "hvc1"
    assert command[command.index("-live_start_index") + 1] == "0"
    assert command[command.index("-c:a") + 1] == "aac"
    assert command[command.index("-af") + 1] == ("aresample=async=1:first_pts=0,apad")
    assert "-shortest" in command
    assert "-bsf:a" not in command
    assert command[command.index("-hls_segment_type") + 1] == "fmp4"
    assert command[command.index("-hls_playlist_type") + 1] == "event"
    assert command[command.index("-hls_fmp4_init_filename") + 1] == "init.mp4"
    assert "independent_segments" in command[command.index("-hls_flags") + 1]
    assert str(tmp_path / "seg_%05d.m4s") in command
    assert command[-1] == str(tmp_path / "index.m3u8")


def test_hls_command_leaves_h264_tagged_as_is(tmp_path: Path) -> None:
    assert "-tag:v" not in _hls_command(
        "http://127.0.0.1:8123/x.m3u8", tmp_path, hevc=False
    )


def test_prune_playback_cache_keeps_active_partial_remux(tmp_path: Path) -> None:
    finished = tmp_path / "finished"
    finished.mkdir()
    (finished / "index.m3u8").write_text(PLAYBACK_PLAYLIST, encoding="utf-8")
    active = tmp_path / "live.0f0f0f0f.part"
    active.mkdir()
    stale = tmp_path / "old.12345678.part"
    stale.mkdir()

    _prune_playback_cache(tmp_path, keep_cache_ids={"finished", "live"})

    assert finished.is_dir()
    assert active.is_dir()
    assert not stale.exists()


def test_validate_proxy_source_accepts_only_signed_local_playlists() -> None:
    _validate_proxy_source(_local_hls_descriptor())
    for descriptor in (
        RecordHlsDescriptor("https://media.example/x.m3u8"),
        RecordHlsDescriptor(LOCAL_HLS_PROXY, proxy_origin="https://ha.local:8123"),
        RecordHlsDescriptor(
            LOCAL_HLS_PROXY.replace("0123456789abcdef0123456789abcdef", "session"),
            proxy_origin="http://ha.local:8123",
        ),
        RecordHlsDescriptor(
            LOCAL_HLS_PROXY.replace("?authSig=x", ""),
            proxy_origin="http://ha.local:8123",
        ),
    ):
        with pytest.raises(ValueError):
            _validate_proxy_source(descriptor)


@pytest.mark.asyncio
async def test_signed_playlist_url_is_authenticated(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, "http", {})
    cache_id = record_playback_cache_id("signed")

    url = signed_record_playlist_url(hass, cache_id, 60)

    assert url.startswith(f"/api/imou_life/record_play/{cache_id}.m3u8?")
    assert "authSig=" in url


@pytest.mark.asyncio
async def test_playlist_view_serves_completed_cache_with_signed_segments(
    hass: HomeAssistant,
) -> None:
    assert await async_setup_component(hass, "http", {})
    cache_id = record_playback_cache_id("complete")
    _write_playback_cache(hass, cache_id)
    register_playback_source(
        hass, cache_id, "entry-a", AsyncMock(return_value=_local_hls_descriptor())
    )

    with patch(
        "custom_components.imou_life.record_playback_hls.asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
    ) as exec_mock:
        response = await ImouRecordPlaybackHlsView().get(
            _segment_request(hass), cache_id
        )

    exec_mock.assert_not_called()
    assert response.content_type == "application/vnd.apple.mpegurl"
    assert response.headers["Cache-Control"] == "no-store"
    assert (
        f'#EXT-X-MAP:URI="/api/imou_life/record_play_file/{cache_id}/init.mp4?'
        in response.text
    )
    assert (
        "\n/api/imou_life/record_play_file/"
        f"{cache_id}/seg_00000.m4s?authSig=" in response.text
    )
    assert "\nseg_00000.m4s\n" not in response.text


@pytest.mark.asyncio
async def test_file_view_serves_cached_segment_with_range_support(
    hass: HomeAssistant,
) -> None:
    cache_id = record_playback_cache_id("files")
    directory = _write_playback_cache(hass, cache_id)
    register_playback_source(
        hass, cache_id, "entry-a", AsyncMock(return_value=_local_hls_descriptor())
    )

    response = await ImouRecordPlaybackFileView().get(
        _segment_request(hass), cache_id, "seg_00000.m4s"
    )

    assert isinstance(response, web.FileResponse)
    assert response.status == 200
    assert response.headers["Content-Type"] == "video/mp4"
    assert response.headers["Cache-Control"] == "private, max-age=86400"
    assert Path(response._path) == directory / "seg_00000.m4s"


@pytest.mark.asyncio
async def test_live_file_view_backs_off_while_ffmpeg_writes_the_segment(
    hass: HomeAssistant,
) -> None:
    """Waiting for a live segment polls with growing delays, not in a spin."""
    cache_id = record_playback_cache_id("waiting-file")
    register_playback_source(
        hass, cache_id, "entry-a", AsyncMock(return_value=_local_hls_descriptor())
    )
    job = HlsPlaybackJob(
        cache_id=cache_id,
        entry_id="entry-a",
        part_dir=Path(hass.config.path()) / CACHE_SUBDIR / "part",
        final_dir=Path(hass.config.path()) / CACHE_SUBDIR / cache_id,
    )
    hass.data[DATA_RECORD_PLAYBACK_HLS]["jobs"][cache_id] = job
    segment = job.part_dir / "seg_00000.m4s"

    with (
        patch(
            "custom_components.imou_life.record_playback_hls._find_segment_file",
            side_effect=[None, None, segment],
        ),
        patch(
            "custom_components.imou_life.record_playback_hls.asyncio.sleep",
            new_callable=AsyncMock,
        ) as sleep_mock,
    ):
        response = await ImouRecordPlaybackFileView().get(
            _segment_request(hass), cache_id, "seg_00000.m4s"
        )

    if job.stop_timer is not None:
        job.stop_timer.cancel()
    assert Path(response._path) == segment
    assert [call.args[0] for call in sleep_mock.await_args_list] == [0.2, 0.4]


@pytest.mark.asyncio
async def test_first_segment_wait_backs_off_while_ffmpeg_starts(
    hass: HomeAssistant,
) -> None:
    """The first playlist is polled with growing delays until it appears."""
    job = SimpleNamespace(
        part_dir=Path(hass.config.path()) / CACHE_SUBDIR / "part",
        final_dir=Path(hass.config.path()) / CACHE_SUBDIR / "final",
        task=None,
    )

    with (
        patch(
            "custom_components.imou_life.record_playback_hls._async_read_playlist",
            new_callable=AsyncMock,
            side_effect=[None, None, None, None, LIVE_PLAYBACK_PLAYLIST],
        ),
        patch(
            "custom_components.imou_life.record_playback_hls.asyncio.sleep",
            new_callable=AsyncMock,
        ) as sleep_mock,
    ):
        body = await _async_wait_for_first_segments(hass, job)

    assert body == LIVE_PLAYBACK_PLAYLIST
    assert [call.args[0] for call in sleep_mock.await_args_list] == [0.2, 0.4]


@pytest.mark.asyncio
async def test_playback_views_reject_unknown_unsafe_requests(
    hass: HomeAssistant,
) -> None:
    cache_id = "a" * 32
    register_playback_source(
        hass, cache_id, "entry-a", AsyncMock(return_value=_local_hls_descriptor())
    )
    playlist_view = ImouRecordPlaybackHlsView()
    file_view = ImouRecordPlaybackFileView()

    with pytest.raises(web.HTTPNotFound):
        await playlist_view.get(_segment_request(hass), "b" * 32)
    with pytest.raises(web.HTTPBadRequest):
        await playlist_view.get(_segment_request(hass), "../etc")
    with pytest.raises(web.HTTPBadRequest):
        await file_view.get(_segment_request(hass), cache_id, "../index.m3u8")
    with pytest.raises(web.HTTPNotFound):
        await file_view.get(_segment_request(hass), cache_id, "seg_99999.m4s")
    with pytest.raises(web.HTTPNotFound):
        await file_view.get(_segment_request(hass), "c" * 32, "init.mp4")


@pytest.mark.asyncio
async def test_unload_removes_only_the_entry_playback_cache(
    hass: HomeAssistant,
) -> None:
    cache_id = record_playback_cache_id("unload")
    other_id = record_playback_cache_id("other")
    _write_playback_cache(hass, cache_id)
    _write_playback_cache(hass, other_id)
    register_playback_source(
        hass, cache_id, "entry-a", AsyncMock(return_value=_local_hls_descriptor())
    )
    register_playback_source(
        hass, other_id, "entry-b", AsyncMock(return_value=_local_hls_descriptor())
    )

    await async_remove_record_playback_sources(hass, "entry-a")

    assert not _playback_cache_dir(hass, cache_id).exists()
    assert _playback_cache_dir(hass, other_id).is_dir()
    with pytest.raises(web.HTTPNotFound):
        await ImouRecordPlaybackHlsView().get(_segment_request(hass), cache_id)


class _RecordingProcess:
    """Stand-in ffmpeg that publishes its HLS files immediately."""

    def __init__(self, output_dir: Path) -> None:
        self.returncode: int | None = None
        self._released = asyncio.Event()
        # ffmpeg cannot create its output directory, so it must already exist.
        (output_dir / "index.m3u8").write_text(LIVE_PLAYBACK_PLAYLIST, encoding="utf-8")
        (output_dir / "init.mp4").write_bytes(b"init")
        (output_dir / "seg_00000.m4s").write_bytes(b"segment")

    async def communicate(self) -> tuple[bytes, bytes]:
        await self._released.wait()
        return b"", b""

    async def wait(self) -> int:
        await self._released.wait()
        return self.returncode or 0

    def terminate(self) -> None:
        self.kill()

    def kill(self) -> None:
        self.returncode = -9
        self._released.set()


@pytest.mark.asyncio
async def test_playlist_view_starts_remux_and_streams_live_segments(
    hass: HomeAssistant,
) -> None:
    assert await async_setup_component(hass, "http", {})
    cache_id = record_playback_cache_id("live")
    processes: list[_RecordingProcess] = []

    def start(*command: str, **_kwargs: object) -> _RecordingProcess:
        process = _RecordingProcess(Path(command[-1]).parent)
        processes.append(process)
        return process

    register_playback_source(
        hass, cache_id, "entry-a", AsyncMock(return_value=_local_hls_descriptor())
    )
    with patch(
        "custom_components.imou_life.record_playback_hls.asyncio.create_subprocess_exec",
        side_effect=start,
    ):
        response = await ImouRecordPlaybackHlsView().get(
            _segment_request(hass), cache_id
        )

    assert len(processes) == 1
    assert response.text.startswith("#EXTM3U")
    assert "#EXT-X-ENDLIST" not in response.text
    assert (
        f"/api/imou_life/record_play_file/{cache_id}/seg_00000.m4s?authSig="
        in response.text
    )
    job = hass.data[DATA_RECORD_PLAYBACK_HLS]["jobs"][cache_id]
    assert not job.finished

    await async_remove_record_playback_sources(hass, "entry-a")

    assert job.task is not None and job.task.done()
    assert cache_id not in hass.data[DATA_RECORD_PLAYBACK_HLS]["jobs"]
    assert not _playback_cache_dir(hass, cache_id).exists()


@pytest.mark.asyncio
async def test_new_remux_clears_incomplete_final_cache(hass: HomeAssistant) -> None:
    """Stale partial finals must not be served while a remux rewrites the clip."""
    assert await async_setup_component(hass, "http", {})
    cache_id = record_playback_cache_id("stale-final")
    _write_playback_cache(hass, cache_id, playlist=LIVE_PLAYBACK_PLAYLIST)
    processes: list[_RecordingProcess] = []

    def start(*command: str, **_kwargs: object) -> _RecordingProcess:
        process = _RecordingProcess(Path(command[-1]).parent)
        processes.append(process)
        return process

    register_playback_source(
        hass, cache_id, "entry-a", AsyncMock(return_value=_local_hls_descriptor())
    )
    with patch(
        "custom_components.imou_life.record_playback_hls.asyncio.create_subprocess_exec",
        side_effect=start,
    ):
        response = await ImouRecordPlaybackHlsView().get(
            _segment_request(hass), cache_id
        )

    assert len(processes) == 1
    assert not _playback_cache_dir(hass, cache_id).exists()
    job = hass.data[DATA_RECORD_PLAYBACK_HLS]["jobs"][cache_id]
    assert job.part_dir.is_dir()
    assert "#EXT-X-ENDLIST" not in response.text

    await async_remove_record_playback_sources(hass, "entry-a")


@pytest.mark.asyncio
async def test_idle_stop_keeps_remux_after_segments_publish(
    hass: HomeAssistant,
) -> None:
    """Pause must not cancel a remux that already wrote playable segments."""
    cache_id = record_playback_cache_id("idle-keep")
    cache_dir = Path(hass.config.path()) / CACHE_SUBDIR
    part_dir = cache_dir / f"{cache_id}.abcdef01.part"
    part_dir.mkdir(parents=True, exist_ok=True)
    (part_dir / "seg_00000.m4s").write_bytes(b"segment")
    job = HlsPlaybackJob(
        cache_id=cache_id,
        entry_id="entry-a",
        part_dir=part_dir,
        final_dir=cache_dir / cache_id,
        last_access=time.time() - 120,
    )
    cancelled = False

    class _Task:
        def done(self) -> bool:
            return False

        def cancel(self) -> None:
            nonlocal cancelled
            cancelled = True

    job.task = _Task()  # type: ignore[assignment]
    store = hass.data.setdefault(
        DATA_RECORD_PLAYBACK_HLS,
        {
            "closed": set(),
            "locks": {},
            "owners": {},
            "signed": {},
            "sources": {},
            "jobs": {},
        },
    )
    store["jobs"][cache_id] = job

    _async_stop_idle_job(hass, job)

    assert not cancelled
    assert store["jobs"].get(cache_id) is job


@pytest.mark.asyncio
async def test_idle_stop_cancels_remux_with_no_segments(hass: HomeAssistant) -> None:
    cache_id = record_playback_cache_id("idle-cancel")
    cache_dir = Path(hass.config.path()) / CACHE_SUBDIR
    part_dir = cache_dir / f"{cache_id}.abcdef02.part"
    part_dir.mkdir(parents=True, exist_ok=True)
    job = HlsPlaybackJob(
        cache_id=cache_id,
        entry_id="entry-a",
        part_dir=part_dir,
        final_dir=cache_dir / cache_id,
        last_access=time.time() - 120,
    )
    cancelled = False

    class _Task:
        def done(self) -> bool:
            return False

        def cancel(self) -> None:
            nonlocal cancelled
            cancelled = True

    job.task = _Task()  # type: ignore[assignment]
    store = hass.data.setdefault(
        DATA_RECORD_PLAYBACK_HLS,
        {
            "closed": set(),
            "locks": {},
            "owners": {},
            "signed": {},
            "sources": {},
            "jobs": {},
        },
    )
    store["jobs"][cache_id] = job

    _async_stop_idle_job(hass, job)

    assert cancelled
    assert cache_id not in store["jobs"]
