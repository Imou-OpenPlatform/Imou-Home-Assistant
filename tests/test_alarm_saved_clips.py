"""Tests for on-disk alarm clip listing."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from custom_components.imou_life.alarm_saved_clips import (
    clip_name_prefix,
    clips_on_day,
    days_with_clips,
    list_saved_clips,
    resolve_clip_path,
)


def test_list_saved_clips_filters_by_camera(tmp_path: Path) -> None:
    """Only matching device/channel mp4 files are returned."""
    device_id = "717BAAHPSF6DAD3"
    channel_id = "0"
    prefix = clip_name_prefix(device_id, channel_id)
    good = tmp_path / f"{prefix}20261112_093000.mp4"
    good.write_bytes(b"x")
    (tmp_path / f"{prefix}badstamp.mp4").write_bytes(b"x")
    (tmp_path / "OTHER_0_20261112_093000.mp4").write_bytes(b"x")
    clips = list_saved_clips(tmp_path, device_id, channel_id)
    assert len(clips) == 1
    assert clips[0].file_name == good.name
    assert clips[0].recorded_at == datetime(2026, 11, 12, 9, 30, 0)


def test_days_and_clips_on_day(tmp_path: Path) -> None:
    device_id = "SN1"
    channel_id = "x"
    (tmp_path / f"{device_id}_{channel_id}_20261110_120000.mp4").write_bytes(b"1")
    (tmp_path / f"{device_id}_{channel_id}_20261112_080000.mp4").write_bytes(b"2")
    days = days_with_clips(tmp_path, device_id, channel_id, 2026, 11)
    assert days == [10, 12]
    day_clips = clips_on_day(tmp_path, device_id, channel_id, 2026, 11, 12)
    assert [clip.file_name for clip in day_clips] == [
        f"{device_id}_{channel_id}_20261112_080000.mp4"
    ]


def test_resolve_clip_path_rejects_traversal(tmp_path: Path) -> None:
    device_id = "SN1"
    channel_id = "0"
    name = f"{device_id}_{channel_id}_20261112_080000.mp4"
    (tmp_path / name).write_bytes(b"x")
    assert resolve_clip_path(tmp_path, device_id, channel_id, name) == tmp_path / name
    try:
        resolve_clip_path(tmp_path, device_id, channel_id, "../secret.mp4")
    except ValueError:
        return
    raise AssertionError("expected ValueError")
