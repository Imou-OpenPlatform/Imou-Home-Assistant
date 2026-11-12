"""Imou cloud and local recordings in the Media Browser."""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import override

from homeassistant.components.camera import DynamicStreamSettings
from homeassistant.components.media_player import BrowseError, MediaClass, MediaType
from homeassistant.components.stream import create_stream
from homeassistant.components.stream.const import HLS_PROVIDER
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.network import NoURLAvailableError, get_url
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
from homeassistant.util import dt as dt_util
from pyimouapi.cloud_record import (
    parse_cloud_record_bitmap_days,
    parse_cloud_records_data,
)
from pyimouapi.exceptions import ImouException
from pyimouapi.local_record import (
    day_time_window,
    local_record_display_title,
    parse_local_bitmap_days,
    parse_local_record_list,
)
from pyimouapi.playback.hls import is_hls_playback_url
from pyimouapi.playback.hls import RECORD_TYPE_CLOUD, RECORD_TYPE_LOCAL

from .alarm_saved_clips import clips_on_day, days_with_clips, resolve_clip_path
from .const import DOMAIN, PARAM_LOCAL_RECORD_PATH
from .runtime_data import get_runtime_data
from .record_hls_proxy import (
    async_create_record_hls_proxy_playlist_url,
    async_register_record_hls_proxy,
)
from .record_playback_mp4 import (
    async_register_record_playback_mp4,
    record_playback_cache_id,
    register_playback_source,
    signed_record_playback_url,
)
from .saved_record_http import async_register_saved_record_view, signed_saved_record_url

_LOGGER = logging.getLogger(__name__)

MEDIA_SOURCE_NAME = "Imou Life"
MIME_MP4 = "video/mp4"
# Media browser web player only treats application/x-mpegURL as HLS (not vnd.apple.mpegurl).
MIME_HLS = "application/x-mpegURL"

KIND_CLOUD = "cloud"
KIND_LOCAL = "local"
KIND_ALARM = "alarm"
PREFIX_PLAY = "play"


async def async_get_media_source(hass: HomeAssistant) -> ImouRecordMediaSource:
    """Register Imou media source."""
    async_register_saved_record_view(hass)
    async_register_record_hls_proxy(hass)
    async_register_record_playback_mp4(hass)
    return ImouRecordMediaSource(hass)


async def _async_try_hls_playback(
    hass: HomeAssistant, upstream_playlist_url: str, stream_label: str
) -> PlayMedia | None:
    """Return Stream HLS PlayMedia when the Stream integration can remux the clip."""
    if "stream" not in hass.config.components:
        return None
    try:
        signed_playlist = await async_create_record_hls_proxy_playlist_url(
            hass, upstream_playlist_url
        )
        playlist_source = f"{get_url(hass).rstrip('/')}{signed_playlist}"
        stream = create_stream(
            hass,
            playlist_source,
            {},
            DynamicStreamSettings(),
            stream_label=stream_label,
        )
        stream.add_provider(HLS_PROVIDER, timeout=3600)
        stream_url = stream.endpoint_url(HLS_PROVIDER).replace("master_", "")
        return PlayMedia(stream_url, MIME_HLS)
    except (
        HomeAssistantError,
        NoURLAvailableError,
        OSError,
        TimeoutError,
        ValueError,
    ) as err:
        _LOGGER.debug(
            "Imou recording HLS via Stream failed (%s), will try MP4", err
        )
        return None


def _saved_clip_folder(hass: HomeAssistant, entry_id: str) -> Path | None:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None:
        return None
    folder_raw = str(entry.options.get(PARAM_LOCAL_RECORD_PATH) or "").strip()
    if not folder_raw or not hass.config.is_allowed_path(folder_raw):
        return None
    return Path(folder_raw)


def _split(identifier: str | None) -> list[str]:
    if not identifier:
        return []
    return [part for part in identifier.split("|") if part]


def _encode_file_token(path: str) -> str:
    return base64.urlsafe_b64encode(path.encode()).decode().rstrip("=")


def _decode_file_token(token: str) -> str:
    pad = "=" * (-len(token) % 4)
    return base64.urlsafe_b64decode(token + pad).decode()


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
            file_name = parts[5]
            folder = _saved_clip_folder(self.hass, entry_id)
            if folder is None:
                raise Unresolvable(
                    translation_domain=DOMAIN,
                    translation_key="record_media_unresolved",
                )
            try:
                clip_path = resolve_clip_path(folder, device_id, channel_id, file_name)
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
                signed_saved_record_url(self.hass, entry_id, file_name),
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
            raise Unresolvable(
                translation_domain=DOMAIN,
                translation_key="record_playback_failed",
                translation_placeholders={"error": err.message or str(err)},
            ) from err
        source_url = data.get("url") if isinstance(data, dict) else None
        if not source_url or not is_hls_playback_url(str(source_url)):
            raise Unresolvable(
                translation_domain=DOMAIN,
                translation_key="record_playback_failed",
                translation_placeholders={"error": "No playback URL from Imou"},
            )
        clip_label = f"{device_id} {begin_time}–{end_time}"
        hls_play = await _async_try_hls_playback(self.hass, str(source_url), clip_label)
        if hls_play is not None:
            _LOGGER.debug(
                "Imou recording playback %s – %s (HLS via Stream)", begin_time, end_time
            )
            return hls_play
        cache_id = record_playback_cache_id(item.identifier or "")
        register_playback_source(self.hass, cache_id, str(source_url))
        _LOGGER.info(
            "Imou recording %s: HLS unavailable, using MP4 fallback", clip_label
        )
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
            BrowseMediaSource(
                domain=DOMAIN,
                identifier="|".join([*parts, KIND_LOCAL]),
                media_class=MediaClass.DIRECTORY,
                media_content_type=MediaType.VIDEO,
                title=await _local_folder_title(self.hass),
                can_play=False,
                can_expand=True,
            ),
        ]
        if _saved_clip_folder(self.hass, entry_id) is not None:
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
                folder = _saved_clip_folder(self.hass, entry_id)
                if folder is not None:
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
                data = await manager.async_local_record_bitmap(
                    device_id, channel_id, year=year, month=month
                )
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
                folder = _saved_clip_folder(self.hass, entry_id)
                if folder is not None:
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
                data = await manager.async_query_cloud_records(
                    device_id,
                    channel_id,
                    begin_time=begin_time,
                    end_time=end_time,
                )
                for segment in parse_cloud_records_data(data):
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
                            thumbnail=segment.thumb_url,
                        )
                    )
            else:
                data = await manager.async_local_record_list(
                    device_id,
                    channel_id,
                    begin_time=begin_time,
                    end_time=end_time,
                )
                for segment in parse_local_record_list(data):
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
                        )
                    )
        except ImouException:
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

    def _runtime(self, entry_id: str):
        entry = self.hass.config_entries.async_get_entry(entry_id)
        runtime = get_runtime_data(entry) if entry else None
        if runtime is None:
            raise BrowseError("Imou integration is not loaded")
        return runtime


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
