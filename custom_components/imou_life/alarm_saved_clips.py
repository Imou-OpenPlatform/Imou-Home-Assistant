"""List and validate post-alarm clips saved under the integration folder."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

CLIP_FILE_PATTERN = re.compile(
    r"^(?P<device>[^/\\]+)_(?P<channel>[^_]+)_(?P<stamp>\d{8}_\d{6})\.mp4$"
)


@dataclass(frozen=True, slots=True)
class SavedAlarmClip:
    """One alarm-triggered mp4 on disk."""

    file_name: str
    device_id: str
    channel_id: str
    recorded_at: datetime


def clip_name_prefix(device_id: str, channel_id: str) -> str:
    """Filename prefix used when camera.record saves a clip."""
    return f"{device_id}_{channel_id}_"


def _parse_clip(path: Path) -> SavedAlarmClip | None:
    match = CLIP_FILE_PATTERN.match(path.name)
    if match is None:
        return None
    try:
        recorded_at = datetime.strptime(match.group("stamp"), "%Y%m%d_%H%M%S")
    except ValueError:
        return None
    return SavedAlarmClip(
        file_name=path.name,
        device_id=match.group("device"),
        channel_id=match.group("channel"),
        recorded_at=recorded_at,
    )


def list_saved_clips(folder: Path, device_id: str, channel_id: str) -> list[SavedAlarmClip]:
    """Return clips for this device channel in the save folder (newest first)."""
    if not folder.is_dir():
        return []
    prefix = clip_name_prefix(device_id, channel_id)
    clips: list[SavedAlarmClip] = []
    for path in folder.iterdir():
        if not path.is_file() or not path.name.startswith(prefix):
            continue
        clip = _parse_clip(path)
        if clip is not None:
            clips.append(clip)
    clips.sort(key=lambda clip: clip.recorded_at, reverse=True)
    return clips


def days_with_clips(
    folder: Path, device_id: str, channel_id: str, year: int, month: int
) -> list[int]:
    """Days in a month that have at least one saved alarm clip."""
    days = {
        clip.recorded_at.day
        for clip in list_saved_clips(folder, device_id, channel_id)
        if clip.recorded_at.year == year and clip.recorded_at.month == month
    }
    return sorted(days)


def clips_on_day(
    folder: Path,
    device_id: str,
    channel_id: str,
    year: int,
    month: int,
    day: int,
) -> list[SavedAlarmClip]:
    """Clips recorded on a calendar day."""
    return [
        clip
        for clip in list_saved_clips(folder, device_id, channel_id)
        if clip.recorded_at.year == year
        and clip.recorded_at.month == month
        and clip.recorded_at.day == day
    ]


def resolve_clip_path(folder: Path, device_id: str, channel_id: str, file_name: str) -> Path:
    """Resolve a basename to a path; raises ValueError when invalid."""
    if "/" in file_name or "\\" in file_name or file_name in (".", ".."):
        raise ValueError("invalid file name")
    if not file_name.startswith(clip_name_prefix(device_id, channel_id)):
        raise ValueError("clip does not belong to this camera")
    if CLIP_FILE_PATTERN.match(file_name) is None:
        raise ValueError("unexpected clip name")
    return folder / file_name
