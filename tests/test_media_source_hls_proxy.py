"""Cloud/local recording playback prefers HLS and falls back to MP4."""

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
    MIME_MP4,
    ImouRecordMediaSource,
)
from custom_components.imou_life.record_hls_proxy import (
    DATA_RECORD_HLS_PROXY,
    ImouRecordHlsPlaylistView,
    ImouRecordHlsResourceView,
    ImouRecordHlsSegmentView,
    RecordHlsDescriptor,
    async_create_record_hls_proxy_playlist_url,
    async_remove_record_hls_proxy_sessions,
)
from custom_components.imou_life.record_playback_mp4 import (
    DATA_RECORD_PLAYBACK_MP4,
    ImouRecordPlaybackMp4View,
    _remux_command,
    async_prepare_record_mp4,
    async_remove_record_playback_sources,
    register_playback_source,
)
from custom_components.imou_life.repairs import (
    ISSUE_RECORD_PLAYBACK,
    async_create_record_playback_issue,
)
from homeassistant.components.media_source import Unresolvable
from homeassistant.components.media_source.models import MediaSourceItem
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
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
            entry_id=entry_id,
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

    assert (
        response.text
        == f"""#EXTM3U
#EXT-X-VERSION:7
#EXT-X-TARGETDURATION:6
#EXT-X-MEDIA-SEQUENCE:42
#EXT-X-PLAYLIST-TYPE:VOD
#EXT-X-KEY:METHOD=AES-128,URI="/api/imou_life/record_hls/{session_id}/resource/key/0",IV=0x1
#EXT-X-MAP:URI="/api/imou_life/record_hls/{session_id}/resource/map/0",BYTERANGE="100@20"
#EXT-X-PROGRAM-DATE-TIME:2026-11-01T00:00:00Z
#EXTINF:1.25,first title
/api/imou_life/record_hls/{session_id}/segment/0.ts
#EXT-X-DISCONTINUITY
#EXTINF:4.75,second title
/api/imou_life/record_hls/{session_id}/segment/1.ts
#EXT-X-KEY:METHOD=NONE
#EXT-X-ENDLIST
"""
    )


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
            "mp4-a",
            entry_a.entry_id,
            AsyncMock(return_value=RecordHlsDescriptor("source-a")),
        )
        register_playback_source(
            hass,
            "mp4-b",
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
    assert "mp4-a" not in hass.data[DATA_RECORD_PLAYBACK_MP4]["owners"]
    assert hass.data[DATA_RECORD_PLAYBACK_MP4]["owners"]["mp4-b"] == "entry-b"


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
    provider_started = asyncio.Event()

    async def provider_a() -> RecordHlsDescriptor:
        provider_started.set()
        await asyncio.Event().wait()
        return RecordHlsDescriptor("source-a")

    register_playback_source(hass, "mp4-a", entry_a.entry_id, provider_a)
    register_playback_source(
        hass,
        "mp4-b",
        entry_b.entry_id,
        AsyncMock(return_value=RecordHlsDescriptor("source-b")),
    )
    task = asyncio.create_task(
        async_prepare_record_mp4(hass, "mp4-a", entry_a.entry_id, provider_a)
    )
    await provider_started.wait()

    with patch(
        "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
        AsyncMock(return_value=False),
    ):
        assert await async_unload_entry(hass, entry_a) is False

    store = hass.data[DATA_RECORD_PLAYBACK_MP4]
    assert store["owners"] == {"mp4-a": "entry-a", "mp4-b": "entry-b"}
    assert {"mp4-a", "mp4-b"} <= store["sources"].keys()
    assert "mp4-a" in store["locks"]
    assert task in store["tasks"]["mp4-a"]
    assert hls_session in hass.data[DATA_RECORD_HLS_PROXY]
    assert not task.cancelled()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


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
    register_playback_source(hass, "mp4-a", entry.entry_id, provider)

    with (
        patch(
            "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
            AsyncMock(side_effect=RuntimeError("unload failed")),
        ),
        pytest.raises(RuntimeError, match="unload failed"),
    ):
        await async_unload_entry(hass, entry)

    store = hass.data[DATA_RECORD_PLAYBACK_MP4]
    assert store["owners"]["mp4-a"] == entry.entry_id
    assert "mp4-a" in store["sources"]
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

    register_mock.assert_called_once()
    assert descriptor.playlist_url == (
        "http://ha.local:8123/api/imou_life/record_hls/sid/playlist.m3u8?authSig=y"
    )
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
async def test_successful_hls_resolve_clears_playback_issue(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, entry_id="entry-a")
    entry.add_to_hass(hass)
    object.__setattr__(entry, "state", ConfigEntryState.LOADED)
    hass.config.components.add("stream")
    async_create_record_playback_issue(
        hass,
        entry.entry_id,
        RequestFailedException("No permission", api_code=ERROR_CODE_NO_PERMISSION),
    )
    stream_mock = MagicMock()
    stream_mock.endpoint_url.return_value = "/api/hls/imou_rec/playlist.m3u8"
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
            return_value=stream_mock,
        ),
    ):
        await ImouRecordMediaSource(hass).async_resolve_media(item)

    assert (
        DOMAIN,
        f"{ISSUE_RECORD_PLAYBACK}_{entry.entry_id}",
    ) not in ir.async_get(hass).issues


@pytest.mark.asyncio
async def test_mp4_expired_source_refreshes_once_and_succeeds(
    hass: HomeAssistant,
) -> None:
    cache_id = f"expired-success-{time.time_ns()}"
    async_create_record_playback_issue(
        hass,
        "entry-a",
        RequestFailedException("No permission", api_code=ERROR_CODE_NO_PERMISSION),
    )
    provider = AsyncMock(
        side_effect=[
            _local_hls_descriptor("expired", time.time() - 1),
            _local_hls_descriptor("fresh", time.time() + 60),
        ]
    )

    def remux(source_url: str, output: Path) -> None:
        if "expired" in source_url:
            raise OSError("ffmpeg failed")
        output.write_bytes(b"mp4")

    with patch(
        "custom_components.imou_life.record_playback_mp4._async_remux_source_to_mp4",
        new_callable=AsyncMock,
        side_effect=remux,
    ) as remux_mock:
        path = await async_prepare_record_mp4(hass, cache_id, "entry-a", provider)

    assert path.read_bytes() == b"mp4"
    assert provider.await_count == 2
    assert [call.args[0] for call in remux_mock.call_args_list] == [
        f"{LOCAL_HLS_PROXY}&marker=expired",
        f"{LOCAL_HLS_PROXY}&marker=fresh",
    ]
    assert (
        DOMAIN,
        f"{ISSUE_RECORD_PLAYBACK}_entry-a",
    ) not in ir.async_get(hass).issues


def test_mp4_ffmpeg_command_accepts_only_local_hls_proxy(tmp_path: Path) -> None:
    command = _remux_command(LOCAL_HLS_PROXY, tmp_path / "output.mp4")

    assert command[command.index("-i") + 1].startswith(
        "http://ha.local:8123/api/imou_life/record_hls/"
    )
    assert command[command.index("-protocol_whitelist") + 1] == (
        "http,https,tcp,tls,crypto"
    )
    assert "-allowed_extensions" not in command


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source_url",
    [
        (
            "https://evil.example/api/imou_life/record_hls/"
            "0123456789abcdef0123456789abcdef/playlist.m3u8?authSig=x"
        ),
        (
            "http://ha.local:8123/api/imou_life/record_hls/"
            "0123456789abcdef0123456789abcdef/playlist.m3u8"
        ),
        "http://ha.local:8123/api/imou_life/record_hls/not-a-session/playlist.m3u8?authSig=x",
    ],
)
async def test_mp4_rejects_nonlocal_or_unsigned_proxy_source(
    hass: HomeAssistant, source_url: str
) -> None:
    cache_id = f"unsafe-source-{time.time_ns()}"
    provider = AsyncMock(
        return_value=RecordHlsDescriptor(
            source_url,
            proxy_origin="http://ha.local:8123",
        )
    )
    with (
        patch(
            "custom_components.imou_life.record_playback_mp4._async_remux_source_to_mp4",
            new_callable=AsyncMock,
        ) as remux,
        pytest.raises(ValueError, match="local HLS proxy"),
    ):
        await async_prepare_record_mp4(hass, cache_id, "entry-a", provider)

    remux.assert_not_awaited()


@pytest.mark.asyncio
async def test_mp4_second_expired_failure_is_final(
    hass: HomeAssistant,
) -> None:
    cache_id = f"expired-twice-{time.time_ns()}"
    provider = AsyncMock(
        side_effect=[
            _local_hls_descriptor("first", time.time() - 1),
            _local_hls_descriptor("second", time.time() - 1),
        ]
    )
    with (
        patch(
            "custom_components.imou_life.record_playback_mp4._async_remux_source_to_mp4",
            new_callable=AsyncMock,
            side_effect=OSError("ffmpeg failed"),
        ) as remux_mock,
        pytest.raises(OSError),
    ):
        await async_prepare_record_mp4(hass, cache_id, "entry-a", provider)

    assert provider.await_count == 2
    assert remux_mock.call_count == 2
    assert (
        DOMAIN,
        f"{ISSUE_RECORD_PLAYBACK}_entry-a",
    ) not in ir.async_get(hass).issues


@pytest.mark.asyncio
@pytest.mark.parametrize("expires_at", [None, time.time() + 3600])
async def test_mp4_unproven_expiry_does_not_refresh_or_create_issue(
    hass: HomeAssistant,
    expires_at: float | None,
) -> None:
    cache_id = f"network-failure-{time.time_ns()}"
    provider = AsyncMock(return_value=_local_hls_descriptor(expires_at=expires_at))
    with (
        patch(
            "custom_components.imou_life.record_playback_mp4._async_remux_source_to_mp4",
            new_callable=AsyncMock,
            side_effect=OSError("connection reset by peer"),
        ),
        pytest.raises(OSError),
    ):
        await async_prepare_record_mp4(hass, cache_id, "entry-a", provider)

    provider.assert_awaited_once_with()
    assert (
        DOMAIN,
        f"{ISSUE_RECORD_PLAYBACK}_entry-a",
    ) not in ir.async_get(hass).issues


@pytest.mark.asyncio
async def test_mp4_refresh_failure_is_final_and_cleans_partial(
    hass: HomeAssistant,
) -> None:
    cache_id = f"refresh-failure-{time.time_ns()}"
    provider = AsyncMock(
        side_effect=[
            _local_hls_descriptor(expires_at=time.time() - 1),
            RequestFailedException("No permission", api_code=ERROR_CODE_NO_PERMISSION),
        ]
    )
    partial = hass.config.path(f"www/imou/playback/{cache_id}.part.mp4")

    def expired_remux(_source_url: str, output: Path) -> None:
        output.write_bytes(b"partial")
        raise OSError("ffmpeg failed")

    with (
        patch(
            "custom_components.imou_life.record_playback_mp4._async_remux_source_to_mp4",
            new_callable=AsyncMock,
            side_effect=expired_remux,
        ),
        pytest.raises(RequestFailedException),
    ):
        await async_prepare_record_mp4(hass, cache_id, "entry-a", provider)

    assert provider.await_count == 2
    assert not await hass.async_add_executor_job(Path(partial).exists)
    assert (
        DOMAIN,
        f"{ISSUE_RECORD_PLAYBACK}_entry-a",
    ) in ir.async_get(hass).issues


@pytest.mark.asyncio
async def test_mp4_refresh_cancellation_propagates_and_cleans_partial(
    hass: HomeAssistant,
) -> None:
    cache_id = f"refresh-cancel-{time.time_ns()}"
    provider = AsyncMock(
        side_effect=[
            _local_hls_descriptor(expires_at=time.time() - 1),
            asyncio.CancelledError(),
        ]
    )
    partial = hass.config.path(f"www/imou/playback/{cache_id}.part.mp4")

    def expired_remux(_source_url: str, output: Path) -> None:
        output.write_bytes(b"partial")
        raise OSError("ffmpeg failed")

    with (
        patch(
            "custom_components.imou_life.record_playback_mp4._async_remux_source_to_mp4",
            new_callable=AsyncMock,
            side_effect=expired_remux,
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await async_prepare_record_mp4(hass, cache_id, "entry-a", provider)

    assert provider.await_count == 2
    assert not await hass.async_add_executor_job(Path(partial).exists)


@pytest.mark.asyncio
async def test_mp4_signed_url_expiry_allows_one_refresh(
    hass: HomeAssistant,
) -> None:
    cache_id = f"signed-expiry-{time.time_ns()}"
    provider = AsyncMock(
        side_effect=[
            RecordHlsDescriptor(
                f"{LOCAL_HLS_PROXY}&expire={time.time() - 1}",
                proxy_origin="http://ha.local:8123",
            ),
            _local_hls_descriptor("fresh"),
        ]
    )

    def remux(source_url: str, output: Path) -> None:
        if "expire=" in source_url:
            raise OSError("opaque ffmpeg failure")
        output.write_bytes(b"mp4")

    with patch(
        "custom_components.imou_life.record_playback_mp4._async_remux_source_to_mp4",
        new_callable=AsyncMock,
        side_effect=remux,
    ):
        await async_prepare_record_mp4(hass, cache_id, "entry-a", provider)

    assert provider.await_count == 2


@pytest.mark.asyncio
async def test_unload_removes_only_entry_mp4_state_and_cancels_inflight(
    hass: HomeAssistant,
) -> None:
    cache_a = f"entry-a-{time.time_ns()}"
    cache_b = f"entry-b-{time.time_ns()}"
    provider_started = asyncio.Event()
    provider_cancelled = asyncio.Event()
    provider_a_calls = 0

    async def provider_a() -> RecordHlsDescriptor:
        nonlocal provider_a_calls
        provider_a_calls += 1
        provider_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            provider_cancelled.set()
        return RecordHlsDescriptor("closed-runtime")

    provider_b = AsyncMock(return_value=RecordHlsDescriptor("entry-b"))
    register_playback_source(hass, cache_a, "entry-a", provider_a)
    register_playback_source(hass, cache_b, "entry-b", provider_b)
    task = asyncio.create_task(
        async_prepare_record_mp4(hass, cache_a, "entry-a", provider_a)
    )
    await provider_started.wait()

    await async_remove_record_playback_sources(hass, "entry-a")

    assert provider_cancelled.is_set()
    with pytest.raises(asyncio.CancelledError):
        await task
    store = hass.data[DATA_RECORD_PLAYBACK_MP4]
    assert cache_a not in store["owners"]
    assert cache_a not in store["sources"]
    assert cache_a not in store["locks"]
    assert cache_a not in store["tasks"]
    assert store["owners"][cache_b] == "entry-b"
    assert cache_b in store["sources"]

    with pytest.raises(web.HTTPNotFound):
        await ImouRecordPlaybackMp4View().get(_segment_request(hass), cache_a)
    with pytest.raises(LookupError):
        await async_prepare_record_mp4(hass, cache_a, "entry-a", provider_a)
    assert provider_a_calls == 1
    provider_b.assert_not_awaited()


@pytest.mark.asyncio
async def test_mp4_cancel_during_communicate_terminates_process(
    hass: HomeAssistant,
) -> None:
    cache_id = f"cancel-process-{time.time_ns()}"
    communicate_started = asyncio.Event()
    partial = Path(hass.config.path(f"www/imou/playback/{cache_id}.part.mp4"))
    process = MagicMock(returncode=None)

    async def communicate():
        await hass.async_add_executor_job(partial.write_bytes, b"partial")
        communicate_started.set()
        await asyncio.Event().wait()

    process.communicate = AsyncMock(side_effect=communicate)
    process.wait = AsyncMock(return_value=-15)
    process.terminate = MagicMock()
    process.kill = MagicMock()
    provider = AsyncMock(return_value=_local_hls_descriptor())

    with patch(
        "custom_components.imou_life.record_playback_mp4.asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
        return_value=process,
    ):
        task = asyncio.create_task(
            async_prepare_record_mp4(hass, cache_id, "entry-a", provider)
        )
        await communicate_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    process.terminate.assert_called_once_with()
    process.wait.assert_awaited_once_with()
    process.kill.assert_not_called()
    assert not await hass.async_add_executor_job(partial.exists)
    assert (
        DOMAIN,
        f"{ISSUE_RECORD_PLAYBACK}_entry-a",
    ) not in ir.async_get(hass).issues


@pytest.mark.asyncio
async def test_mp4_timeout_terminates_then_kills_process(
    hass: HomeAssistant,
) -> None:
    cache_id = f"timeout-process-{time.time_ns()}"
    partial = Path(hass.config.path(f"www/imou/playback/{cache_id}.part.mp4"))
    process = MagicMock(returncode=None)

    async def communicate():
        await hass.async_add_executor_job(partial.write_bytes, b"partial")
        await asyncio.Event().wait()

    process.communicate = AsyncMock(side_effect=communicate)
    process.wait = AsyncMock(side_effect=[TimeoutError, -9])
    process.terminate = MagicMock()
    process.kill = MagicMock()
    provider = AsyncMock(return_value=_local_hls_descriptor())

    with (
        patch(
            "custom_components.imou_life.record_playback_mp4.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=process,
        ),
        patch(
            "custom_components.imou_life.record_playback_mp4._REMUX_TIMEOUT",
            0,
        ),
        pytest.raises(TimeoutError),
    ):
        await async_prepare_record_mp4(hass, cache_id, "entry-a", provider)

    process.terminate.assert_called_once_with()
    process.kill.assert_called_once_with()
    assert process.wait.await_count == 2
    assert not await hass.async_add_executor_job(partial.exists)


@pytest.mark.asyncio
async def test_mp4_terminate_refusal_falls_back_to_kill(
    hass: HomeAssistant,
) -> None:
    cache_id = f"terminate-refused-{time.time_ns()}"
    process = MagicMock(returncode=None)
    process.communicate = AsyncMock(side_effect=asyncio.CancelledError)
    process.wait = AsyncMock(return_value=-9)
    process.terminate = MagicMock(side_effect=PermissionError)
    process.kill = MagicMock()
    provider = AsyncMock(return_value=_local_hls_descriptor())

    with (
        patch(
            "custom_components.imou_life.record_playback_mp4.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=process,
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await async_prepare_record_mp4(hass, cache_id, "entry-a", provider)

    process.terminate.assert_called_once_with()
    process.kill.assert_called_once_with()
    process.wait.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_mp4_process_success_atomically_publishes_output(
    hass: HomeAssistant,
) -> None:
    cache_id = f"process-success-{time.time_ns()}"
    partial = Path(hass.config.path(f"www/imou/playback/{cache_id}.part.mp4"))
    process = MagicMock(returncode=0)

    async def communicate():
        await hass.async_add_executor_job(partial.write_bytes, b"mp4")
        return b"", b""

    process.communicate = AsyncMock(side_effect=communicate)
    provider = AsyncMock(return_value=_local_hls_descriptor())

    with patch(
        "custom_components.imou_life.record_playback_mp4.asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
        return_value=process,
    ):
        output = await async_prepare_record_mp4(hass, cache_id, "entry-a", provider)

    assert output.read_bytes() == b"mp4"
    assert not await hass.async_add_executor_job(partial.exists)


@pytest.mark.asyncio
async def test_mp4_process_failure_captures_stderr_without_refresh(
    hass: HomeAssistant,
) -> None:
    cache_id = f"process-failure-{time.time_ns()}"
    process = MagicMock(returncode=1)
    process.communicate = AsyncMock(return_value=(b"", b"ffmpeg diagnostic"))
    provider = AsyncMock(return_value=_local_hls_descriptor())

    with (
        patch(
            "custom_components.imou_life.record_playback_mp4.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=process,
        ),
        pytest.raises(OSError, match="ffmpeg diagnostic"),
    ):
        await async_prepare_record_mp4(hass, cache_id, "entry-a", provider)

    provider.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_cancelling_same_cache_waiter_does_not_touch_owner_partial(
    hass: HomeAssistant,
) -> None:
    cache_id = f"waiter-cancel-{time.time_ns()}"
    partial = Path(hass.config.path(f"www/imou/playback/{cache_id}.part.mp4"))
    remux_started = asyncio.Event()
    allow_remux = asyncio.Event()
    provider = AsyncMock(return_value=_local_hls_descriptor())

    async def remux(_source_url: str, output: Path) -> None:
        await hass.async_add_executor_job(output.write_bytes, b"partial")
        remux_started.set()
        await allow_remux.wait()
        await hass.async_add_executor_job(output.write_bytes, b"complete")

    with patch(
        "custom_components.imou_life.record_playback_mp4._async_remux_source_to_mp4",
        side_effect=remux,
    ):
        owner = asyncio.create_task(
            async_prepare_record_mp4(hass, cache_id, "entry-a", provider)
        )
        await remux_started.wait()
        waiter = asyncio.create_task(
            async_prepare_record_mp4(hass, cache_id, "entry-a", provider)
        )
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

        assert await hass.async_add_executor_job(partial.read_bytes) == b"partial"
        allow_remux.set()
        output = await owner
        cached = await async_prepare_record_mp4(hass, cache_id, "entry-a", provider)

    assert output == cached
    assert await hass.async_add_executor_job(output.read_bytes) == b"complete"
    provider.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_cancelling_same_cache_owner_allows_waiter_to_take_over(
    hass: HomeAssistant,
) -> None:
    cache_id = f"owner-cancel-{time.time_ns()}"
    first_remux_started = asyncio.Event()
    first_remux_cancelled = asyncio.Event()
    provider = AsyncMock(return_value=_local_hls_descriptor())
    remux_calls = 0

    async def remux(_source_url: str, output: Path) -> None:
        nonlocal remux_calls
        remux_calls += 1
        if remux_calls == 1:
            await hass.async_add_executor_job(output.write_bytes, b"partial")
            first_remux_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                first_remux_cancelled.set()
            return
        assert not await hass.async_add_executor_job(output.exists)
        await hass.async_add_executor_job(output.write_bytes, b"complete")

    with patch(
        "custom_components.imou_life.record_playback_mp4._async_remux_source_to_mp4",
        side_effect=remux,
    ):
        owner = asyncio.create_task(
            async_prepare_record_mp4(hass, cache_id, "entry-a", provider)
        )
        await first_remux_started.wait()
        waiter = asyncio.create_task(
            async_prepare_record_mp4(hass, cache_id, "entry-a", provider)
        )
        owner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await owner
        output = await asyncio.wait_for(waiter, timeout=1)

    assert first_remux_cancelled.is_set()
    assert await hass.async_add_executor_job(output.read_bytes) == b"complete"
    assert remux_calls == 2
    assert provider.await_count == 2
