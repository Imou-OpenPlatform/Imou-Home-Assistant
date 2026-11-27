"""Proxy Imou HLS playlists so Home Assistant Stream can demux segments."""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urljoin, urlsplit

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.network import NoURLAvailableError, get_url
from pyimouapi.playback.hls import (
    parse_hls_media_segment_urls,
    pick_hls_variant_playlist_url,
)

from .record_http_fetch import (
    async_fetch_url_response,
    async_fetch_url_text,
    async_pin_public_url,
)

_LOGGER = logging.getLogger(__name__)

DATA_RECORD_HLS_PROXY = "imou_life_record_hls_proxy"
DATA_RECORD_HLS_PROXY_REGISTERED = "imou_life_record_hls_proxy_registered"
DATA_RECORD_HLS_PROXY_TIMER = "imou_life_record_hls_proxy_timer"
DATA_RECORD_HLS_PROXY_TIMER_TOKEN = "imou_life_record_hls_proxy_timer_token"
SESSION_TTL_SECONDS = 600
PLAYLIST_FOLLOW_MIN_SECONDS = 2.0
# Cloud segment tokens expire within seconds; allow a fresh upstream request
# once the cooldown has passed instead of giving up after the first refresh.
REFRESH_COOLDOWN_SECONDS = 5.0
# Failed refreshes back off exponentially and stop after a few attempts so an
# expired clip cannot keep spending API quota on every segment request.
_REFRESH_MAX_BACKOFF_SECONDS = 60.0
_REFRESH_MAX_FAILURES = 6
_SIGNED_PATH_REFRESH_MARGIN_SECONDS = 60.0
_LOCAL_PLAYBACK_PREPARE_MAX_SECONDS = 120.0
_LOCAL_PLAYBACK_PREPARE_POLL_SECONDS = 0.75
# Live clips are buffered locally so a player that opens late can still fetch
# the opening segments after the upstream window has slid past them.
_LIVE_PREFETCH_MAX_SEGMENTS = 30
_LIVE_PREFETCH_MAX_BYTES = 32 * 1024 * 1024
# The upstream window of three segments slides every 4-6 s while players only
# reload after draining it, so segments listed in between would never be seen.
_LIVE_FOLLOW_INTERVAL_SECONDS = 1.0
_LIVE_FOLLOW_MAX_FAILURES = 12
# Every segment of a local clip shares one signed URL shape and differs only
# by this number, so a window that slid past a poll can still be completed.
_SEGMENT_NUMBER_RE = re.compile(r"(?<![A-Za-z])seg_(\d+)\.ts")
_WINDOW_GAP_MAX_SEGMENTS = 60
# Upstream lists the segments past a clip's end before it appends ENDLIST.
_UNPUBLISHED_SEGMENT_RETRIES = 2
_UNPUBLISHED_SEGMENT_RETRY_SECONDS = 1.0
# Imou's live transcoder occasionally re-serves an already published span under
# a new segment number. ffmpeg then sees PTS go backwards and ffmpeg's HLS
# muxer stretches the duplicated span instead of dropping it, so the proxy
# filters such segments out by their presentation time.
_TS_PACKET_SIZE = 188
_TS_SYNC_BYTE = 0x47
_TS_NULL_PID = 0x1FFF
_REWIND_TOLERANCE_SECONDS = 0.5

PLAYLIST_URL = "/api/imou_life/record_hls/{session_id}/playlist.m3u8"
SEGMENT_URL = "/api/imou_life/record_hls/{session_id}/segment/{index}.ts"
RESOURCE_URL = "/api/imou_life/record_hls/{session_id}/resource/{kind}/{index}"
_UPSTREAM_REFRESH_STATUSES = frozenset({401, 403, 404, 502})


@dataclass(slots=True)
class RecordHlsDescriptor:
    """One upstream playlist URL and its optional absolute expiry."""

    playlist_url: str
    expires_at: float | None = None
    proxy_origin: str | None = None


@dataclass(frozen=True, slots=True)
class _LoadedMediaPlaylist:
    """A media playlist plus the URL used to resolve its relative URIs."""

    body: str
    segment_urls: list[str]
    base_url: str


_LOCAL_PREPARE_WARM_SEGMENT_COUNT = 3


@dataclass(frozen=True, slots=True)
class PreparedRecordHlsPlayback:
    """A public localvod snapshot once the first upstream segment is readable."""

    loaded: _LoadedMediaPlaylist
    segment_prefetch: dict[int, bytes]


@dataclass(frozen=True, slots=True)
class HlsByteRange:
    """One absolute byte range from an HLS media playlist."""

    start: int
    length: int

    @property
    def end(self) -> int:
        """Return the inclusive final byte."""
        return self.start + self.length - 1

    @property
    def request_header(self) -> str:
        """Return the corresponding HTTP Range header."""
        return f"bytes={self.start}-{self.end}"


@dataclass(frozen=True, slots=True)
class HlsSegment:
    """One upstream segment and its stable refresh identity."""

    url: str
    identity: str | None
    byte_range: HlsByteRange | None
    valid: bool = True
    # Content fingerprint (program date time or path + range) used to detect a
    # playlist that restarts or rewinds after a refresh.
    content: str | None = None


@dataclass(frozen=True, slots=True)
class HlsResource:
    """One encryption key or initialization map."""

    url: str
    identity: str
    byte_range: HlsByteRange | None = None


@dataclass(slots=True)
class RecordHlsSession:
    """Upstream segment URLs for one proxied recording playback."""

    entry_id: str
    segments: list[HlsSegment]
    resources: dict[str, list[HlsResource]]
    playlist_body: str
    expires_at: float
    refresh: Callable[[], Awaitable[RecordHlsDescriptor | str]] | None
    refresh_lock: asyncio.Lock
    refresh_task: asyncio.Task[RecordHlsDescriptor | str] | None = None
    refreshed_segments: dict[str, HlsSegment] | None = None
    refreshed_resources: dict[str, HlsResource] | None = None
    last_refresh_at: float = 0.0
    refresh_failures: int = 0
    refresh_backoff: float = REFRESH_COOLDOWN_SECONDS
    generation: int = 0
    active: bool = True
    media_playlist_url: str = ""
    live: bool = False
    window_indexes: list[int] = field(default_factory=list)
    last_playlist_fetch_at: float = 0
    segment_prefetch: dict[int, bytes] = field(default_factory=dict)
    signed_paths: dict[tuple[str, int], tuple[str, float]] = field(default_factory=dict)
    upstream_fetch_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Progressive remuxing can outlive both the signed source and the idle TTL.
    stream_ttl_seconds: float | None = None
    # Local recordings arrive as a rolling live window; keeping issued segments
    # (and their tag lines) lets a player that opens late still start at the
    # clip's beginning.
    following: bool = False
    playlist_header: list[str] | None = None
    playlist_trailer: list[str] | None = None
    segment_blocks: list[list[str]] = field(default_factory=list)
    content_indexes: dict[str, int] = field(default_factory=dict)
    # End of the presentation span already listed for remux. A new segment that
    # starts before this still overlaps prior content even when its start PTS
    # moves forward (localvod's sliding window); ffmpeg copy then remuxes the
    # overlap as a repeated scene on a monotonic timeline.
    max_listed_end: float | None = None
    # When each localvod slice resets PTS (or PTS cannot be read), fall back to
    # keeping every Nth upstream segment so a 3-wide / 2s-slide window does not
    # remux the same scene twice.
    slide_stride: int = 1
    upstream_seen: int = 0
    prefetch_task: asyncio.Task[None] | None = None
    follow_timer: Callable[[], None] | None = None
    follow_failures: int = 0

    def close(self) -> asyncio.Task[RecordHlsDescriptor | str] | None:
        """Release callbacks and cancel an in-progress refresh."""
        self.active = False
        self.refresh = None
        if (cancel_follow := self.follow_timer) is not None:
            cancel_follow()
        self.follow_timer = None
        refresh_task = self.refresh_task
        if refresh_task is not None:
            refresh_task.cancel()
        self.refresh_task = None
        if self.prefetch_task is not None:
            self.prefetch_task.cancel()
        self.prefetch_task = None
        self.segments.clear()
        self.window_indexes.clear()
        self.segment_prefetch.clear()
        self.signed_paths.clear()
        self.segment_blocks.clear()
        self.playlist_header = None
        self.playlist_trailer = None
        self.content_indexes.clear()
        self.max_listed_end = None
        self.slide_stride = 1
        self.upstream_seen = 0
        self.signed_paths.clear()
        if self.refreshed_segments is not None:
            self.refreshed_segments.clear()
        for resources in self.resources.values():
            resources.clear()
        if self.refreshed_resources is not None:
            self.refreshed_resources.clear()
        return refresh_task


def async_register_record_hls_proxy(hass: HomeAssistant) -> None:
    """Register HLS proxy views once."""
    if hass.data.get(DATA_RECORD_HLS_PROXY_REGISTERED):
        return
    hass.http.register_view(ImouRecordHlsPlaylistView())
    hass.http.register_view(ImouRecordHlsSegmentView())
    hass.http.register_view(ImouRecordHlsResourceView())
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


async def async_remove_record_hls_proxy_playlist_session(
    hass: HomeAssistant, playlist_url: str
) -> None:
    """Release the proxy session backing one generated playlist URL."""
    path = urlsplit(playlist_url).path
    prefix = "/api/imou_life/record_hls/"
    suffix = "/playlist.m3u8"
    if not path.startswith(prefix) or not path.endswith(suffix):
        return
    session_id = path[len(prefix) : -len(suffix)]
    if not session_id or "/" in session_id:
        return
    task = _remove_session(hass, session_id)
    _schedule_cleanup(hass)
    if task is not None:
        await asyncio.gather(task, return_exceptions=True)


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


def _loopback_host(hass: HomeAssistant, api: object) -> str:
    """Pick the local address this Home Assistant server listens on."""
    http_server = getattr(hass, "http", None)
    server_host = getattr(http_server, "server_host", None)
    if server_host is None:
        return "127.0.0.1"
    hosts = [server_host] if isinstance(server_host, str) else list(server_host)
    if any(host in ("0.0.0.0", "127.0.0.1", "localhost") for host in hosts):
        return "127.0.0.1"
    if any(host in ("::", "::1") for host in hosts):
        return "[::1]"
    if hosts:
        host = hosts[0]
        return f"[{host}]" if ":" in host and not host.startswith("[") else host
    host = getattr(api, "host", None)
    if not host or host in ("0.0.0.0", "::", "127.0.0.1", "localhost"):
        return "127.0.0.1"
    return f"[{host}]" if ":" in host and not host.startswith("[") else host


def record_hls_proxy_origin(
    hass: HomeAssistant,
    *,
    fallback_url: Callable[[HomeAssistant], str] | None = None,
) -> str:
    """Return an origin this Home Assistant process can open on itself.

    Stream/PyAV and ffmpeg run on the Home Assistant host, not in the browser.
    Always prefer loopback on the API port so external URLs, reverse proxies,
    hairpin NAT, and split DNS cannot break server-side playback for remote users.
    """
    api = getattr(hass.config, "api", None)
    if api is not None:
        port = getattr(api, "port", None)
        if port:
            scheme = "https" if getattr(api, "use_ssl", False) else "http"
            return f"{scheme}://{_loopback_host(hass, api)}:{port}"
    resolver = fallback_url or get_url
    return resolver(hass).rstrip("/")


def _proxy_origin(hass: HomeAssistant) -> str:
    """Origin this Home Assistant process can open, for Stream's second fetch."""
    try:
        return record_hls_proxy_origin(hass)
    except NoURLAvailableError:
        return ""


def _absolute_proxy_url(hass: HomeAssistant, signed_path: str) -> str:
    """Turn a signed /api path into an absolute URL on this Home Assistant."""
    if signed_path.startswith(("http://", "https://")):
        return signed_path
    origin = _proxy_origin(hass)
    if not origin:
        return signed_path
    path = signed_path if signed_path.startswith("/") else f"/{signed_path}"
    return f"{origin}{path}"


def _first_program_date_time(body: str) -> str | None:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            return stripped.split(":", 1)[1]
    return None


def _is_sliding_playlist(body: str) -> bool:
    """A public playlist that keeps moving until the clip ends."""
    if "#EXT-X-ENDLIST" in body:
        return False
    return any(
        line.strip().startswith("#EXT-X-MEDIA-SEQUENCE:") for line in body.splitlines()
    )


_SEGMENT_PREFIX_TAGS = ("#EXTINF:", "#EXT-X-PROGRAM-DATE-TIME:")
_ENCRYPTION_TAGS = ("#EXT-X-KEY:", "#EXT-X-MAP:")


def _split_playlist_segments(
    body: str,
) -> tuple[list[str], list[list[str]], list[str]]:
    """Split a media playlist into header, per-segment tags, and trailer lines."""
    header: list[str] = []
    blocks: list[list[str]] = []
    pending: list[str] = []
    started = False
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            if not started and stripped.startswith(_SEGMENT_PREFIX_TAGS):
                started = True
            (pending if started else header).append(line)
            continue
        blocks.append(pending)
        pending = []
        started = True
    if not started or not blocks:
        return [], [], []
    return header, blocks, pending


def _is_encrypted_playlist(body: str) -> bool:
    return any(line.strip().startswith(_ENCRYPTION_TAGS) for line in body.splitlines())


def _presentation_time_bounds(body: bytes) -> tuple[float | None, float | None]:
    """Return the earliest and latest PES presentation times (seconds)."""
    earliest: float | None = None
    latest: float | None = None
    offset = 0
    limit = len(body) - _TS_PACKET_SIZE
    while offset <= limit:
        if body[offset] != _TS_SYNC_BYTE:
            offset += 1
            continue
        packet = body[offset : offset + _TS_PACKET_SIZE]
        offset += _TS_PACKET_SIZE
        if (
            not packet[1] & 0x40
            or ((packet[1] & 0x1F) << 8 | packet[2]) == _TS_NULL_PID
        ):
            continue
        adaptation = (packet[3] >> 4) & 0x03
        position = 4
        if adaptation & 0x02:
            position += 1 + packet[4]
        if not adaptation & 0x01 or position + 14 > _TS_PACKET_SIZE:
            continue
        payload = packet[position:]
        if payload[:3] != b"\x00\x00\x01" or not payload[7] & 0x80:
            continue
        stamp = payload[9:14]
        presentation_time = (
            ((stamp[0] >> 1) & 0x07) << 30
            | stamp[1] << 22
            | ((stamp[2] >> 1) << 15)
            | stamp[3] << 7
            | stamp[4] >> 1
        ) / 90000
        if earliest is None or presentation_time < earliest:
            earliest = presentation_time
        if latest is None or presentation_time > latest:
            latest = presentation_time
    return earliest, latest


def _first_presentation_time(body: bytes) -> float | None:
    """Return the earliest PES presentation time (seconds) in a TS segment."""
    return _presentation_time_bounds(body)[0]


def _extinf_duration(block: list[str] | None) -> float | None:
    if not block:
        return None
    for line in block:
        stripped = line.strip()
        if not stripped.startswith("#EXTINF:"):
            continue
        try:
            return float(stripped.split(":", 1)[1].split(",", 1)[0])
        except ValueError:
            return None
    return None


def _coverage_end(
    start: float | None, body: bytes | None, block: list[str] | None
) -> float | None:
    """Return how far this segment covers on the presentation timeline."""
    if body:
        _earliest, latest = _presentation_time_bounds(body)
        if latest is not None and (start is None or latest > start + 0.01):
            return latest
    duration = _extinf_duration(block)
    if start is not None and duration is not None:
        return start + duration
    if body:
        return _presentation_time_bounds(body)[1]
    return start


def _overlaps_listed_coverage(start: float, max_end: float) -> bool:
    """Return whether start is still inside media already listed for remux.

    Localvod slides a long segment forward by a shorter step. Keeping every
    extending slide remuxes the overlap as a repeated scene on a monotonic
    timeline, so only segments that begin at (or after) the covered end are
    listed.
    """
    return start < max_end - _REWIND_TOLERANCE_SECONDS


def _starts_progress_reliably(
    starts: list[float | None], *, min_step: float
) -> bool:
    """Return whether every start is known and advances enough for PTS checks."""
    if len(starts) < 2 or any(start is None for start in starts):
        return False
    return all(
        starts[index] >= starts[index - 1] + min_step for index in range(1, len(starts))
    )


def _drop_rewound_segments(
    segments: list[HlsSegment],
    blocks: list[list[str]],
    segment_prefetch: dict[int, bytes],
) -> tuple[list[HlsSegment], list[list[str]], dict[int, bytes], float | None, int]:
    """Remove opening segments that rewind or overlap already covered media."""
    starts: list[float | None] = []
    ends: list[float | None] = []
    durations: list[float | None] = []
    for index, _segment in enumerate(segments):
        body = segment_prefetch.get(index)
        block = blocks[index] if index < len(blocks) else None
        start = _first_presentation_time(body) if body is not None else None
        starts.append(start)
        ends.append(_coverage_end(start, body, block))
        durations.append(_extinf_duration(block))

    typical = next((duration for duration in durations if duration), None)
    min_step = max(1.0, (typical or 2.0) * 0.4)
    if _starts_progress_reliably(starts, min_step=min_step):
        kept: list[HlsSegment] = []
        kept_blocks: list[list[str]] = []
        kept_prefetch: dict[int, bytes] = {}
        max_end: float | None = None
        dropped = 0
        for index, segment in enumerate(segments):
            body = segment_prefetch.get(index)
            block = blocks[index] if index < len(blocks) else None
            start = starts[index]
            end = ends[index]
            if (
                start is not None
                and max_end is not None
                and _overlaps_listed_coverage(start, max_end)
            ):
                dropped += 1
                continue
            if end is not None:
                max_end = end if max_end is None else max(max_end, end)
            if body is not None:
                kept_prefetch[len(kept)] = body
            kept.append(segment)
            if block is not None:
                kept_blocks.append(block)
        if dropped:
            _LOGGER.debug(
                "Imou live window dropped %s overlapping opening segment(s)", dropped
            )
        return kept, kept_blocks, kept_prefetch, max_end, 1

    # PTS resets per slice (common on localvod) or cannot be read: keep every
    # Nth segment from a sliding window so remux does not replay the overlap.
    stride = max(1, len(segments) - 1) if len(segments) >= 3 else 1
    kept = []
    kept_blocks = []
    kept_prefetch = {}
    dropped = 0
    for index, segment in enumerate(segments):
        block = blocks[index] if index < len(blocks) else None
        if index % stride != 0:
            dropped += 1
            continue
        body = segment_prefetch.get(index)
        if body is not None:
            kept_prefetch[len(kept)] = body
        kept.append(segment)
        if block is not None:
            kept_blocks.append(block)
    max_end = (len(kept) * typical) if typical is not None and kept else None
    if dropped:
        _LOGGER.debug(
            "Imou live window kept every %s opening segment(s); dropped %s slide(s)",
            stride,
            dropped,
        )
    return kept, kept_blocks, kept_prefetch, max_end, stride


def _adopt_sliding_window(
    session: RecordHlsSession,
    body: str,
    segment_urls: list[str],
    media_playlist_url: str,
) -> None:
    """Serve only the upstream window (fallback for encrypted live playlists)."""
    incoming = _segment_descriptors(body, segment_urls)
    known: dict[str, int] = {}
    for index, segment in enumerate(session.segments):
        if segment.identity and segment.identity not in known:
            known[segment.identity] = index
    window: list[int] = []
    for segment in incoming:
        if segment.identity and segment.identity in known:
            index = known[segment.identity]
            session.segments[index] = segment
        else:
            index = len(session.segments)
            session.segments.append(segment)
            if segment.identity:
                known[segment.identity] = index
        window.append(index)
    session.window_indexes = window
    session.playlist_header = None
    session.playlist_trailer = None
    session.segment_blocks.clear()
    session.playlist_body = body
    session.media_playlist_url = media_playlist_url
    session.live = _is_sliding_playlist(body)
    session.following = False
    _LOGGER.debug(
        "Imou live window adopted: %s segments, first %s (PDT %s)",
        len(window),
        window[0] if window else None,
        _first_program_date_time(body) or "none",
    )


def _replace_sliding_window(
    session: RecordHlsSession,
    body: str,
    segment_urls: list[str],
    media_playlist_url: str,
    *,
    incoming: list[HlsSegment] | None = None,
    window_times: list[float | None] | None = None,
    window_bodies: dict[str, bytes] | None = None,
    blocks: list[list[str]] | None = None,
) -> None:
    """Merge the upstream window without dropping or duplicating issued segments."""
    if not segment_urls:
        return
    if not session.following:
        _adopt_sliding_window(session, body, segment_urls, media_playlist_url)
        return
    if incoming is None:
        incoming = _segment_descriptors(body, segment_urls)
    header, split_blocks, trailer = _split_playlist_segments(body)
    if blocks is None:
        blocks = split_blocks
    if len(blocks) != len(incoming) or not header:
        _LOGGER.debug("Imou live window had an unexpected shape; keeping the old one")
        return
    if window_times is None:
        window_times = [None] * len(incoming)
    identity_indexes: dict[str, int] = {}
    for known_index, known_segment in enumerate(session.segments):
        if known_segment.identity and known_segment.identity not in identity_indexes:
            identity_indexes[known_segment.identity] = known_index
    appended = 0
    rewound = 0
    unpublished = 0
    for segment, block, start in zip(incoming, blocks, window_times, strict=True):
        index: int | None = None
        if segment.content is not None:
            index = session.content_indexes.get(segment.content)
        if index is None and segment.identity is not None:
            index = identity_indexes.get(segment.identity)
        body_bytes: bytes | None = None
        if window_bodies is not None:
            key = segment.content or segment.identity
            if key is not None:
                body_bytes = window_bodies.get(key)
        end = _coverage_end(start, body_bytes, block)
        if index is None:
            listed = segment.content or segment.identity
            if (
                window_bodies is not None
                and listed is not None
                and listed in window_bodies
                and not window_bodies[listed]
            ):
                # Upstream lists a segment before its bytes are published; stop
                # here so the window stays in order and retry on the next pass.
                unpublished += 1
                break
            if session.slide_stride > 1:
                session.upstream_seen += 1
                if (session.upstream_seen - 1) % session.slide_stride != 0:
                    # Same slide stride as the opening window: skip the overlap.
                    rewound += 1
                    continue
            elif (
                start is not None
                and session.max_listed_end is not None
                and _overlaps_listed_coverage(start, session.max_listed_end)
            ):
                # Upstream re-served media that is still inside the span already
                # listed for remux (same start, or a forward slide that overlaps).
                rewound += 1
                continue
            index = len(session.segments)
            session.segments.append(segment)
            session.segment_blocks.append(block)
            appended += 1
            if session.slide_stride > 1:
                duration = _extinf_duration(block)
                if duration is not None:
                    session.max_listed_end = (
                        duration
                        if session.max_listed_end is None
                        else session.max_listed_end + duration
                    )
        else:
            session.segments[index] = segment
            session.segment_blocks[index] = block
        if session.slide_stride <= 1 and end is not None:
            session.max_listed_end = (
                end
                if session.max_listed_end is None
                else max(session.max_listed_end, end)
            )
        if body_bytes is not None:
            session.segment_prefetch.setdefault(index, body_bytes)
        if segment.content is not None:
            session.content_indexes.setdefault(segment.content, index)
        if segment.identity is not None:
            identity_indexes.setdefault(segment.identity, index)
    session.window_indexes = list(range(len(session.segments)))
    session.playlist_header = header
    session.playlist_trailer = trailer
    if not unpublished:
        # Keeping the previous body makes the next refresh re-read this window
        # once the missing segment has been published.
        session.playlist_body = body
    session.media_playlist_url = media_playlist_url
    session.live = _is_sliding_playlist(body)
    _LOGGER.debug(
        "Imou live window: %s segments, appended %s, rewound %s, pending %s (PDT %s)",
        len(session.window_indexes),
        appended,
        rewound,
        unpublished,
        _first_program_date_time(body) or "none",
    )


async def _async_prefetch_upstream_segments(
    hass: HomeAssistant, session: RecordHlsSession, indexes: list[int]
) -> None:
    """Warm early segments while the player is still opening the playlist."""
    total_bytes = sum(len(body) for body in session.segment_prefetch.values())

    async def async_prefetch(index: int) -> None:
        nonlocal total_bytes
        if index < 0 or index >= len(session.segments):
            return
        if index in session.segment_prefetch:
            return
        if session.live and total_bytes >= _LIVE_PREFETCH_MAX_BYTES:
            return
        segment = session.segments[index]
        range_header: str | None = None
        if segment.byte_range is not None:
            range_header = segment.byte_range.request_header
        try:
            async with session.upstream_fetch_lock:
                upstream_response = await async_fetch_url_response(
                    hass,
                    segment.url,
                    range_header=range_header,
                )
        except (OSError, TimeoutError, ValueError):
            _LOGGER.debug("Imou HLS segment %s prefetch skipped", index)
            return
        if 200 <= upstream_response.status < 300 and upstream_response.body:
            session.segment_prefetch[index] = upstream_response.body
            total_bytes += len(upstream_response.body)
            if session.slide_stride <= 1:
                start = _first_presentation_time(upstream_response.body)
                end = _coverage_end(start, upstream_response.body, None)
                if end is not None and (
                    session.max_listed_end is None or end > session.max_listed_end
                ):
                    session.max_listed_end = end

    for index in indexes:
        await async_prefetch(index)


def _schedule_live_prefetch(hass: HomeAssistant, session: RecordHlsSession) -> None:
    """Buffer the start of a live clip before the upstream window slides past it."""
    if not session.following:
        return
    if session.prefetch_task is not None and not session.prefetch_task.done():
        return
    pending = [
        index
        for index in session.window_indexes
        if index < _LIVE_PREFETCH_MAX_SEGMENTS and index not in session.segment_prefetch
    ]
    if not pending:
        return
    session.prefetch_task = hass.async_create_task(
        _async_prefetch_upstream_segments(hass, session, pending)
    )


def _schedule_live_follow(hass: HomeAssistant, session: RecordHlsSession) -> None:
    """Track the upstream window on a timer rather than on player requests."""
    if (
        not session.active
        or not session.following
        or not session.live
        or session.follow_timer is not None
    ):
        return

    async def async_follow_tick(_now: datetime) -> None:
        session.follow_timer = None
        if not session.active or not session.following or not session.live:
            return
        if not await _async_follow_live_playlist(hass, session, force=True):
            session.follow_failures += 1
            if session.follow_failures >= _LIVE_FOLLOW_MAX_FAILURES:
                _LOGGER.debug("Imou live window follow stopped after failures")
                return
        else:
            session.follow_failures = 0
        _schedule_live_follow(hass, session)

    session.follow_timer = async_call_later(
        hass, _LIVE_FOLLOW_INTERVAL_SECONDS, async_follow_tick
    )


def _unlisted_window_segments(
    session: RecordHlsSession, incoming: list[HlsSegment]
) -> list[HlsSegment]:
    """Derive the segments a window slid past between two playlist reads."""
    if not incoming or not session.segments:
        return []
    template = incoming[0]
    if (
        template.byte_range is not None
        or not template.valid
        or template.content is None
        or _SEGMENT_NUMBER_RE.search(template.content) is None
    ):
        return []
    shape = _SEGMENT_NUMBER_RE.sub("seg_%d.ts", template.url)
    numbers: list[int] = []
    for segment in incoming:
        if (
            segment.byte_range is not None
            or _SEGMENT_NUMBER_RE.sub("seg_%d.ts", segment.url) != shape
        ):
            return []
        numbers.append(int(_SEGMENT_NUMBER_RE.search(segment.url).group(1)))  # type: ignore[union-attr]
    highest = -1
    for segment in session.segments:
        if (
            segment.byte_range is None
            and _SEGMENT_NUMBER_RE.sub("seg_%d.ts", segment.url) == shape
            and (found := _SEGMENT_NUMBER_RE.search(segment.url)) is not None
        ):
            highest = max(highest, int(found.group(1)))
    first = min(numbers)
    if first <= highest + 1 or first - highest - 1 > _WINDOW_GAP_MAX_SEGMENTS:
        return []
    return [
        HlsSegment(
            _SEGMENT_NUMBER_RE.sub(f"seg_{number}.ts", template.url),
            None,
            None,
            True,
            _SEGMENT_NUMBER_RE.sub(f"seg_{number}.ts", template.content),
        )
        for number in range(highest + 1, first)
    ]


async def _async_buffer_window_segments(
    hass: HomeAssistant,
    session: RecordHlsSession,
    incoming: list[HlsSegment],
    *,
    probe_known: bool = True,
) -> tuple[list[float | None], dict[str, bytes]]:
    """Download window segments and read each start time.

    When ``probe_known`` is false, segments already listed on the session skip
    the network fetch (their PTS watermark was recorded when they were kept).
    """
    times: list[float | None] = []
    bodies: dict[str, bytes] = {}
    known_identities = {
        segment.identity for segment in session.segments if segment.identity is not None
    }
    for segment in incoming:
        key = segment.content or segment.identity
        body: bytes | None = None
        already_listed = (
            segment.content is not None and segment.content in session.content_indexes
        ) or (segment.identity is not None and segment.identity in known_identities)
        if segment.content is not None:
            known = session.content_indexes.get(segment.content)
            if known is not None:
                body = session.segment_prefetch.get(known)
        if already_listed and body is None and not probe_known:
            times.append(None)
            continue
        if body is None and segment.byte_range is None:
            try:
                async with session.upstream_fetch_lock:
                    upstream_response = await async_fetch_url_response(
                        hass, segment.url
                    )
            except (OSError, TimeoutError, ValueError):
                upstream_response = None
            if upstream_response is not None and 200 <= upstream_response.status < 300:
                body = upstream_response.body
        # An empty 200 means the segment is listed but not published yet; keep
        # it out of ``bodies`` so live windows still append and retry on serve.
        if body and key is not None:
            bodies[key] = body
        times.append(_first_presentation_time(body) if body else None)
    return times, bodies


async def _async_follow_live_playlist(
    hass: HomeAssistant,
    session: RecordHlsSession,
    *,
    force: bool = False,
) -> bool:
    """Re-read a moving public playlist and adopt its current window."""
    if not session.live or not session.media_playlist_url:
        return True
    now = time.time()
    if session.stream_ttl_seconds:
        session.expires_at = max(session.expires_at, now + session.stream_ttl_seconds)
    if not force and now - session.last_playlist_fetch_at < PLAYLIST_FOLLOW_MIN_SECONDS:
        return True
    playlist_url = session.media_playlist_url
    try:
        body = await async_fetch_url_text(hass, playlist_url)
    except (OSError, TimeoutError, ValueError):
        _LOGGER.debug("Imou live recording playlist was not reloaded")
        return False
    media_playlist_url = getattr(body, "final_url", playlist_url)
    segment_urls = parse_hls_media_segment_urls(body, media_playlist_url)
    if not segment_urls:
        return True
    # Compare the whole body, not just the media sequence: while the clip is
    # being produced the window grows without the sequence changing, and a
    # frozen proxy playlist makes the player treat a later jump as the end.
    if str(body) == session.playlist_body:
        session.last_playlist_fetch_at = now
        return True
    try:
        await _async_pin_segment_urls(hass, segment_urls)
    except ValueError:
        _LOGGER.debug("Imou live recording playlist pin skipped")
        return False
    if not session.active:
        return True
    blocks: list[list[str]] | None = None
    window_times: list[float | None] | None = None
    window_bodies: dict[str, bytes] | None = None
    incoming: list[HlsSegment] | None = None
    if session.following:
        incoming = _segment_descriptors(body, segment_urls)
        _header, split_blocks, _trailer = _split_playlist_segments(body)
        if missing := _unlisted_window_segments(session, incoming):
            incoming = missing + incoming
            blocks = [split_blocks[0]] * len(missing) + split_blocks
        else:
            blocks = split_blocks
        # Probe PTS while the clip is still live so overlapping slides and
        # PTS resets can be filtered before ffmpeg remuxes them. Re-download
        # known segments only until the coverage watermark is seeded.
        window_times, window_bodies = await _async_buffer_window_segments(
            hass,
            session,
            incoming,
            probe_known=session.max_listed_end is None and session.slide_stride <= 1,
        )
        if not _is_sliding_playlist(body):
            # Trailing entries on the final window are listed past the clip's
            # end and never get bytes.
            while incoming and not window_bodies.get(
                incoming[-1].content or incoming[-1].identity or ""
            ):
                incoming.pop()
                window_times.pop()
                blocks.pop()
    async with session.refresh_lock:
        if not session.active or not session.live:
            return True
        _replace_sliding_window(
            session,
            body,
            segment_urls,
            media_playlist_url,
            incoming=incoming,
            window_times=window_times,
            window_bodies=window_bodies,
            blocks=blocks,
        )
        session.last_playlist_fetch_at = now
        _schedule_live_prefetch(hass, session)
    return True


def _signed_proxy_path(
    hass: HomeAssistant,
    session: RecordHlsSession,
    key: tuple[str, int],
    path: str,
) -> str:
    """Sign one proxy path, reusing the signature until it nears expiry."""
    now = time.time()
    cached = session.signed_paths.get(key)
    if cached is not None and cached[1] - now > _SIGNED_PATH_REFRESH_MARGIN_SECONDS:
        return cached[0]
    remaining_ttl = max(0, session.expires_at - now)
    signed = _absolute_proxy_url(
        hass,
        async_sign_path(
            hass,
            path,
            timedelta(seconds=remaining_ttl),
            use_content_user=True,
        ),
    )
    session.signed_paths[key] = (signed, now + remaining_ttl)
    return signed


def _session_playlist_body(session: RecordHlsSession) -> str:
    """Return the playlist text, retaining every issued live segment."""
    if not session.following or session.playlist_header is None:
        return session.playlist_body
    lines = list(session.playlist_header)
    for index in session.window_indexes:
        if index < len(session.segment_blocks):
            lines.extend(session.segment_blocks[index])
        if index < len(session.segments):
            lines.append(session.segments[index].url)
    if session.playlist_trailer:
        lines.extend(session.playlist_trailer)
    return "\n".join(lines) + "\n"


def _build_proxy_playlist(
    hass: HomeAssistant,
    session_id: str,
    session: RecordHlsSession,
) -> str:
    lines: list[str] = []
    segment_index = 0
    resource_indexes = {"key": 0, "map": 0}
    pending_range_line: int | None = None
    for original in _session_playlist_body(session).splitlines():
        stripped = original.strip()
        if stripped.startswith("#EXT-X-BYTERANGE:"):
            pending_range_line = len(lines)
            lines.append(original)
            continue
        if stripped.startswith("#EXT-X-MEDIA-SEQUENCE:") and session.following:
            # Never pass a rewound upstream sequence through: the proxy window
            # only grows, so its sequence must too.
            first = session.window_indexes[0] if session.window_indexes else 0
            lines.append(f"#EXT-X-MEDIA-SEQUENCE:{first}")
            continue
        if stripped.startswith(("#EXT-X-KEY:", "#EXT-X-MAP:")):
            kind = "key" if stripped.startswith("#EXT-X-KEY:") else "map"
            if "URI=" not in stripped:
                lines.append(original)
                continue
            index = resource_indexes[kind]
            resource_indexes[kind] += 1
            resource_path = RESOURCE_URL.format(
                session_id=session_id, kind=kind, index=index
            )
            signed = _signed_proxy_path(hass, session, (kind, index), resource_path)
            lines.append(re.sub(r'URI="[^"]*"', f'URI="{signed}"', original, count=1))
            continue
        if stripped and not stripped.startswith("#"):
            absolute_index = (
                session.window_indexes[segment_index]
                if segment_index < len(session.window_indexes)
                else segment_index
            )
            segment = session.segments[absolute_index]
            if pending_range_line is not None and segment.byte_range is not None:
                lines[pending_range_line] = (
                    "#EXT-X-BYTERANGE:"
                    f"{segment.byte_range.length}@{segment.byte_range.start}"
                )
            segment_path = SEGMENT_URL.format(
                session_id=session_id, index=absolute_index
            )
            lines.append(
                _signed_proxy_path(
                    hass, session, ("segment", absolute_index), segment_path
                )
            )
            segment_index += 1
            pending_range_line = None
            continue
        lines.append(original)
    return "\n".join(lines) + "\n"


def _segment_descriptors(
    playlist_body: str, segment_urls: list[str]
) -> list[HlsSegment]:
    """Parse absolute byte ranges and stable identities for media segments."""
    media_sequence: int | None = None
    for line in playlist_body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            continue
        try:
            media_sequence = int(stripped.split(":", 1)[1])
        except ValueError:
            break
        break

    metadata: list[tuple[HlsByteRange | None, str | None, bool]] = []
    pending_byte_range: str | None = None
    has_byte_range = False
    program_date_time: str | None = None
    previous_range: HlsByteRange | None = None
    previous_resource: tuple[str, str, str, str] | None = None
    segment_index = 0
    for line in playlist_body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#EXT-X-BYTERANGE:"):
            pending_byte_range = stripped.split(":", 1)[1]
            has_byte_range = True
        elif stripped.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            program_date_time = stripped.split(":", 1)[1]
        elif stripped and not stripped.startswith("#"):
            if not urlsplit(stripped).path.lower().endswith(".m3u8"):
                path = (
                    urlsplit(segment_urls[segment_index]).path
                    if segment_index < len(segment_urls)
                    else urlsplit(stripped).path
                )
                parsed_resource = urlsplit(
                    segment_urls[segment_index]
                    if segment_index < len(segment_urls)
                    else stripped
                )
                resource = (
                    parsed_resource.scheme.lower(),
                    parsed_resource.netloc.lower(),
                    parsed_resource.path,
                    parsed_resource.query,
                )
                byte_range: HlsByteRange | None = None
                valid = True
                if has_byte_range:
                    match = re.fullmatch(
                        r"(?P<length>\d+)(?:@(?P<offset>\d+))?",
                        pending_byte_range or "",
                    )
                    if match is None or int(match.group("length")) <= 0:
                        valid = False
                    else:
                        length = int(match.group("length"))
                        if (offset := match.group("offset")) is not None:
                            start = int(offset)
                        elif (
                            previous_range is not None and previous_resource == resource
                        ):
                            start = previous_range.end + 1
                        else:
                            valid = False
                            start = 0
                        if valid:
                            byte_range = HlsByteRange(start, length)
                metadata.append((byte_range, program_date_time, valid))
                previous_range = byte_range
                previous_resource = resource if byte_range is not None else None
                segment_index += 1
            pending_byte_range = None
            has_byte_range = False
            program_date_time = None
    metadata.extend([(None, None, True)] * max(0, len(segment_urls) - len(metadata)))

    paths = [urlsplit(url).path for url in segment_urls]
    path_counts = Counter(paths)
    # Imou serves every segment from one path and names it in ``userid``; it is
    # the only stable discriminator once auth query tokens rotate.
    segment_ids = [
        (parse_qs(urlsplit(url).query).get("userid") or [""])[0] for url in segment_urls
    ]
    id_counts = Counter(segment_ids)
    segments: list[HlsSegment] = []
    for index, (url, path) in enumerate(zip(segment_urls, paths, strict=True)):
        segment_range, segment_time, valid = metadata[index]
        range_identity = (
            f"{segment_range.start}:{segment_range.length}"
            if segment_range is not None
            else ""
        )
        if not valid:
            identity = None
        elif media_sequence is not None:
            identity = f"sequence:{media_sequence + index}|range:{range_identity}"
        elif path_counts[path] == 1:
            identity = f"path:{path}|range:{range_identity}"
        elif segment_range is not None or segment_time is not None:
            identity = f"path:{path}|range:{range_identity}|time:{segment_time or ''}"
        else:
            identity = None
        if segment_time is not None:
            content = f"time:{segment_time}"
        elif segment_range is not None:
            content = f"path:{path}|range:{range_identity}"
        elif path_counts[path] == 1:
            content = f"path:{path}"
        elif segment_ids[index] and id_counts[segment_ids[index]] == 1:
            content = f"path:{path}|userid:{segment_ids[index]}"
        else:
            content = None
        segments.append(HlsSegment(url, identity, segment_range, valid, content))

    identity_counts = Counter(
        segment.identity for segment in segments if segment.identity is not None
    )
    duplicate_identities = {
        identity for identity, count in identity_counts.items() if count > 1
    }
    if duplicate_identities:
        segments = [
            HlsSegment(
                segment.url,
                None if segment.identity in duplicate_identities else segment.identity,
                segment.byte_range,
                segment.valid,
                segment.content,
            )
            for segment in segments
        ]
    return segments


def _resource_identity(kind: str, url: str, byte_range: HlsByteRange | None) -> str:
    parsed = urlsplit(url)
    range_identity = (
        f"{byte_range.start}:{byte_range.length}" if byte_range is not None else ""
    )
    return (
        f"{kind}:{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path}"
        f"|range:{range_identity}"
    )


def _playlist_resource_descriptors(
    playlist_body: str, base_url: str
) -> dict[str, list[HlsResource]]:
    resources: dict[str, list[HlsResource]] = {"key": [], "map": []}
    previous_map_range: HlsByteRange | None = None
    previous_map_url: str | None = None
    for line in playlist_body.splitlines():
        stripped = line.strip()
        if (
            not stripped.startswith(("#EXT-X-KEY:", "#EXT-X-MAP:"))
            and "URI=" not in stripped
        ):
            continue
        if stripped.startswith("#EXT-X-KEY:"):
            kind = "key"
            method = re.search(r"(?:^|,)METHOD=([^,]*)", stripped.split(":", 1)[1])
            if method is None:
                raise ValueError("Malformed HLS key")
            uri_match = re.search(r'(?:^|,)URI="([^"]*)"', stripped.split(":", 1)[1])
            if method.group(1) == "NONE":
                if uri_match is not None:
                    raise ValueError("METHOD=NONE must not have a URI")
                continue
            if uri_match is None:
                raise ValueError("HLS encryption key has no quoted URI")
            byte_range = None
        elif stripped.startswith("#EXT-X-MAP:"):
            kind = "map"
            uri_match = re.search(r'(?:^|,)URI="([^"]*)"', stripped.split(":", 1)[1])
            if uri_match is None:
                raise ValueError("HLS map has no quoted URI")
            uri = urljoin(base_url, uri_match.group(1))
            range_match = re.search(
                r'(?:^|,)BYTERANGE="(\d+)(?:@(\d+))?"',
                stripped.split(":", 1)[1],
            )
            if "BYTERANGE=" in stripped and range_match is None:
                raise ValueError("Malformed HLS map byte range")
            if range_match is not None:
                length = int(range_match.group(1))
                if length <= 0:
                    raise ValueError("Malformed HLS map byte range")
                if range_match.group(2) is not None:
                    start = int(range_match.group(2))
                elif previous_map_range is not None and previous_map_url == uri:
                    start = previous_map_range.end + 1
                else:
                    raise ValueError("HLS map byte range requires an offset")
                byte_range = HlsByteRange(start, length)
            else:
                byte_range = None
        else:
            raise ValueError("Unsupported HLS URI tag")
        if kind == "key":
            uri = urljoin(base_url, uri_match.group(1))
        resources[kind].append(
            HlsResource(uri, _resource_identity(kind, uri, byte_range), byte_range)
        )
        if kind == "map":
            previous_map_range = byte_range
            previous_map_url = uri if byte_range is not None else None
    return resources


async def _async_validate_playlist_targets(
    hass: HomeAssistant,
    resources: dict[str, list[HlsResource]],
) -> None:
    for url in (resource.url for values in resources.values() for resource in values):
        await async_pin_public_url(hass, url)


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


def _upstream_range_header(segment: HlsSegment, client_range: str | None) -> str | None:
    """Return a safe upstream Range constrained to the HLS descriptor."""
    if not segment.valid:
        msg = "Malformed HLS byte range"
        raise ValueError(msg)
    if segment.byte_range is None:
        return client_range
    if client_range is None:
        return segment.byte_range.request_header
    match = re.fullmatch(r"bytes=(\d+)-(\d+)", client_range.strip())
    if match is None:
        msg = "Unsupported Range for HLS byte-range segment"
        raise ValueError(msg)
    start, end = (int(value) for value in match.groups())
    if start > end or start < segment.byte_range.start or end > segment.byte_range.end:
        msg = "Range exceeds HLS byte-range segment"
        raise ValueError(msg)
    return f"bytes={start}-{end}"


def _validate_byte_range_response(
    segment: HlsSegment | HlsResource,
    requested_range: str | None,
    status: int,
    headers,
) -> None:
    """Reject upstream responses that could expose bytes outside a descriptor."""
    if segment.byte_range is None:
        return
    if status != 206 or requested_range is None:
        msg = "Upstream ignored HLS byte range"
        raise ValueError(msg)
    content_range = next(
        (value for name, value in headers.items() if name.lower() == "content-range"),
        None,
    )
    match = (
        re.fullmatch(r"bytes (\d+)-(\d+)/(?:\d+|\*)", content_range)
        if content_range is not None
        else None
    )
    requested = re.fullmatch(r"bytes=(\d+)-(\d+)", requested_range)
    if match is None or requested is None or match.groups() != requested.groups():
        msg = "Upstream returned the wrong HLS byte range"
        raise ValueError(msg)


def _pin_cache_key(url: str) -> tuple[str, str, int] | None:
    """Return (scheme, host, port) for safe URLs so repeated segments skip DNS."""
    if url != url.strip() or any(ord(character) < 32 for character in url):
        return None
    try:
        parsed = urlsplit(url)
        explicit_port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or explicit_port == 0
    ):
        return None
    port = explicit_port or (443 if parsed.scheme.lower() == "https" else 80)
    return (parsed.scheme.lower(), parsed.hostname.lower(), port)


async def _async_pin_segment_urls(hass: HomeAssistant, urls: list[str]) -> None:
    """Pin each unique public segment host once instead of repeating DNS lookups."""
    seen: set[tuple[str, str, int]] = set()
    for segment_url in urls:
        key = _pin_cache_key(segment_url)
        if key is not None and key in seen:
            continue
        await async_pin_public_url(hass, segment_url)
        if key is not None:
            seen.add(key)


async def _async_fetch_playlist_segment_body(
    hass: HomeAssistant,
    loaded: _LoadedMediaPlaylist,
    index: int,
) -> bytes | None:
    """Fetch one upstream media segment when the playlist lists it."""
    if index < 0 or index >= len(loaded.segment_urls):
        return None
    segments = _segment_descriptors(loaded.body, loaded.segment_urls)
    if index >= len(segments):
        return None
    segment = segments[index]
    if not segment.valid:
        return None
    range_header: str | None = None
    if segment.byte_range is not None:
        range_header = segment.byte_range.request_header
    try:
        response = await async_fetch_url_response(
            hass,
            segment.url,
            range_header=range_header,
        )
    except (OSError, TimeoutError, ValueError):
        return None
    if not 200 <= response.status < 300 or not response.body:
        return None
    try:
        _validate_byte_range_response(
            segment,
            range_header,
            response.status,
            response.headers,
        )
    except ValueError:
        return None
    return response.body


async def _async_upstream_first_segment_ready(
    hass: HomeAssistant,
    loaded: _LoadedMediaPlaylist,
) -> PreparedRecordHlsPlayback | None:
    """Return a snapshot when the playlist's first media segment can be fetched."""
    first_body = await _async_fetch_playlist_segment_body(hass, loaded, 0)
    if first_body is None:
        return None
    prefetch: dict[int, bytes] = {0: first_body}
    warm_through = min(
        _LOCAL_PREPARE_WARM_SEGMENT_COUNT,
        len(loaded.segment_urls),
    )
    for index in range(1, warm_through):
        body = await _async_fetch_playlist_segment_body(hass, loaded, index)
        if body is None:
            break
        prefetch[index] = body
    return PreparedRecordHlsPlayback(loaded, prefetch)


async def async_prepare_local_record_playback(
    hass: HomeAssistant,
    playlist_url: str,
    *,
    max_wait: float = _LOCAL_PLAYBACK_PREPARE_MAX_SECONDS,
) -> PreparedRecordHlsPlayback:
    """Poll localvod until Lechange publishes a fetchable first segment."""
    deadline = time.time() + max_wait
    last_log = 0.0
    while time.time() < deadline:
        try:
            loaded = await _load_media_playlist(hass, playlist_url)
        except (OSError, TimeoutError, ValueError):
            await asyncio.sleep(_LOCAL_PLAYBACK_PREPARE_POLL_SECONDS)
            continue
        prepared = await _async_upstream_first_segment_ready(hass, loaded)
        if prepared is not None:
            elapsed = max_wait - (deadline - time.time())
            _LOGGER.debug("Imou local recording segments ready after %.1fs", elapsed)
            return prepared
        now = time.time()
        if now - last_log >= 5.0:
            last_log = now
            _LOGGER.debug("Waiting for Imou local recording segments to publish")
        await asyncio.sleep(_LOCAL_PLAYBACK_PREPARE_POLL_SECONDS)
    msg = "Imou local recording segments were not ready in time"
    raise TimeoutError(msg)


async def _load_media_playlist(
    hass: HomeAssistant, playlist_url: str
) -> _LoadedMediaPlaylist:
    body = await async_fetch_url_text(hass, playlist_url)
    media_playlist_url = getattr(body, "final_url", playlist_url)
    segment_urls = parse_hls_media_segment_urls(body, media_playlist_url)
    if segment_urls:
        await _async_pin_segment_urls(hass, segment_urls)
        return _LoadedMediaPlaylist(body, segment_urls, media_playlist_url)
    variant = pick_hls_variant_playlist_url(body, media_playlist_url)
    if variant is None:
        return _LoadedMediaPlaylist(body, segment_urls, media_playlist_url)
    await async_pin_public_url(hass, variant)
    body = await async_fetch_url_text(hass, variant)
    media_playlist_url = getattr(body, "final_url", variant)
    segment_urls = parse_hls_media_segment_urls(body, media_playlist_url)
    await _async_pin_segment_urls(hass, segment_urls)
    return _LoadedMediaPlaylist(body, segment_urls, media_playlist_url)


async def async_create_record_hls_proxy_playlist_url(
    hass: HomeAssistant,
    upstream_playlist_url: str,
    *,
    refresh: Callable[[], Awaitable[RecordHlsDescriptor | str]],
    entry_id: str,
    expires_at: float | None = None,
    prepared_playback: PreparedRecordHlsPlayback | None = None,
    stream_ttl_seconds: float | None = None,
) -> str:
    """Fetch Imou m3u8 and return a signed HA playlist URL Stream can open."""
    _prune_sessions(hass)
    if prepared_playback is not None:
        loaded = prepared_playback.loaded
    else:
        loaded = await _load_media_playlist(hass, upstream_playlist_url)
    if isinstance(loaded, _LoadedMediaPlaylist):
        body, segment_urls, media_playlist_url = (
            loaded.body,
            loaded.segment_urls,
            loaded.base_url,
        )
    else:
        body, segment_urls = loaded
        media_playlist_url = upstream_playlist_url
    if not segment_urls:
        msg = "Imou HLS playlist had no segments"
        raise ValueError(msg)
    segments = _segment_descriptors(body, segment_urls)
    resources = _playlist_resource_descriptors(body, media_playlist_url)
    await _async_validate_playlist_targets(hass, resources)
    session_id = secrets.token_hex(16)
    live = _is_sliding_playlist(body)
    header, blocks, trailer = _split_playlist_segments(body) if live else ([], [], [])
    following = (
        live and not _is_encrypted_playlist(body) and len(blocks) == len(segments)
    )
    segment_prefetch: dict[int, bytes] = {}
    if prepared_playback is not None:
        for index, prefetched_body in prepared_playback.segment_prefetch.items():
            if 0 <= index < len(segments):
                segment_prefetch[index] = prefetched_body
    max_listed_end: float | None = None
    slide_stride = 1
    upstream_seen = 0
    if following:
        # Need bodies for the whole first window so forward-overlapping slides
        # and PTS-reset windows are filtered before ffmpeg remuxes them.
        for index, segment in enumerate(segments):
            if index in segment_prefetch or segment.byte_range is not None:
                continue
            try:
                upstream_response = await async_fetch_url_response(hass, segment.url)
            except (OSError, TimeoutError, ValueError):
                continue
            if 200 <= upstream_response.status < 300 and upstream_response.body:
                segment_prefetch[index] = upstream_response.body
        if segment_prefetch:
            upstream_seen = len(segments)
            segments, blocks, segment_prefetch, max_listed_end, slide_stride = (
                _drop_rewound_segments(segments, blocks, segment_prefetch)
            )
            if slide_stride <= 1:
                upstream_seen = 0
    content_indexes: dict[str, int] = {}
    for index, segment in enumerate(segments):
        if segment.content is not None:
            content_indexes.setdefault(segment.content, index)
    _LOGGER.debug(
        "Imou HLS session: %s segments, first PDT %s, live=%s, following=%s, stride=%s",
        len(segments),
        _first_program_date_time(body) or "none",
        live,
        following,
        slide_stride,
    )
    session_expiry = _session_expiry(
        upstream_playlist_url,
        segment_urls,
        expires_at,
    )
    if stream_ttl_seconds is not None:
        session_expiry = max(session_expiry, time.time() + stream_ttl_seconds)
    session = RecordHlsSession(
        entry_id=entry_id,
        segments=segments,
        resources=resources,
        playlist_body=body,
        expires_at=session_expiry,
        refresh=refresh,
        refresh_lock=asyncio.Lock(),
        media_playlist_url=media_playlist_url,
        live=live,
        window_indexes=list(range(len(segments))),
        last_playlist_fetch_at=time.time(),
        stream_ttl_seconds=stream_ttl_seconds,
        following=following,
        playlist_header=header if following else None,
        playlist_trailer=trailer if following else None,
        segment_blocks=blocks if following else [],
        content_indexes=content_indexes,
        max_listed_end=max_listed_end,
        slide_stride=slide_stride,
        upstream_seen=upstream_seen,
    )
    _sessions(hass)[session_id] = session
    session.segment_prefetch.update(segment_prefetch)
    if prepared_playback is None and not live and session.segments:
        hass.async_create_task(
            _async_prefetch_upstream_segments(hass, session, [0]),
        )
    _schedule_live_prefetch(hass, session)
    _schedule_live_follow(hass, session)
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


async def _async_refresh_record_hls_session(
    hass: HomeAssistant,
    session_id: str,
    record: RecordHlsSession,
    generation: int,
) -> bool:
    """Refresh one session and preserve its original signed handles."""
    async with record.refresh_lock:
        if not record.active or _sessions(hass).get(session_id) is not record:
            raise web.HTTPNotFound()
        if generation != record.generation:
            return True
        now = time.time()
        if (
            record.refresh is None
            or record.refresh_failures >= _REFRESH_MAX_FAILURES
            or now - record.last_refresh_at < record.refresh_backoff
        ):
            return False
        record.last_refresh_at = now
        try:

            async def async_refresh() -> RecordHlsDescriptor | str:
                if record.refresh is None:
                    raise web.HTTPNotFound()
                return await record.refresh()

            record.refresh_task = hass.async_create_task(async_refresh())
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
            loaded = await _load_media_playlist(hass, descriptor.playlist_url)
            if isinstance(loaded, _LoadedMediaPlaylist):
                body, segment_urls, media_playlist_url = (
                    loaded.body,
                    loaded.segment_urls,
                    loaded.base_url,
                )
            else:
                body, segment_urls = loaded
                media_playlist_url = descriptor.playlist_url
            if not record.active or _sessions(hass).get(session_id) is not record:
                raise web.HTTPNotFound()
            if not segment_urls:
                raise ValueError("Refreshed Imou HLS playlist had no segments")
            refreshed_segments = _segment_descriptors(body, segment_urls)
            refreshed_resources = _playlist_resource_descriptors(
                body, media_playlist_url
            )
            await _async_validate_playlist_targets(hass, refreshed_resources)
            record.refreshed_segments = {
                refreshed.identity: refreshed
                for refreshed in refreshed_segments
                if refreshed.identity is not None
            }
            record.refreshed_resources = {
                resource.identity: resource
                for values in refreshed_resources.values()
                for resource in values
            }
            record.expires_at = _session_expiry(
                descriptor.playlist_url,
                segment_urls,
                descriptor.expires_at,
            )
            if record.stream_ttl_seconds is not None:
                record.expires_at = max(
                    record.expires_at, now + record.stream_ttl_seconds
                )
            # Prefetched bodies are segment content, not URLs; they stay valid.
            record.signed_paths.clear()
            record.refresh_failures = 0
            record.refresh_backoff = REFRESH_COOLDOWN_SECONDS
            record.generation += 1
            _schedule_cleanup(hass)
        except web.HTTPException:
            raise
        except Exception:
            record.refresh_failures += 1
            record.refresh_backoff = min(
                record.refresh_backoff * 2, _REFRESH_MAX_BACKOFF_SECONDS
            )
            _LOGGER.warning("Imou HLS session refresh failed", exc_info=True)
            return False
        return True


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
        if record.live:
            await _async_follow_live_playlist(hass, record)
            if not record.active or _sessions(hass).get(session_id) is not record:
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
        if seg_index < 0 or seg_index >= len(record.segments):
            raise web.HTTPNotFound()
        segment = record.segments[seg_index]
        identity = segment.identity
        # Prefetched bytes survive a refresh and bypass expiring upstream URLs.
        if (
            segment.byte_range is None
            and request.headers.get("Range") is None
            and (cached := record.segment_prefetch.get(seg_index)) is not None
        ):
            return web.Response(
                body=cached,
                status=200,
                headers={"Content-Type": "video/mp2t"},
            )
        if record.refreshed_segments is None:
            upstream = segment.url
        else:
            if identity is None:
                raise web.HTTPBadGateway()
            refreshed_segment = record.refreshed_segments.get(identity)
            if refreshed_segment is None:
                raise web.HTTPBadGateway()
            segment = refreshed_segment
            upstream = segment.url
        try:
            range_header = _upstream_range_header(segment, request.headers.get("Range"))
        except ValueError as err:
            raise web.HTTPBadGateway() from err
        generation = record.generation
        try:
            for _ in range(_UNPUBLISHED_SEGMENT_RETRIES + 1):
                upstream_response = await async_fetch_url_response(
                    hass,
                    upstream,
                    range_header=range_header,
                )
                if (
                    not 200 <= upstream_response.status < 300
                    or upstream_response.body
                    or not record.live
                ):
                    break
                await asyncio.sleep(_UNPUBLISHED_SEGMENT_RETRY_SECONDS)
        except (OSError, TimeoutError, ValueError) as err:
            _LOGGER.warning("Imou HLS segment %s fetch failed: %s", seg_index, err)
            raise web.HTTPBadGateway() from err
        if not record.active or _sessions(hass).get(session_id) is not record:
            raise web.HTTPNotFound()
        if upstream_response.status in _UPSTREAM_REFRESH_STATUSES and record.live:
            previous_url = upstream
            await _async_follow_live_playlist(hass, record, force=True)
            if not record.active or _sessions(hass).get(session_id) is not record:
                raise web.HTTPNotFound()
            if seg_index < len(record.segments):
                segment = record.segments[seg_index]
                if segment.url != previous_url:
                    upstream = segment.url
                    identity = segment.identity
                    try:
                        range_header = _upstream_range_header(
                            segment, request.headers.get("Range")
                        )
                    except ValueError as err:
                        raise web.HTTPBadGateway() from err
                    try:
                        upstream_response = await async_fetch_url_response(
                            hass,
                            upstream,
                            range_header=range_header,
                        )
                    except (OSError, TimeoutError, ValueError) as err:
                        _LOGGER.warning(
                            "Imou HLS segment %s fetch failed: %s", seg_index, err
                        )
                        raise web.HTTPBadGateway() from err
        if upstream_response.status in _UPSTREAM_REFRESH_STATUSES:
            _LOGGER.warning(
                "Imou HLS segment %s upstream HTTP %s; refreshing session",
                seg_index,
                upstream_response.status,
            )
            refreshed = await _async_refresh_record_hls_session(
                hass, session_id, record, generation
            )
            if not refreshed or identity is None or record.refreshed_segments is None:
                raise web.HTTPBadGateway()
            refreshed_segment = record.refreshed_segments.get(identity)
            if refreshed_segment is None:
                raise web.HTTPBadGateway()
            segment = refreshed_segment
            upstream = segment.url
            try:
                range_header = _upstream_range_header(
                    segment, request.headers.get("Range")
                )
            except ValueError as err:
                raise web.HTTPBadGateway() from err
            try:
                upstream_response = await async_fetch_url_response(
                    hass,
                    upstream,
                    range_header=range_header,
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
        try:
            _validate_byte_range_response(
                segment,
                range_header,
                upstream_response.status,
                upstream_response.headers,
            )
        except ValueError as err:
            raise web.HTTPBadGateway() from err
        if not 200 <= upstream_response.status < 300:
            _LOGGER.warning(
                "Imou HLS segment %s upstream HTTP %s",
                seg_index,
                upstream_response.status,
            )
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


class ImouRecordHlsResourceView(HomeAssistantView):
    """Proxy an HLS encryption key or initialization map."""

    requires_auth = True
    url = RESOURCE_URL
    name = "api:imou_life_record_hls_resource"
    cors_allowed = True

    async def get(
        self,
        request: web.Request,
        session_id: str,
        kind: str,
        index: str,
    ) -> web.StreamResponse:
        """Return one non-segment HLS resource."""
        hass = request.app["hass"]
        _prune_sessions(hass)
        record = _sessions(hass).get(session_id)
        if record is None or kind not in {"key", "map"}:
            raise web.HTTPNotFound()
        try:
            resource_index = int(index)
        except ValueError:
            raise web.HTTPNotFound() from None
        if resource_index < 0 or resource_index >= len(record.resources[kind]):
            raise web.HTTPNotFound()
        resource = record.resources[kind][resource_index]
        if record.refreshed_resources is not None:
            refreshed_resource = record.refreshed_resources.get(resource.identity)
            if refreshed_resource is None:
                raise web.HTTPBadGateway()
            resource = refreshed_resource
        original_identity = resource.identity
        generation = record.generation

        def range_for(candidate: HlsResource) -> str | None:
            return (
                candidate.byte_range.request_header
                if candidate.byte_range is not None
                else request.headers.get("Range")
            )

        range_header = range_for(resource)
        try:
            upstream = await async_fetch_url_response(
                hass, resource.url, range_header=range_header
            )
        except (OSError, TimeoutError, ValueError) as err:
            _LOGGER.warning(
                "Imou HLS %s %s fetch failed: %s", kind, resource_index, err
            )
            raise web.HTTPBadGateway() from err
        if upstream.status in _UPSTREAM_REFRESH_STATUSES:
            _LOGGER.warning(
                "Imou HLS %s %s upstream HTTP %s; refreshing session",
                kind,
                resource_index,
                upstream.status,
            )
            refreshed = await _async_refresh_record_hls_session(
                hass, session_id, record, generation
            )
            if not refreshed or record.refreshed_resources is None:
                raise web.HTTPBadGateway()
            refreshed_resource = record.refreshed_resources.get(original_identity)
            if refreshed_resource is None:
                raise web.HTTPBadGateway()
            resource = refreshed_resource
            range_header = range_for(resource)
            try:
                upstream = await async_fetch_url_response(
                    hass, resource.url, range_header=range_header
                )
            except (OSError, TimeoutError, ValueError) as err:
                _LOGGER.warning(
                    "Refreshed Imou HLS %s %s fetch failed: %s",
                    kind,
                    resource_index,
                    err,
                )
                raise web.HTTPBadGateway() from err
        try:
            _validate_byte_range_response(
                resource, range_header, upstream.status, upstream.headers
            )
        except ValueError as err:
            raise web.HTTPBadGateway() from err
        if not 200 <= upstream.status < 300:
            _LOGGER.warning(
                "Imou HLS %s %s upstream HTTP %s",
                kind,
                resource_index,
                upstream.status,
            )
            raise web.HTTPBadGateway()
        headers = {
            name: value
            for name, value in upstream.headers.items()
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
        headers["Content-Type"] = (
            "application/octet-stream" if kind == "key" else "video/mp4"
        )
        return web.Response(
            body=upstream.body,
            status=upstream.status,
            headers=headers,
        )
