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
    ImouRecordHlsSegmentView,
    async_create_record_hls_proxy_playlist_url,
    async_remove_record_hls_proxy_sessions,
)
from custom_components.imou_life.record_playback_mp4 import (
    async_prepare_record_mp4,
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
HLS_PLAYLIST = """#EXTM3U
#EXT-X-MEDIA-SEQUENCE:42
#EXTINF:2,
https://media.example/segment.ts?token=old
#EXT-X-ENDLIST
"""
HLS_REFRESHED_PLAYLIST = HLS_PLAYLIST.replace("token=old", "token=fresh")


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

    assert "#EXT-X-VERSION:4" in playlist_response.text
    assert "#EXT-X-BYTERANGE:100@50" in playlist_response.text
    assert "#EXT-X-BYTERANGE:25@150" in playlist_response.text
    assert segment_response.body == b"second range"
    fetch.assert_awaited_once_with(
        hass,
        "https://media.example/segment.ts",
        range_header="bytes=150-174",
    )


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


@pytest.mark.asyncio
async def test_stable_resolve_failure_creates_playback_issue(
    hass: HomeAssistant,
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT, entry_id="entry-a")
    entry.add_to_hass(hass)
    object.__setattr__(entry, "state", ConfigEntryState.LOADED)
    runtime = _runtime_with_hls_url()
    runtime.coordinator.device_manager.delegate.async_create_device_record_hls = (
        AsyncMock(side_effect=RequestFailedException("OP1009: No permission"))
    )
    item = MediaSourceItem(hass, DOMAIN, IDENTIFIER.format(entry=entry.entry_id), None)

    with (
        patch(
            "custom_components.imou_life.media_source.get_runtime_data",
            return_value=runtime,
        ),
        pytest.raises(Unresolvable),
    ):
        await ImouRecordMediaSource(hass).async_resolve_media(item)

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
        pytest.raises(Unresolvable),
    ):
        await ImouRecordMediaSource(hass).async_resolve_media(item)

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
        hass, entry.entry_id, RequestFailedException("OP1009: No permission")
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
        hass, "entry-a", RequestFailedException("OP1009: No permission")
    )
    provider = AsyncMock(
        side_effect=[
            "https://media.example/expired.m3u8",
            "https://media.example/fresh.m3u8",
        ]
    )

    def remux(source_url, output):
        if "expired" in source_url:
            raise OSError("Server returned 403 Forbidden")
        output.write_bytes(b"mp4")

    with patch(
        "custom_components.imou_life.record_playback_mp4._remux_source_to_mp4",
        side_effect=remux,
    ) as remux_mock:
        path = await async_prepare_record_mp4(hass, cache_id, "entry-a", provider)

    assert path.read_bytes() == b"mp4"
    assert provider.await_count == 2
    assert [call.args[0] for call in remux_mock.call_args_list] == [
        "https://media.example/expired.m3u8",
        "https://media.example/fresh.m3u8",
    ]
    assert (
        DOMAIN,
        f"{ISSUE_RECORD_PLAYBACK}_entry-a",
    ) not in ir.async_get(hass).issues


@pytest.mark.asyncio
async def test_mp4_second_expired_failure_is_final(
    hass: HomeAssistant,
) -> None:
    cache_id = f"expired-twice-{time.time_ns()}"
    provider = AsyncMock(side_effect=["first", "second"])
    with (
        patch(
            "custom_components.imou_life.record_playback_mp4._remux_source_to_mp4",
            side_effect=OSError("HTTP error 403 Forbidden"),
        ) as remux_mock,
        pytest.raises(OSError),
    ):
        await async_prepare_record_mp4(hass, cache_id, "entry-a", provider)

    assert provider.await_count == 2
    assert remux_mock.call_count == 2
    assert (
        DOMAIN,
        f"{ISSUE_RECORD_PLAYBACK}_entry-a",
    ) in ir.async_get(hass).issues


@pytest.mark.asyncio
async def test_mp4_nonexpired_failure_does_not_refresh_or_create_issue(
    hass: HomeAssistant,
) -> None:
    cache_id = f"network-failure-{time.time_ns()}"
    provider = AsyncMock(return_value="source")
    with (
        patch(
            "custom_components.imou_life.record_playback_mp4._remux_source_to_mp4",
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
        side_effect=["source", RequestFailedException("Authentication failed")]
    )
    partial = hass.config.path(f"www/imou/playback/{cache_id}.part.mp4")

    def expired_remux(_source_url, output):
        output.write_bytes(b"partial")
        raise OSError("input URL expired")

    with (
        patch(
            "custom_components.imou_life.record_playback_mp4._remux_source_to_mp4",
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
    provider = AsyncMock(side_effect=["source", asyncio.CancelledError()])
    partial = hass.config.path(f"www/imou/playback/{cache_id}.part.mp4")

    def expired_remux(_source_url, output):
        output.write_bytes(b"partial")
        raise OSError("input URL expired")

    with (
        patch(
            "custom_components.imou_life.record_playback_mp4._remux_source_to_mp4",
            side_effect=expired_remux,
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await async_prepare_record_mp4(hass, cache_id, "entry-a", provider)

    assert provider.await_count == 2
    assert not await hass.async_add_executor_job(Path(partial).exists)
