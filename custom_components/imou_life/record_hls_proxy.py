"""Proxy Imou HLS playlists so Home Assistant Stream can demux segments."""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant
from pyimouapi.playback.hls import parse_hls_media_segment_urls

from .record_http_fetch import async_fetch_url_bytes, async_fetch_url_text

_LOGGER = logging.getLogger(__name__)

DATA_RECORD_HLS_PROXY = "imou_life_record_hls_proxy"
SESSION_TTL_SECONDS = 3600

PLAYLIST_URL = "/api/imou_life/record_hls/{session_id}/playlist.m3u8"
SEGMENT_URL = "/api/imou_life/record_hls/{session_id}/segment/{index}.ts"


@dataclass(slots=True)
class RecordHlsSession:
    """Upstream segment URLs for one proxied recording playback."""

    segment_urls: list[str]
    playlist_body: str
    created: float


def async_register_record_hls_proxy(hass: HomeAssistant) -> None:
    """Register HLS proxy views once."""
    if hass.data.get(DATA_RECORD_HLS_PROXY):
        return
    hass.http.register_view(ImouRecordHlsPlaylistView())
    hass.http.register_view(ImouRecordHlsSegmentView())
    hass.data[DATA_RECORD_HLS_PROXY] = {}


def _sessions(hass: HomeAssistant) -> dict[str, RecordHlsSession]:
    store = hass.data.get(DATA_RECORD_HLS_PROXY)
    if not isinstance(store, dict):
        store = {}
        hass.data[DATA_RECORD_HLS_PROXY] = store
    return store


def _prune_sessions(hass: HomeAssistant) -> None:
    now = time.time()
    sessions = _sessions(hass)
    expired = [
        key
        for key, session in sessions.items()
        if now - session.created > SESSION_TTL_SECONDS
    ]
    for key in expired:
        sessions.pop(key, None)


def _playlist_target_duration(playlist_body: str) -> int:
    for line in playlist_body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#EXT-X-TARGETDURATION:"):
            try:
                return max(1, int(stripped.split(":", 1)[1]))
            except ValueError:
                break
    return 2


def _build_proxy_playlist(
    hass: HomeAssistant, session_id: str, session: RecordHlsSession
) -> str:
    target = _playlist_target_duration(session.playlist_body)
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", f"#EXT-X-TARGETDURATION:{target}"]
    if "#EXT-X-ENDLIST" in session.playlist_body:
        lines.append("#EXT-X-PLAYLIST-TYPE:VOD")
    for index in range(len(session.segment_urls)):
        segment_path = SEGMENT_URL.format(session_id=session_id, index=index)
        signed = async_sign_path(
            hass,
            segment_path,
            timedelta(seconds=SESSION_TTL_SECONDS),
            use_content_user=True,
        )
        lines.append(f"#EXTINF:{target},")
        lines.append(signed)
    if "#EXT-X-ENDLIST" in session.playlist_body:
        lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


async def async_create_record_hls_proxy_playlist_url(
    hass: HomeAssistant, upstream_playlist_url: str
) -> str:
    """Fetch Imou m3u8 and return a signed HA playlist URL Stream can open."""
    _prune_sessions(hass)
    body = await async_fetch_url_text(hass, upstream_playlist_url)
    segment_urls = parse_hls_media_segment_urls(body)
    if not segment_urls:
        msg = "Imou HLS playlist had no segments"
        raise ValueError(msg)
    session_id = secrets.token_hex(16)
    _sessions(hass)[session_id] = RecordHlsSession(
        segment_urls=segment_urls,
        playlist_body=body,
        created=time.time(),
    )
    playlist_path = PLAYLIST_URL.format(session_id=session_id)
    signed_playlist = async_sign_path(
        hass,
        playlist_path,
        timedelta(seconds=SESSION_TTL_SECONDS),
        use_content_user=True,
    )
    _LOGGER.debug(
        "Imou record HLS proxy %s with %s segments", session_id, len(segment_urls)
    )
    return signed_playlist


class ImouRecordHlsPlaylistView(HomeAssistantView):
    """Serve a playlist whose segment lines end in ``.ts`` on this host."""

    requires_auth = True
    url = PLAYLIST_URL
    name = "api:imou_life_record_hls_playlist"
    cors_allowed = True

    async def get(self, request: web.Request, session_id: str) -> web.Response:
        """Return rewritten m3u8."""
        hass = request.app["hass"]
        record = _sessions(hass).get(session_id)
        if record is None:
            raise web.HTTPNotFound()
        playlist = _build_proxy_playlist(hass, session_id, record)
        if not playlist:
            raise web.HTTPNotFound()
        return web.Response(
            text=playlist,
            content_type="application/vnd.apple.mpegurl",
        )


class ImouRecordHlsSegmentView(HomeAssistantView):
    """Proxy one Imou TS segment for the HLS demuxer."""

    requires_auth = True
    url = SEGMENT_URL
    name = "api:imou_life_record_hls_segment"
    cors_allowed = True

    async def get(
        self, request: web.Request, session_id: str, index: str
    ) -> web.StreamResponse:
        """Stream bytes from Lechange."""
        hass = request.app["hass"]
        record = _sessions(hass).get(session_id)
        if record is None:
            raise web.HTTPNotFound()
        try:
            seg_index = int(index)
        except ValueError as err:
            raise web.HTTPBadRequest() from err
        if seg_index < 0 or seg_index >= len(record.segment_urls):
            raise web.HTTPNotFound()
        upstream = record.segment_urls[seg_index]
        payload = await async_fetch_url_bytes(hass, upstream)
        return web.Response(body=payload, content_type="video/mp2t")
