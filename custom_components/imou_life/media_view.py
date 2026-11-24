"""Authenticated delivery for alarm and AI Insight media files."""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.auth import STORAGE_KEY, async_sign_path
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

URL_PREFIX = "/imou_media"
SIGNED_URL_TTL = timedelta(days=7)
_ALLOWED_SUFFIXES = frozenset({".jpg", ".jpeg", ".svg"})
_MEDIA_DIR = ("imou_life_media",)


def media_root(hass: HomeAssistant) -> Path:
    """Return the folder that holds media; it is outside unauthenticated www."""
    return Path(hass.config.path(*_MEDIA_DIR)).resolve()


def media_url_for_path(hass: HomeAssistant, path: Path) -> str | None:
    """Return the authenticated URL for a file under the media folder."""
    root = media_root(hass)
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        return None
    return f"{URL_PREFIX}/{resolved.relative_to(root).as_posix()}"


def media_file_path(hass: HomeAssistant, url: str) -> Path | None:
    """Map an ``/imou_media`` or legacy ``/local`` URL to a readable file."""
    if url.startswith(f"{URL_PREFIX}/"):
        root = media_root(hass)
        rel = url[len(URL_PREFIX) + 1 :].split("?", 1)[0]
    elif url.startswith("/local/"):
        root = Path(hass.config.path("www")).resolve()
        rel = url[len("/local/") :].split("?", 1)[0]
    else:
        return None
    candidate = Path(rel)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    path = (root / candidate).resolve()
    if not path.is_relative_to(root) or path.suffix.lower() not in _ALLOWED_SUFFIXES:
        return None
    return path if path.is_file() else None


def signed_media_path(hass: HomeAssistant, path: str) -> str:
    """Sign an authenticated media path for a phone outside the LAN."""
    refresh_token_id = hass.data.get(STORAGE_KEY)
    if not isinstance(refresh_token_id, str):
        return path
    return async_sign_path(
        hass, path, SIGNED_URL_TTL, refresh_token_id=refresh_token_id
    )


class ImouMediaView(HomeAssistantView):
    """Serve media files to authenticated users and signed URLs only."""

    url = f"{URL_PREFIX}/{{filename:.*}}"
    name = "api:imou_life:media"
    requires_auth = True

    async def get(self, request: web.Request, filename: str) -> web.StreamResponse:
        """Return one media file without allowing path escapes or caching."""
        hass: HomeAssistant = request.app["hass"]
        root = media_root(hass)
        path = (root / filename).resolve()
        if (
            not path.is_relative_to(root)
            or path.suffix.lower() not in _ALLOWED_SUFFIXES
            or not path.is_file()
        ):
            raise web.HTTPNotFound()
        return web.FileResponse(
            path,
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
