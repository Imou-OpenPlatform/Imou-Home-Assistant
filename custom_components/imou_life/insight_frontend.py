"""Load small frontend helpers for AI Insight dialogs and form layout."""

from __future__ import annotations

import logging
from pathlib import Path

from aiohttp import web
from homeassistant.components import frontend
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_TRIAL_DIALOG_FLAG = "trial_dialog_registered"
_STATIC_URL = f"/{DOMAIN}_static"
_WWW = Path(__file__).parent / "www"
_SCRIPTS = (
    ("trial-dialog.js", "imou_life:trial_dialog", 3),
    ("insight-form.js", "imou_life:insight_form", 1),
)


def _module_url(filename: str, version: int) -> str:
    return f"{_STATIC_URL}/{filename}?v={version}"


def _make_script_view(
    filename: str, view_name: str
) -> type[HomeAssistantView]:
    script_path = _WWW / filename

    class ImouInsightScriptView(HomeAssistantView):
        """Serve one AI Insight script with no-store."""

        requires_auth = False
        url = f"{_STATIC_URL}/{filename}"
        name = view_name

        async def get(self, request: web.Request) -> web.Response:
            """Return the current script with no-store."""
            body = await request.app["hass"].async_add_executor_job(
                script_path.read_bytes
            )
            return web.Response(
                body=body,
                content_type="text/javascript",
                charset="utf-8",
                headers={"Cache-Control": "no-store"},
            )

    ImouInsightScriptView.__name__ = f"ImouInsight_{filename.replace('.', '_')}"
    return ImouInsightScriptView


async def async_register_insight_trial_frontend(hass: HomeAssistant) -> bool:
    """Register optional AI Insight frontend modules when frontend is loaded."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_TRIAL_DIALOG_FLAG):
        return True
    if (
        "frontend" not in hass.config.components
        or "http" not in hass.config.components
        or hass.http is None
    ):
        _LOGGER.debug("AI Insight frontend helpers are unavailable")
        return False

    try:
        for filename, view_name, version in _SCRIPTS:
            hass.http.register_view(_make_script_view(filename, view_name)())
            frontend.add_extra_js_url(hass, _module_url(filename, version))
    except (KeyError, RuntimeError):
        _LOGGER.warning("Unable to register the optional AI Insight frontend")
        return False

    domain_data[_TRIAL_DIALOG_FLAG] = True
    return True
