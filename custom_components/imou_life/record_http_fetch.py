"""Fetch Imou CDN URLs with curl when aiohttp TLS fails (7080/7088)."""

from __future__ import annotations

import logging
import shutil
import subprocess

from aiohttp import ClientSession
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

_CURL_TIMEOUT = "120"


def _curl_available() -> bool:
    return shutil.which("curl") is not None


def _lechange_cdn_url(url: str) -> bool:
    lowered = url.lower()
    return "lechange.com" in lowered and (":7080" in lowered or ":7088" in lowered)


def _sync_curl(url: str) -> bytes:
    cmd = ["curl", "-sfS", "--max-time", _CURL_TIMEOUT, "--http1.1"]
    if _lechange_cdn_url(url):
        cmd.extend(["-k", "--tlsv1.2"])
    cmd.append(url)
    completed = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        stderr = (completed.stderr or b"").decode(errors="replace").strip()
        msg = f"curl failed ({completed.returncode}): {stderr or url}"
        raise OSError(msg)
    return completed.stdout


async def _async_aiohttp_get(session: ClientSession, url: str) -> bytes:
    async with session.get(url, timeout=120) as response:
        response.raise_for_status()
        return await response.read()


async def async_fetch_url_bytes(hass: HomeAssistant, url: str) -> bytes:
    """GET bytes from an Imou media CDN, using curl for Lechange TLS quirks."""
    if _curl_available():
        try:
            return await hass.async_add_executor_job(_sync_curl, url)
        except OSError:
            if _lechange_cdn_url(url):
                raise
            _LOGGER.debug("curl fetch failed for %s, trying aiohttp", url, exc_info=True)
    session = async_get_clientsession(hass)
    try:
        return await _async_aiohttp_get(session, url)
    except Exception:
        if not _curl_available():
            raise
        _LOGGER.debug("aiohttp fetch failed for %s, retrying with curl", url, exc_info=True)
        return await hass.async_add_executor_job(_sync_curl, url)


async def async_fetch_url_text(hass: HomeAssistant, url: str) -> str:
    """GET text (m3u8) from Imou media CDN."""
    return (await async_fetch_url_bytes(hass, url)).decode()
