"""Proxy Imou HLS playlists so Home Assistant Stream can demux segments."""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from pyimouapi.playback.hls import (
    parse_hls_media_segment_urls,
    pick_hls_variant_playlist_url,
)

from .record_http_fetch import async_fetch_url_response, async_fetch_url_text

_LOGGER = logging.getLogger(__name__)

DATA_RECORD_HLS_PROXY = "imou_life_record_hls_proxy"
DATA_RECORD_HLS_PROXY_REGISTERED = "imou_life_record_hls_proxy_registered"
DATA_RECORD_HLS_PROXY_TIMER = "imou_life_record_hls_proxy_timer"
DATA_RECORD_HLS_PROXY_TIMER_TOKEN = "imou_life_record_hls_proxy_timer_token"
SESSION_TTL_SECONDS = 600

PLAYLIST_URL = "/api/imou_life/record_hls/{session_id}/playlist.m3u8"
SEGMENT_URL = "/api/imou_life/record_hls/{session_id}/segment/{index}.ts"


@dataclass(slots=True)
class RecordHlsDescriptor:
    """One upstream playlist URL and its optional absolute expiry."""

    playlist_url: str
    expires_at: float | None = None


@dataclass(slots=True)
class RecordHlsSession:
    """Upstream segment URLs for one proxied recording playback."""

    entry_id: str
    segment_urls: list[str]
    segment_identities: list[str | None]
    playlist_body: str
    expires_at: float
    refresh: Callable[[], Awaitable[RecordHlsDescriptor | str]] | None
    refresh_lock: asyncio.Lock
    refresh_task: asyncio.Task[RecordHlsDescriptor | str] | None = None
    refreshed_segment_urls: dict[str, str] | None = None
    refresh_attempted: bool = False
    generation: int = 0
    active: bool = True

    def close(self) -> asyncio.Task[RecordHlsDescriptor | str] | None:
        """Release callbacks and cancel an in-progress refresh."""
        self.active = False
        self.refresh = None
        refresh_task = self.refresh_task
        if refresh_task is not None:
            refresh_task.cancel()
        self.refresh_task = None
        self.segment_urls.clear()
        self.segment_identities.clear()
        if self.refreshed_segment_urls is not None:
            self.refreshed_segment_urls.clear()
        return refresh_task


def async_register_record_hls_proxy(hass: HomeAssistant) -> None:
    """Register HLS proxy views once."""
    if hass.data.get(DATA_RECORD_HLS_PROXY_REGISTERED):
        return
    hass.http.register_view(ImouRecordHlsPlaylistView())
    hass.http.register_view(ImouRecordHlsSegmentView())
    hass.data[DATA_RECORD_HLS_PROXY_REGISTERED] = True
    hass.data.setdefault(DATA_RECORD_HLS_PROXY, {})


def _sessions(hass: HomeAssistant) -> dict[str, RecordHlsSession]:
    store = hass.data.get(DATA_RECORD_HLS_PROXY)
    if not isinstance(store, dict):
        store = {}
        hass.data[DATA_RECORD_HLS_PROXY] = store
    return store


def _remove_session(
    hass: HomeAssistant, session_id: str
) -> asyncio.Task[RecordHlsDescriptor | str] | None:
    if (session := _sessions(hass).pop(session_id, None)) is not None:
        return session.close()
    return None


def _prune_sessions(hass: HomeAssistant, *, reschedule: bool = True) -> None:
    now = time.time()
    sessions = _sessions(hass)
    expired = [key for key, session in sessions.items() if now >= session.expires_at]
    for key in expired:
        _remove_session(hass, key)
    if expired and reschedule:
        _schedule_cleanup(hass)


def _schedule_cleanup(hass: HomeAssistant) -> None:
    if (cancel := hass.data.pop(DATA_RECORD_HLS_PROXY_TIMER, None)) is not None:
        cancel()
    token = object()
    hass.data[DATA_RECORD_HLS_PROXY_TIMER_TOKEN] = token
    sessions = _sessions(hass)
    if not sessions:
        hass.data.pop(DATA_RECORD_HLS_PROXY_TIMER_TOKEN, None)
        return

    @callback
    def async_cleanup_expired_sessions(_now) -> None:
        if hass.data.get(DATA_RECORD_HLS_PROXY_TIMER_TOKEN) is not token:
            return
        hass.data.pop(DATA_RECORD_HLS_PROXY_TIMER, None)
        hass.data.pop(DATA_RECORD_HLS_PROXY_TIMER_TOKEN, None)
        _prune_sessions(hass, reschedule=False)
        _schedule_cleanup(hass)

    hass.data[DATA_RECORD_HLS_PROXY_TIMER] = async_call_later(
        hass,
        max(0, min(session.expires_at for session in sessions.values()) - time.time()),
        async_cleanup_expired_sessions,
    )


async def async_remove_record_hls_proxy_sessions(
    hass: HomeAssistant, entry_id: str
) -> None:
    """Remove all proxy sessions owned by one config entry."""
    if not isinstance(hass.data.get(DATA_RECORD_HLS_PROXY), dict):
        return
    refresh_tasks: list[asyncio.Task[RecordHlsDescriptor | str]] = []
    for session_id, session in list(_sessions(hass).items()):
        if (
            session.entry_id == entry_id
            and (task := _remove_session(hass, session_id)) is not None
        ):
            refresh_tasks.append(task)
    _schedule_cleanup(hass)
    if refresh_tasks:
        await asyncio.gather(*refresh_tasks, return_exceptions=True)


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
    remaining_ttl = max(0, session.expires_at - time.time())
    for index in range(len(session.segment_urls)):
        segment_path = SEGMENT_URL.format(session_id=session_id, index=index)
        signed = async_sign_path(
            hass,
            segment_path,
            timedelta(seconds=remaining_ttl),
            use_content_user=True,
        )
        lines.append(f"#EXTINF:{target},")
        lines.append(signed)
    if "#EXT-X-ENDLIST" in session.playlist_body:
        lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


def _segment_identities(
    playlist_body: str, segment_urls: list[str]
) -> list[str | None]:
    """Build identities only when playlist metadata proves a stable mapping."""
    for line in playlist_body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            continue
        try:
            sequence = int(stripped.split(":", 1)[1])
        except ValueError:
            break
        return [f"sequence:{sequence + index}" for index in range(len(segment_urls))]

    metadata: list[tuple[str | None, str | None]] = []
    byte_range: str | None = None
    program_date_time: str | None = None
    for line in playlist_body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#EXT-X-BYTERANGE:"):
            value = stripped.split(":", 1)[1]
            byte_range = value if "@" in value else None
        elif stripped.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            program_date_time = stripped.split(":", 1)[1]
        elif stripped and not stripped.startswith("#"):
            if not urlsplit(stripped).path.lower().endswith(".m3u8"):
                metadata.append((byte_range, program_date_time))
            byte_range = None
            program_date_time = None
    metadata.extend([(None, None)] * (len(segment_urls) - len(metadata)))

    paths = [urlsplit(url).path for url in segment_urls]
    path_counts = Counter(paths)
    identities: list[str | None] = []
    for index, path in enumerate(paths):
        if path_counts[path] == 1:
            identities.append(f"path:{path}")
            continue
        segment_range, segment_time = metadata[index]
        if segment_range is None and segment_time is None:
            identities.append(None)
            continue
        identities.append(
            f"path:{path}|range:{segment_range or ''}|time:{segment_time or ''}"
        )

    for identity in {identity for identity in identities if identity is not None}:
        if identities.count(identity) > 1:
            identities = [
                None if candidate == identity else candidate for candidate in identities
            ]
    return identities


def _url_expiry(url: str, now: float) -> float | None:
    """Return an expiry encoded in a signed URL when present."""
    query = parse_qs(urlsplit(url).query)
    for key in ("expire", "expires", "expiry", "exp", "expireTime"):
        values = query.get(key)
        if not values:
            continue
        try:
            value = float(values[0])
        except ValueError:
            continue
        if value >= 1_000_000_000:
            return max(now, value)
        return now + max(0, value)
    return None


def _session_expiry(
    playlist_url: str,
    segment_urls: list[str],
    expires_at: float | None,
) -> float:
    now = time.time()
    candidates = [now + SESSION_TTL_SECONDS]
    if expires_at is not None:
        candidates.append(expires_at)
    for url in (playlist_url, *segment_urls):
        if (url_expiry := _url_expiry(url, now)) is not None:
            candidates.append(url_expiry)
    return max(now, min(candidates))


async def _load_media_playlist(
    hass: HomeAssistant, playlist_url: str
) -> tuple[str, list[str]]:
    body = await async_fetch_url_text(hass, playlist_url)
    segment_urls = parse_hls_media_segment_urls(body, playlist_url)
    if segment_urls:
        return body, segment_urls
    variant = pick_hls_variant_playlist_url(body, playlist_url)
    if variant is None:
        return body, segment_urls
    body = await async_fetch_url_text(hass, variant)
    segment_urls = parse_hls_media_segment_urls(body, variant)
    return body, segment_urls


async def async_create_record_hls_proxy_playlist_url(
    hass: HomeAssistant,
    upstream_playlist_url: str,
    *,
    refresh: Callable[[], Awaitable[RecordHlsDescriptor | str]],
    entry_id: str,
    expires_at: float | None = None,
) -> str:
    """Fetch Imou m3u8 and return a signed HA playlist URL Stream can open."""
    _prune_sessions(hass)
    body, segment_urls = await _load_media_playlist(hass, upstream_playlist_url)
    if not segment_urls:
        msg = "Imou HLS playlist had no segments"
        raise ValueError(msg)
    segment_identities = _segment_identities(body, segment_urls)
    session_id = secrets.token_hex(16)
    _sessions(hass)[session_id] = RecordHlsSession(
        entry_id=entry_id,
        segment_urls=segment_urls,
        segment_identities=segment_identities,
        playlist_body=body,
        expires_at=_session_expiry(
            upstream_playlist_url,
            segment_urls,
            expires_at,
        ),
        refresh=refresh,
        refresh_lock=asyncio.Lock(),
    )
    _schedule_cleanup(hass)
    playlist_path = PLAYLIST_URL.format(session_id=session_id)
    signed_playlist = async_sign_path(
        hass,
        playlist_path,
        timedelta(
            seconds=max(
                0,
                _sessions(hass)[session_id].expires_at - time.time(),
            )
        ),
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
        _prune_sessions(hass)
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
        _prune_sessions(hass)
        record = _sessions(hass).get(session_id)
        if record is None:
            raise web.HTTPNotFound()
        try:
            seg_index = int(index)
        except ValueError as err:
            raise web.HTTPBadRequest() from err
        if seg_index < 0 or seg_index >= len(record.segment_urls):
            raise web.HTTPNotFound()
        identity = record.segment_identities[seg_index]
        if record.refreshed_segment_urls is None:
            upstream = record.segment_urls[seg_index]
        elif (
            identity is None
            or (upstream := record.refreshed_segment_urls.get(identity)) is None
        ):
            raise web.HTTPBadGateway()
        generation = record.generation
        try:
            upstream_response = await async_fetch_url_response(
                hass,
                upstream,
                range_header=request.headers.get("Range"),
            )
        except (OSError, TimeoutError, ValueError) as err:
            _LOGGER.warning("Imou HLS segment %s fetch failed: %s", seg_index, err)
            raise web.HTTPBadGateway() from err
        if not record.active or _sessions(hass).get(session_id) is not record:
            raise web.HTTPNotFound()
        if upstream_response.status in (401, 403, 404):
            async with record.refresh_lock:
                if not record.active or _sessions(hass).get(session_id) is not record:
                    raise web.HTTPNotFound()
                if generation == record.generation and not record.refresh_attempted:
                    record.refresh_attempted = True
                    try:
                        if record.refresh is None:
                            raise web.HTTPNotFound()
                        record.refresh_task = hass.async_create_task(record.refresh())
                        try:
                            descriptor = await record.refresh_task
                        except asyncio.CancelledError:
                            if not record.active:
                                raise web.HTTPNotFound() from None
                            raise
                        finally:
                            record.refresh_task = None
                        if isinstance(descriptor, str):
                            descriptor = RecordHlsDescriptor(descriptor)
                        body, segment_urls = await _load_media_playlist(
                            hass, descriptor.playlist_url
                        )
                        if (
                            not record.active
                            or _sessions(hass).get(session_id) is not record
                        ):
                            raise web.HTTPNotFound()
                        if not segment_urls:
                            msg = "Refreshed Imou HLS playlist had no segments"
                            raise ValueError(msg)
                        refreshed_identities = _segment_identities(body, segment_urls)
                        record.refreshed_segment_urls = {
                            identity: url
                            for identity, url in zip(
                                refreshed_identities, segment_urls, strict=True
                            )
                            if identity is not None
                        }
                        record.playlist_body = body
                        record.expires_at = _session_expiry(
                            descriptor.playlist_url,
                            segment_urls,
                            descriptor.expires_at,
                        )
                        record.generation += 1
                        _schedule_cleanup(hass)
                    except web.HTTPException:
                        raise
                    except Exception:
                        _LOGGER.warning(
                            "Imou HLS segment %s refresh failed",
                            seg_index,
                            exc_info=True,
                        )
                refreshed = (
                    record.generation > generation
                    and identity is not None
                    and record.refreshed_segment_urls is not None
                    and (upstream := record.refreshed_segment_urls.get(identity))
                    is not None
                )
            if not refreshed:
                raise web.HTTPBadGateway()
            try:
                upstream_response = await async_fetch_url_response(
                    hass,
                    upstream,
                    range_header=request.headers.get("Range"),
                )
            except (OSError, TimeoutError, ValueError) as err:
                _LOGGER.warning(
                    "Refreshed Imou HLS segment %s fetch failed: %s",
                    seg_index,
                    err,
                )
                raise web.HTTPBadGateway() from err
            if not record.active or _sessions(hass).get(session_id) is not record:
                raise web.HTTPNotFound()
        if not 200 <= upstream_response.status < 300:
            raise web.HTTPBadGateway()
        response_headers = {
            name: value
            for name, value in upstream_response.headers.items()
            if name.lower()
            in {
                "accept-ranges",
                "cache-control",
                "content-length",
                "content-range",
                "content-type",
                "etag",
                "last-modified",
            }
        }
        if not any(name.lower() == "content-type" for name in response_headers):
            response_headers["Content-Type"] = "video/mp2t"
        return web.Response(
            body=upstream_response.body,
            status=upstream_response.status,
            headers=response_headers,
        )
