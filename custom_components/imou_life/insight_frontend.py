"""Load a small frontend helper for AI Insight trial dialogs."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_TRIAL_DIALOG_FLAG = "trial_dialog_registered"
_STATIC_URL = f"/{DOMAIN}_static"
_MODULE_URL = f"{_STATIC_URL}/trial-dialog.js"


async def async_register_insight_trial_frontend(hass: HomeAssistant) -> None:
    """Serve trial-dialog.js and load it as a frontend module (once)."""
    if hass.data.setdefault(DOMAIN, {}).get(_TRIAL_DIALOG_FLAG):
        return
    www = Path(__file__).parent / "www"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(_STATIC_URL, str(www), False)]
    )
    frontend.add_extra_js_url(hass, _MODULE_URL)
    hass.data[DOMAIN][_TRIAL_DIALOG_FLAG] = True
