"""Load a small frontend helper for AI Insight trial dialogs."""

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
_MODULE_URL = f"{_STATIC_URL}/trial-dialog.js?v=3"
_SCRIPT_PATH = Path(__file__).parent / "www" / "trial-dialog.js"


class ImouTrialDialogView(HomeAssistantView):
    """Serve the trial dialog script so browsers do not keep an old copy."""

    requires_auth = False
    url = f"{_STATIC_URL}/trial-dialog.js"
    name = "imou_life:trial_dialog"

    async def get(self, request: web.Request) -> web.Response:
        """Return the current script with no-store."""
        hass: HomeAssistant = request.app["hass"]
        body = await hass.async_add_executor_job(_SCRIPT_PATH.read_bytes)
        return web.Response(
            body=body,
            content_type="text/javascript",
            charset="utf-8",
            headers={"Cache-Control": "no-store"},
        )


async def async_register_insight_trial_frontend(hass: HomeAssistant) -> bool:
    """Register the optional AI Insight dialog module when frontend is loaded."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_TRIAL_DIALOG_FLAG):
        return True
    if (
        "frontend" not in hass.config.components
        or "http" not in hass.config.components
        or hass.http is None
    ):
        _LOGGER.debug("AI Insight dialog frontend is unavailable")
        return False

    try:
        hass.http.register_view(ImouTrialDialogView())
        frontend.add_extra_js_url(hass, _MODULE_URL)
    except (KeyError, RuntimeError):
        _LOGGER.warning("Unable to register the optional AI Insight dialog")
        return False

    domain_data[_TRIAL_DIALOG_FLAG] = True
    return True
