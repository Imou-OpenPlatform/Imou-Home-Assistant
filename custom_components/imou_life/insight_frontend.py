"""Load a small frontend helper for AI Insight trial dialogs."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_TRIAL_DIALOG_FLAG = "trial_dialog_registered"
_STATIC_URL = f"/{DOMAIN}_static"
_MODULE_URL = f"{_STATIC_URL}/trial-dialog.js"


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

    www = Path(__file__).parent / "www"
    try:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(_STATIC_URL, str(www), False)]
        )
        frontend.add_extra_js_url(hass, _MODULE_URL)
    except (KeyError, RuntimeError):
        _LOGGER.warning("Unable to register the optional AI Insight dialog")
        return False

    domain_data[_TRIAL_DIALOG_FLAG] = True
    return True
