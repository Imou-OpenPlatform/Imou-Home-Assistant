"""Imou cloud and local recordings in the Media Browser."""

from __future__ import annotations

import base64
import calendar
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import override

from homeassistant.components.media_player import BrowseError, MediaClass, MediaType
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.network import get_url
from homeassistant.util import dt as dt_util
from pyimouapi.cloud_record import parse_cloud_record_bitmap_days
from pyimouapi.const import ERROR_CODE_DEVICE_OFFLINE, PARAM_STATE, PARAM_STATUS
from pyimouapi.exceptions import ImouException
from pyimouapi.ha_device import DeviceStatus, ImouHaDevice
from pyimouapi.local_record import (
    day_time_window,
    local_record_display_title,
    parse_local_bitmap_days,
)
from pyimouapi.playback.hls import (
    RECORD_TYPE_CLOUD,
    RECORD_TYPE_LOCAL,
    is_hls_playback_url,
)

from .alarm_saved_clips import (
    alarm_clip_folder,
    clips_on_day,
    days_with_clips,
    resolve_clip_path,
)
from .const import DOMAIN, imou_life_device_key_from_ids
from .record_cover import (
    async_register_record_covers,
    record_cover_thumbnail_url,
)
from .record_hls_proxy import (
    RecordHlsDescriptor,
    async_create_record_hls_proxy_playlist_url,
    async_prepare_local_record_playback,
    async_register_record_hls_proxy,
    record_hls_proxy_origin,
)
from .record_playback_mp4 import (
    RECORD_REMUX_TIMEOUT_SECONDS,
    async_register_record_playback_mp4,
    record_playback_cache_id,
    register_playback_source,
    signed_record_playback_url,
    signed_record_stream_url,
)
from .repairs import (
    RecordPlaybackEmptySourceError,
    async_create_record_playback_issue,
    async_delete_record_playback_issue,
)
from .runtime_data import get_runtime_data
from .saved_record_http import async_register_saved_record_view, signed_saved_record_url

_LOGGER = logging.getLogger(__name__)

MEDIA_SOURCE_NAME = "Imou Life"
MIME_MP4 = "video/mp4"

KIND_CLOUD = "cloud"
KIND_LOCAL = "local"
KIND_ALARM = "alarm"
PREFIX_PLAY = "play"
# Cloud URLs carry short-lived tokens; only reuse the one minted while resolving
# when the player asks for the clip right away.
SOURCE_REUSE_SECONDS = 60.0
SOURCE_EXPIRY_MARGIN_SECONDS = 5.0
LOCAL_LIVE_DEADLINE_GRACE_SECONDS = 120.0
LOCAL_LIVE_TIMEOUT_MIN_SECONDS = 300.0
# A localvod ticket lists no segments until the device starts producing them.
# Wait briefly before opening the proxy; the stream view gives up on the first
# bytes after 45 s, so stay under that.
LOCAL_SEGMENT_WAIT_SECONDS = 30.0


async def async_get_media_source(hass: HomeAssistant) -> ImouRecordMediaSource:
    """Register Imou media source."""
    async_register_saved_record_view(hass)
    async_register_record_hls_proxy(hass)
    async_register_record_playback_mp4(hass)
    async_register_record_covers(hass)
    return ImouRecordMediaSource(hass)


def _local_stream_ttl_seconds(begin_time: str, end_time: str) -> float:
    """Lifetime for a local playback session; Imou produces it in real time."""
    try:
        begin = datetime.strptime(begin_time, "%Y-%m-%d %H:%M:%S")
        end = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return 4 * 3600.0
    return min(max(1800.0, (end - begin).total_seconds() + 1800.0), 24 * 3600.0)


def _local_live_timeout_seconds(begin_time: str, end_time: str) -> float:
    """Bound real-time production using the clip duration, not its wall-clock end."""
    try:
        begin = datetime.strptime(begin_time, "%Y-%m-%d %H:%M:%S")
        end = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return 4 * 3600.0
    duration = max(0.0, (end - begin).total_seconds())
    return max(
        LOCAL_LIVE_TIMEOUT_MIN_SECONDS,
        duration + LOCAL_LIVE_DEADLINE_GRACE_SECONDS,
    )


def _record_hls_expiry(data: object) -> float | None:
    """Extract an absolute URL expiry from public playback response metadata."""
    if not isinstance(data, dict):
        return None
    now = time.time()
    for key in ("expiresAt", "expireAt", "expiry", "expireTime"):
        value = data.get(key)
        if value is None:
            continue
        try:
            expiry = float(value)
        except (TypeError, ValueError):
            continue
        if expiry >= 1_000_000_000:
            return max(now, expiry)
        return now + max(0, expiry)
    return None


def _saved_clip_folder(hass: HomeAssistant) -> Path:
    return alarm_clip_folder(hass)


def _split(identifier: str | None) -> list[str]:
    if not identifier:
        return []
    return [part for part in identifier.split("|") if part]


def _encode_file_token(path: str) -> str:
    return base64.urlsafe_b64encode(path.encode()).decode().rstrip("=")


def _decode_file_token(token: str) -> str:
    pad = "=" * (-len(token) % 4)
    return base64.urlsafe_b64decode(token + pad).decode()


def _record_capability_ids(device: ImouHaDevice) -> set[str]:
    """Page ids, parent device ids, and the capability list, together."""
    ids = set(device.capability_ids or ())
    ids.update(device.device_capability_ids)
    if device.capability_set is not None:
        ids.update(device.capability_set.services)
    return ids


def _supports_local_records(device: ImouHaDevice | None) -> bool:
    """Return whether a device can list local recording clips."""
    if device is None:
        return False
    return "record.file.list" in _record_capability_ids(device)


def _has_record_bitmap(device: ImouHaDevice) -> bool:
    """Return whether this camera can ask which days have recordings."""
    return "record.file.bitmap" in _record_capability_ids(device)


def _days_without_bitmap(year: int, month: int, today: datetime) -> list[int]:
    """Every day of the month, through today when the month is current."""
    last = calendar.monthrange(year, month)[1]
    if (year, month) > (today.year, today.month):
        return []
    if (year, month) == (today.year, today.month):
        last = min(last, today.day)
    return list(range(1, last + 1))


class ImouRecordMediaSource(MediaSource):
    """Browse Imou cloud and SD recordings."""

    name: str = MEDIA_SOURCE_NAME

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(DOMAIN)
        self.hass = hass

    @override
    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        parts = _split(item.identifier)
        if not parts or parts[0] != PREFIX_PLAY:
            raise Unresolvable(
                translation_domain=DOMAIN,
                translation_key="record_media_unresolved",
            )
        if len(parts) < 6:
            raise Unresolvable(
                translation_domain=DOMAIN,
                translation_key="record_media_unresolved",
            )
        kind = parts[1]
        entry_id = parts[2]
        device_id = parts[3]
        channel_id = parts[4]
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.state is not ConfigEntryState.LOADED:
            raise Unresolvable(
                translation_domain=DOMAIN,
                translation_key="record_media_not_loaded",
            )
        if kind == KIND_ALARM:
            alarm_file_name = parts[5]
            folder = _saved_clip_folder(self.hass)
            try:
                clip_path = resolve_clip_path(
                    folder, device_id, channel_id, alarm_file_name
                )
            except ValueError as err:
                raise Unresolvable(
                    translation_domain=DOMAIN,
                    translation_key="record_media_unresolved",
                ) from err
            if not clip_path.is_file() or not self.hass.config.is_allowed_path(
                str(clip_path)
            ):
                raise Unresolvable(
                    translation_domain=DOMAIN,
                    translation_key="record_media_unresolved",
                )
            return PlayMedia(
                signed_saved_record_url(self.hass, entry_id, alarm_file_name),
                MIME_MP4,
            )
        if len(parts) not in (7, 8):
            raise Unresolvable(
                translation_domain=DOMAIN,
                translation_key="record_media_unresolved",
            )
        begin_time = parts[5]
        end_time = parts[6]
        file_name = _decode_file_token(parts[7]) if len(parts) == 8 else None
        runtime = get_runtime_data(entry)
        if runtime is None:
            raise Unresolvable(
                translation_domain=DOMAIN,
                translation_key="record_media_not_loaded",
            )
        record_type = RECORD_TYPE_CLOUD if kind == KIND_CLOUD else RECORD_TYPE_LOCAL
        manager = runtime.coordinator.device_manager.delegate

        async def async_record_hls_descriptor() -> RecordHlsDescriptor:
            try:
                data = await manager.async_create_device_record_hls(
                    device_id,
                    channel_id,
                    begin_time=begin_time,
                    end_time=end_time,
                    record_type=record_type,
                    file_name=file_name if kind == KIND_LOCAL else None,
                )
            except ImouException as err:
                async_create_record_playback_issue(self.hass, entry_id, err)
                raise
            source_url = data.get("url") if isinstance(data, dict) else None
            if not source_url or not is_hls_playback_url(str(source_url)):
                empty_source_error = RecordPlaybackEmptySourceError()
                async_create_record_playback_issue(
                    self.hass, entry_id, empty_source_error
                )
                raise empty_source_error
            return RecordHlsDescriptor(
                str(source_url),
                _record_hls_expiry(data),
            )

        cache_id = record_playback_cache_id(item.identifier or "")
        # A local clip cannot be fetched up front: Imou generates its segments in
        # real time, so the playback session has to outlive the clip.
        stream_ttl_seconds = (
            _local_stream_ttl_seconds(begin_time, end_time)
            if kind == KIND_LOCAL
            else RECORD_REMUX_TIMEOUT_SECONDS
        )
        live_timeout_seconds = (
            _local_live_timeout_seconds(begin_time, end_time)
            if kind == KIND_LOCAL
            else RECORD_REMUX_TIMEOUT_SECONDS
        )
        pending_source: RecordHlsDescriptor | None = None
        source_minted_at = 0.0

        async def async_mp4_source() -> RecordHlsDescriptor:
            # Avoid minting the same short-lived URL twice while resolving; remux
            # retries and later plays request a fresh one instead.
            nonlocal pending_source
            source = pending_source
            pending_source = None
            now = time.time()
            expired = (
                source is not None
                and source.expires_at is not None
                and (source.expires_at <= now + SOURCE_EXPIRY_MARGIN_SECONDS)
            )
            if (
                source is None
                or now - source_minted_at > SOURCE_REUSE_SECONDS
                or expired
            ):
                source = await async_record_hls_descriptor()
            prepared = (
                await async_prepare_local_record_playback(
                    self.hass,
                    source.playlist_url,
                    max_wait=LOCAL_SEGMENT_WAIT_SECONDS,
                )
                if kind == KIND_LOCAL
                else None
            )
            signed_playlist = await async_create_record_hls_proxy_playlist_url(
                self.hass,
                source.playlist_url,
                refresh=async_record_hls_descriptor,
                entry_id=entry_id,
                expires_at=source.expires_at,
                prepared_playback=prepared,
                stream_ttl_seconds=stream_ttl_seconds,
            )
            proxy_origin = record_hls_proxy_origin(self.hass, fallback_url=get_url)
            return RecordHlsDescriptor(
                f"{proxy_origin}{signed_playlist}",
                source.expires_at,
                proxy_origin,
            )

        if kind == KIND_LOCAL:
            # A local clip is finite, but Imou only produces it while it streams
            # and Stream restarts ended sources. Mux it into a fragmented MP4
            # while the player reads it: playback can be stopped and ends at
            # ENDLIST, and the finished file becomes a seekable cache entry.
            register_playback_source(
                self.hass,
                cache_id,
                entry_id,
                async_mp4_source,
                live_timeout_seconds=live_timeout_seconds,
            )
            _LOGGER.debug(
                "Imou local recording playback %s – %s (live MP4)",
                begin_time,
                end_time,
            )
            async_delete_record_playback_issue(self.hass, entry_id)
            return PlayMedia(signed_record_stream_url(self.hass, cache_id), MIME_MP4)

        try:
            descriptor = await async_record_hls_descriptor()
        except ImouException as err:
            raise Unresolvable(
                translation_domain=DOMAIN,
                translation_key="record_playback_failed",
            ) from err
        except RecordPlaybackEmptySourceError as err:
            raise Unresolvable(
                translation_domain=DOMAIN,
                translation_key="record_playback_failed",
            ) from err
        if entry.state is not ConfigEntryState.LOADED:
            raise Unresolvable(
                translation_domain=DOMAIN,
                translation_key="record_media_not_loaded",
            )
        pending_source = descriptor
        source_minted_at = time.time()
        # Stream cannot reliably stop or finish finite VOD. Send fragmented
        # MP4 while the clip downloads, then cache the finalized seekable file.
        register_playback_source(
            self.hass,
            cache_id,
            entry_id,
            async_mp4_source,
            live_timeout_seconds=live_timeout_seconds,
        )
        _LOGGER.debug(
            "Imou recording playback %s – %s (progressive MP4)", begin_time, end_time
        )
        async_delete_record_playback_issue(self.hass, entry_id)
        return PlayMedia(signed_record_playback_url(self.hass, cache_id), MIME_MP4)

    @override
    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        parts = _split(item.identifier)
        if not parts:
            return await self._browse_accounts()
        if parts[0] == PREFIX_PLAY:
            raise BrowseError("Playback items are not browsable")
        if len(parts) == 1:
            return await self._browse_cameras(parts)
        if len(parts) == 2:
            return await self._browse_kinds(parts)
        if len(parts) == 3:
            return await self._browse_months(parts)
        if len(parts) == 4 and parts[-1].startswith("month:"):
            return await self._browse_days(parts)
        if len(parts) == 5 and parts[-1].startswith("day:"):
            return await self._browse_clips(parts)
        raise BrowseError(f"Unknown Imou media identifier: {item.identifier}")

    async def _browse_accounts(self) -> BrowseMediaSource:
        children: list[BrowseMediaSource] = []
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.state is not ConfigEntryState.LOADED:
                continue
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=f"entry:{entry.entry_id}",
                    media_class=MediaClass.DIRECTORY,
                    media_content_type=MediaType.VIDEO,
                    title=entry.title or DOMAIN,
                    can_play=False,
                    can_expand=True,
                )
            )
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=None,
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title=MEDIA_SOURCE_NAME,
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _browse_cameras(self, parts: list[str]) -> BrowseMediaSource:
        entry_id = parts[0].removeprefix("entry:")
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            raise BrowseError("Imou account not found")
        registry = er.async_get(self.hass)
        device_registry = dr.async_get(self.hass)
        children: list[BrowseMediaSource] = []
        for entity in er.async_entries_for_config_entry(registry, entry_id):
            if entity.domain != "camera" or entity.platform != DOMAIN:
                continue
            if entity.disabled or entity.hidden:
                continue
            title = entity.name
            if not title and entity.device_id:
                device = device_registry.async_get(entity.device_id)
                if device is not None:
                    title = device.name_by_user or device.name
            if not title:
                title = entity.entity_id
            device_id, channel_id = self._camera_ids_from_entity(entity)
            if device_id is None:
                continue
            cam_key = f"cam:{device_id}:{channel_id}"
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier="|".join([*parts, cam_key]),
                    media_class=MediaClass.DIRECTORY,
                    media_content_type=MediaType.VIDEO,
                    title=title,
                    can_play=False,
                    can_expand=True,
                )
            )
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier="|".join(parts),
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title=entry.title or DOMAIN,
            can_play=False,
            can_expand=True,
            children=sorted(children, key=lambda child: child.title or ""),
        )

    @staticmethod
    def _camera_ids_from_entity(entity: er.RegistryEntry) -> tuple[str | None, str]:
        unique = entity.unique_id or ""
        base = unique.rsplit("$", 1)[0] if "$" in unique else unique
        if "_" not in base:
            return None, "0"
        device_id, channel = base.rsplit("_", 1)
        return device_id, channel

    async def _browse_kinds(self, parts: list[str]) -> BrowseMediaSource:
        cam_part = next(p for p in parts if p.startswith("cam:"))
        _, device_id, channel_id = cam_part.split(":", 2)
        entry_part = next(p for p in parts if p.startswith("entry:"))
        entry_id = entry_part.removeprefix("entry:")
        title = f"{device_id} · ch{channel_id}"
        children = [
            BrowseMediaSource(
                domain=DOMAIN,
                identifier="|".join([*parts, KIND_CLOUD]),
                media_class=MediaClass.DIRECTORY,
                media_content_type=MediaType.VIDEO,
                title=await _cloud_folder_title(self.hass),
                can_play=False,
                can_expand=True,
            ),
        ]
        entry = self.hass.config_entries.async_get_entry(entry_id)
        runtime = get_runtime_data(entry) if entry is not None else None
        device_key = imou_life_device_key_from_ids(device_id, channel_id, None)
        device = (
            runtime.coordinator.get_device(device_key)
            if runtime is not None and device_key is not None
            else None
        )
        if _supports_local_records(device):
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier="|".join([*parts, KIND_LOCAL]),
                    media_class=MediaClass.DIRECTORY,
                    media_content_type=MediaType.VIDEO,
                    title=await _local_folder_title(self.hass),
                    can_play=False,
                    can_expand=True,
                )
            )
        children.append(
            BrowseMediaSource(
                domain=DOMAIN,
                identifier="|".join([*parts, KIND_ALARM]),
                media_class=MediaClass.DIRECTORY,
                media_content_type=MediaType.VIDEO,
                title=await _alarm_folder_title(self.hass),
                can_play=False,
                can_expand=True,
            )
        )
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier="|".join(parts),
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title=title,
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _browse_months(self, parts: list[str]) -> BrowseMediaSource:
        kind = parts[-1]
        now = dt_util.now()
        children: list[BrowseMediaSource] = []
        year, month = now.year, now.month
        for _ in range(6):
            label = f"{year:04d}-{month:02d}"
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier="|".join([*parts, f"month:{label}"]),
                    media_class=MediaClass.DIRECTORY,
                    media_content_type=MediaType.VIDEO,
                    title=label,
                    can_play=False,
                    can_expand=True,
                )
            )
            month -= 1
            if month < 1:
                month = 12
                year -= 1
        if kind == KIND_CLOUD:
            folder = await _cloud_folder_title(self.hass)
        elif kind == KIND_ALARM:
            folder = await _alarm_folder_title(self.hass)
        else:
            folder = await _local_folder_title(self.hass)
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier="|".join(parts),
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title=folder,
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _browse_days(self, parts: list[str]) -> BrowseMediaSource:
        kind = parts[2]
        month_part = parts[-1]
        month_label = month_part.removeprefix("month:")
        year_s, month_s = month_label.split("-", 1)
        year, month = int(year_s), int(month_s)
        cam_part = next(p for p in parts if p.startswith("cam:"))
        _, device_id, channel_id = cam_part.split(":", 2)
        entry_part = next(p for p in parts if p.startswith("entry:"))
        entry_id = entry_part.removeprefix("entry:")
        runtime = self._runtime(entry_id)
        manager = runtime.coordinator.device_manager.delegate
        days: list[int] = []
        try:
            if kind == KIND_ALARM:
                folder = _saved_clip_folder(self.hass)
                days = await self.hass.async_add_executor_job(
                    days_with_clips,
                    folder,
                    device_id,
                    channel_id,
                    year,
                    month,
                )
            elif kind == KIND_CLOUD:
                data = await manager.async_query_cloud_record_bitmap(
                    device_id, channel_id, year=year, month=month
                )
                bitmap = str(data.get("bitmap") or "")
                days = parse_cloud_record_bitmap_days(bitmap, year=year, month=month)
            else:
                camera = self._camera(runtime, device_id, channel_id)
                if camera is not None and not _has_record_bitmap(camera):
                    days = _days_without_bitmap(year, month, dt_util.now())
                else:
                    try:
                        data = await manager.async_local_record_bitmap(
                            device_id,
                            channel_id,
                            year=year,
                            month=month,
                            product_id=self._camera_product_id(
                                runtime, device_id, channel_id
                            ),
                        )
                    except ImouException as err:
                        offline = await self._offline_browse_error(
                            runtime, device_id, channel_id, err
                        )
                        if offline is not None:
                            raise offline from err
                        _LOGGER.debug(
                            "Record bitmap unavailable for %s ch%s (%s)",
                            device_id,
                            channel_id,
                            kind,
                            exc_info=True,
                        )
                    else:
                        days = parse_local_bitmap_days(data, year=year, month=month)
        except ImouException:
            _LOGGER.debug(
                "Record bitmap unavailable for %s ch%s (%s)",
                device_id,
                channel_id,
                kind,
                exc_info=True,
            )
        children = [
            BrowseMediaSource(
                domain=DOMAIN,
                identifier="|".join([*parts, f"day:{day:02d}"]),
                media_class=MediaClass.DIRECTORY,
                media_content_type=MediaType.VIDEO,
                title=f"{month_label}-{day:02d}",
                can_play=False,
                can_expand=True,
            )
            for day in days
        ]
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier="|".join(parts),
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title=month_label,
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _browse_clips(self, parts: list[str]) -> BrowseMediaSource:
        kind = parts[2]
        day_part = parts[-1]
        day = int(day_part.removeprefix("day:"))
        month_part = parts[-2]
        month_label = month_part.removeprefix("month:")
        year_s, month_s = month_label.split("-", 1)
        year, month = int(year_s), int(month_s)
        cam_part = next(p for p in parts if p.startswith("cam:"))
        _, device_id, channel_id = cam_part.split(":", 2)
        entry_part = next(p for p in parts if p.startswith("entry:"))
        entry_id = entry_part.removeprefix("entry:")
        begin_time, end_time = day_time_window(year, month, day)
        runtime = self._runtime(entry_id)
        manager = runtime.coordinator.device_manager.delegate
        children: list[BrowseMediaSource] = []
        try:
            if kind == KIND_ALARM:
                folder = _saved_clip_folder(self.hass)
                day_clips = await self.hass.async_add_executor_job(
                    clips_on_day,
                    folder,
                    device_id,
                    channel_id,
                    year,
                    month,
                    day,
                )
                for clip in day_clips:
                    play_id = "|".join(
                        [
                            PREFIX_PLAY,
                            kind,
                            entry_id,
                            device_id,
                            channel_id,
                            clip.file_name,
                        ]
                    )
                    title = clip.recorded_at.strftime("%H:%M:%S")
                    children.append(
                        BrowseMediaSource(
                            domain=DOMAIN,
                            identifier=play_id,
                            media_class=MediaClass.VIDEO,
                            media_content_type=MediaType.VIDEO,
                            title=title,
                            can_play=True,
                            can_expand=False,
                        )
                    )
            elif kind == KIND_CLOUD:
                segments = await manager.async_list_cloud_record_segments(
                    device_id,
                    channel_id,
                    begin_time=begin_time,
                    end_time=end_time,
                )
                for segment in segments:
                    play_id = "|".join(
                        [
                            PREFIX_PLAY,
                            kind,
                            entry_id,
                            device_id,
                            channel_id,
                            segment.begin_time,
                            segment.end_time,
                        ]
                    )
                    title = f"{segment.begin_time} - {segment.end_time}"
                    children.append(
                        BrowseMediaSource(
                            domain=DOMAIN,
                            identifier=play_id,
                            media_class=MediaClass.VIDEO,
                            media_content_type=MediaType.VIDEO,
                            title=title,
                            can_play=True,
                            can_expand=False,
                            thumbnail=self._cover_thumbnail(
                                entry_id,
                                device_id,
                                channel_id,
                                segment.thumb_url,
                            ),
                        )
                    )
            else:
                segments = await manager.async_list_local_record_segments(
                    device_id,
                    channel_id,
                    begin_time=begin_time,
                    end_time=end_time,
                    product_id=self._camera_product_id(runtime, device_id, channel_id),
                )
                for segment in segments:
                    play_parts = [
                        PREFIX_PLAY,
                        kind,
                        entry_id,
                        device_id,
                        channel_id,
                        segment.begin_time,
                        segment.end_time,
                    ]
                    if segment.file_name:
                        play_parts.append(_encode_file_token(segment.file_name))
                    play_id = "|".join(play_parts)
                    title = local_record_display_title(
                        segment.file_name,
                        segment.begin_time,
                        segment.end_time,
                    )
                    children.append(
                        BrowseMediaSource(
                            domain=DOMAIN,
                            identifier=play_id,
                            media_class=MediaClass.VIDEO,
                            media_content_type=MediaType.VIDEO,
                            title=title,
                            can_play=True,
                            can_expand=False,
                            thumbnail=self._cover_thumbnail(
                                entry_id,
                                device_id,
                                channel_id,
                                segment.thumb_url,
                            ),
                        )
                    )
        except ImouException as err:
            if kind == KIND_LOCAL:
                offline = await self._offline_browse_error(
                    runtime, device_id, channel_id, err
                )
                if offline is not None:
                    raise offline from err
            _LOGGER.debug(
                "Record list failed for %s ch%s on %s",
                device_id,
                channel_id,
                begin_time,
                exc_info=True,
            )
        day_title = f"{month_label}-{day:02d}"
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier="|".join(parts),
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.VIDEO,
            title=day_title,
            can_play=False,
            can_expand=bool(children),
            children=children,
        )

    def _cover_thumbnail(
        self, entry_id: str, device_id: str, channel_id: str, url: str | None
    ) -> str | None:
        if not url:
            return None
        return record_cover_thumbnail_url(
            self.hass,
            entry_id=entry_id,
            device_id=device_id,
            channel_id=channel_id,
            url=url,
        )

    def _camera(self, runtime, device_id: str, channel_id: str) -> ImouHaDevice | None:
        device_key = imou_life_device_key_from_ids(device_id, channel_id, None)
        if device_key is None:
            return None
        return runtime.coordinator.get_device(device_key)

    async def _offline_browse_error(
        self, runtime, device_id: str, channel_id: str, err: ImouException
    ) -> BrowseError | None:
        """Local recordings need the device. An offline device is not an empty day."""
        if not _local_failure_is_offline(
            err, self._camera(runtime, device_id, channel_id)
        ):
            return None
        return BrowseError(
            await _translation(
                self.hass, "record_media_device_offline", "Device is offline"
            )
        )

    @staticmethod
    def _camera_product_id(runtime, device_id: str, channel_id: str) -> str | None:
        device_key = imou_life_device_key_from_ids(device_id, channel_id, None)
        if device_key is None:
            return None
        device = runtime.coordinator.get_device(device_key)
        if device is None:
            return None
        return device.product_id

    def _runtime(self, entry_id: str):
        entry = self.hass.config_entries.async_get_entry(entry_id)
        runtime = get_runtime_data(entry) if entry else None
        if runtime is None:
            raise BrowseError("Imou integration is not loaded")
        return runtime


def _local_failure_is_offline(err: ImouException, device: ImouHaDevice | None) -> bool:
    """True when a local-recording query failed because the device is offline."""
    if (err.api_code or "").upper() == ERROR_CODE_DEVICE_OFFLINE:
        return True
    message = (err.message or "").lower()
    if "dv1007" in message or "offline" in message or "离线" in message:
        return True
    if device is None:
        return False
    status = device.sensors.get(PARAM_STATUS)
    if not isinstance(status, dict):
        return False
    return status.get(PARAM_STATE) == DeviceStatus.OFFLINE.value


async def _cloud_folder_title(hass: HomeAssistant) -> str:
    return await _translation(hass, "record_media_cloud_folder", "Cloud recordings")


async def _local_folder_title(hass: HomeAssistant) -> str:
    return await _translation(hass, "record_media_local_folder", "Local recordings")


async def _alarm_folder_title(hass: HomeAssistant) -> str:
    return await _translation(hass, "record_media_alarm_folder", "Record on alarm")


async def _translation(hass: HomeAssistant, key: str, default: str) -> str:
    from homeassistant.helpers import translation

    translations = await translation.async_get_translations(
        hass, hass.config.language, "media_source", {DOMAIN}
    )
    return translations.get(f"component.{DOMAIN}.media_source.{key}", default)
