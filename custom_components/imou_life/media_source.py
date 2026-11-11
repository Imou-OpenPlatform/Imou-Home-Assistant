"""Imou cloud and local recordings in the Media Browser."""

from __future__ import annotations

import logging
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
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pyimouapi.cloud_record import (
    parse_cloud_record_bitmap_days,
    parse_cloud_records_data,
)
from pyimouapi.exceptions import ImouException
from pyimouapi.local_record import (
    day_time_window,
    parse_local_bitmap_days,
    parse_local_record_list,
)
from pyimouapi.playback.hls import RECORD_TYPE_CLOUD, RECORD_TYPE_LOCAL

from .const import DOMAIN
from .runtime_data import get_runtime_data

_LOGGER = logging.getLogger(__name__)

MEDIA_SOURCE_NAME = "Imou Life"
MIME_HLS = "application/vnd.apple.mpegurl"

KIND_CLOUD = "cloud"
KIND_LOCAL = "local"
PREFIX_PLAY = "play"


async def async_get_media_source(hass: HomeAssistant) -> ImouRecordMediaSource:
    """Register Imou media source."""
    return ImouRecordMediaSource(hass)


def _split(identifier: str | None) -> list[str]:
    if not identifier:
        return []
    return [part for part in identifier.split("|") if part]


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
        try:
            _, kind, entry_id, device_id, channel_id, begin_time, end_time = parts
        except ValueError as err:
            raise Unresolvable(
                translation_domain=DOMAIN,
                translation_key="record_media_unresolved",
            ) from err
        entry = self.hass.config_entries.async_get_entry(entry_id)
        runtime = get_runtime_data(entry) if entry else None
        if (
            entry is None
            or runtime is None
            or entry.state is not ConfigEntryState.LOADED
        ):
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
            )
        except ImouException as err:
            raise Unresolvable(
                translation_domain=DOMAIN,
                translation_key="record_playback_failed",
                translation_placeholders={"error": err.message or str(err)},
            ) from err
        url = data.get("url") if isinstance(data, dict) else None
        if not url:
            raise Unresolvable(
                translation_domain=DOMAIN,
                translation_key="record_playback_failed",
                translation_placeholders={"error": "No playback URL"},
            )
        return PlayMedia(str(url), MIME_HLS)

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
        children: list[BrowseMediaSource] = []
        for entity in er.async_entries_for_config_entry(registry, entry_id):
            if entity.domain != "camera" or not entity.entity_id.startswith(
                f"{DOMAIN}."
            ):
                continue
            title = entity.name or entity.entity_id
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
        title = f"{device_id} · ch{channel_id}"
        children = [
            BrowseMediaSource(
                domain=DOMAIN,
                identifier="|".join([*parts, KIND_CLOUD]),
                media_class=MediaClass.DIRECTORY,
                media_content_type=MediaType.VIDEO,
                title=_cloud_folder_title(self.hass),
                can_play=False,
                can_expand=True,
            ),
            BrowseMediaSource(
                domain=DOMAIN,
                identifier="|".join([*parts, KIND_LOCAL]),
                media_class=MediaClass.DIRECTORY,
                media_content_type=MediaType.VIDEO,
                title=_local_folder_title(self.hass),
                can_play=False,
                can_expand=True,
            ),
        ]
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
        folder = (
            _cloud_folder_title(self.hass)
            if kind == KIND_CLOUD
            else _local_folder_title(self.hass)
        )
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
            if kind == KIND_CLOUD:
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
            if kind == KIND_CLOUD:
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
                    title = segment.file_name
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


def _cloud_folder_title(hass: HomeAssistant) -> str:
    return _translation(hass, "record_media_cloud_folder", "Cloud recordings")


def _local_folder_title(hass: HomeAssistant) -> str:
    return _translation(hass, "record_media_local_folder", "Local recordings")


def _translation(hass: HomeAssistant, key: str, default: str) -> str:
    from homeassistant.helpers import translation

    translations = translation.async_get_translations(
        hass, hass.config.language, DOMAIN, {f"media_source.{key}"}
    )
    return translations.get(f"component.{DOMAIN}.media_source.{key}", default)
