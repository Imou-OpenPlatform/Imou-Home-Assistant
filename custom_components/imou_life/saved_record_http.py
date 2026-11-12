"""Authenticated HTTP access to saved alarm mp4 files."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.util import raise_if_invalid_filename

from .alarm_saved_clips import CLIP_FILE_PATTERN
from .const import DOMAIN, PARAM_LOCAL_RECORD_PATH

SAVED_RECORD_URL = "/api/imou_life/saved_record/{entry_id}/{filename}"
DATA_SAVED_RECORD_VIEW = "imou_life_saved_record_view"


def saved_record_play_path(entry_id: str, filename: str) -> str:
    """Relative Home Assistant path for signed playback URLs."""
    return SAVED_RECORD_URL.format(entry_id=entry_id, filename=filename)


def async_register_saved_record_view(hass: HomeAssistant) -> None:
    """Register the saved-record view once."""
    if hass.data.get(DATA_SAVED_RECORD_VIEW):
        return
    hass.http.register_view(ImouSavedRecordView())
    hass.data[DATA_SAVED_RECORD_VIEW] = True


def signed_saved_record_url(hass: HomeAssistant, entry_id: str, filename: str) -> str:
    """Return a short-lived authenticated URL for the media player."""
    from homeassistant.components.http.auth import async_sign_path

    return async_sign_path(
        hass,
        saved_record_play_path(entry_id, filename),
        timedelta(hours=1),
        use_content_user=True,
    )


class ImouSavedRecordView(HomeAssistantView):
    """Serve a saved alarm clip from the integration allowlisted folder."""

    requires_auth = True
    url = SAVED_RECORD_URL
    name = "api:imou_life_saved_record"

    async def get(
        self, request: web.Request, entry_id: str, filename: str
    ) -> web.StreamResponse:
        """Stream one mp4."""
        hass = request.app["hass"]
        raise_if_invalid_filename(filename)
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            raise web.HTTPNotFound()
        folder_raw = str(entry.options.get(PARAM_LOCAL_RECORD_PATH) or "").strip()
        if not folder_raw:
            raise web.HTTPNotFound()
        folder = Path(folder_raw)
        if not hass.config.is_allowed_path(str(folder)):
            raise web.HTTPForbidden()
        if CLIP_FILE_PATTERN.match(filename) is None:
            raise web.HTTPNotFound()
        path = folder / filename
        if not path.is_file() or not hass.config.is_allowed_path(str(path)):
            raise web.HTTPNotFound()
        return web.FileResponse(path)
