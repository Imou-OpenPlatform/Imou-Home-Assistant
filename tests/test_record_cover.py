"""Tests for serving recording covers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from custom_components.imou_life.const import DOMAIN
from custom_components.imou_life.pic_thumbnail import _DownloadFailure
from custom_components.imou_life.record_cover import (
    _async_download_cover,
    _async_refresh_cover_url,
    _RecordCover,
)
from homeassistant.core import HomeAssistant
from pyimouapi.cloud_record import CloudRecordSegment
from pytest_homeassistant_custom_component.common import MockConfigEntry

from . import USER_INPUT

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def _cover(entry_id: str, kind: str = "cloud") -> _RecordCover:
    return _RecordCover(
        entry_id,
        "SN1",
        "0",
        "https://example.test/old.dav",
        kind=kind,
        begin_time="2026-11-23 09:59:05",
        end_time="2026-11-23 09:59:40",
    )


def _entry_with_manager(hass: HomeAssistant) -> tuple[MockConfigEntry, MagicMock]:
    entry = MockConfigEntry(domain=DOMAIN, data=USER_INPUT)
    entry.add_to_hass(hass)
    manager = MagicMock()
    coordinator = SimpleNamespace(
        device_manager=SimpleNamespace(delegate=manager),
    )
    entry.runtime_data = SimpleNamespace(coordinator=coordinator)
    return entry, manager


async def test_cover_url_is_refreshed_from_a_new_listing(
    hass: HomeAssistant,
) -> None:
    """A rejected cover URL is replaced by the clip's next signed URL."""
    entry, manager = _entry_with_manager(hass)
    cover = _cover(entry.entry_id)
    manager.async_list_cloud_record_segments = AsyncMock(
        return_value=[
            CloudRecordSegment(
                record_id="r1",
                begin_time="2026-11-23 09:59:05",
                end_time="2026-11-23 09:59:40",
                thumb_url="https://example.test/new.dav",
            )
        ]
    )

    refreshed = await _async_refresh_cover_url(hass, cover)

    assert refreshed is True
    assert cover.url == "https://example.test/new.dav"
    manager.async_list_cloud_record_segments.assert_awaited_once_with(
        "SN1",
        "0",
        begin_time="2026-11-23 09:58:05",
        end_time="2026-11-23 10:00:40",
    )


async def test_local_cover_is_not_refreshed_from_cloud_records(
    hass: HomeAssistant,
) -> None:
    """Local clips keep their own cover URL instead of a cloud match."""
    entry, manager = _entry_with_manager(hass)
    cover = _cover(entry.entry_id, kind="local")
    manager.async_list_cloud_record_segments = AsyncMock()

    assert await _async_refresh_cover_url(hass, cover) is False
    manager.async_list_cloud_record_segments.assert_not_awaited()


async def test_download_retries_with_a_fresh_url_after_a_rejection(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 401 forces a new listing, and the next download succeeds."""
    entry, manager = _entry_with_manager(hass)
    cover = _cover(entry.entry_id)
    manager.async_list_cloud_record_segments = AsyncMock(
        return_value=[
            CloudRecordSegment(
                record_id="r1",
                begin_time="2026-11-23 09:59:05",
                end_time="2026-11-23 09:59:40",
                thumb_url="https://example.test/new.dav",
            )
        ]
    )
    downloads: list[str] = []

    async def _download(_hass: HomeAssistant, url: str) -> bytes | _DownloadFailure:
        downloads.append(url)
        if url.endswith("old.dav"):
            return _DownloadFailure("HTTP 401", True)
        return b"cover"

    monkeypatch.setattr(
        "custom_components.imou_life.record_cover._async_try_download_picture",
        _download,
    )

    assert await _async_download_cover(hass, cover) == b"cover"
    assert downloads == [
        "https://example.test/old.dav",
        "https://example.test/new.dav",
    ]


async def test_download_gives_up_when_no_new_url_is_available(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unretryable failure is reported without listing the clip again."""
    entry, manager = _entry_with_manager(hass)
    cover = _cover(entry.entry_id)
    manager.async_list_cloud_record_segments = AsyncMock()

    async def _download(_hass: HomeAssistant, _url: str) -> _DownloadFailure:
        return _DownloadFailure("larger than 8 MiB", False)

    monkeypatch.setattr(
        "custom_components.imou_life.record_cover._async_try_download_picture",
        _download,
    )

    assert await _async_download_cover(hass, cover) is None
    manager.async_list_cloud_record_segments.assert_not_awaited()
