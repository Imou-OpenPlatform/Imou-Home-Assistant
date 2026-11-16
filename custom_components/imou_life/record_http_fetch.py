"""Fetch Imou CDN URLs with curl when aiohttp TLS fails (7080/7088)."""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from aiohttp import ClientSession
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

_CURL_TIMEOUT = "120"


@dataclass(frozen=True, slots=True)
class UrlFetchResponse:
    """Status, headers, and body returned by an Imou media URL."""

    status: int
    headers: Mapping[str, str]
    body: bytes


def _curl_available() -> bool:
    return shutil.which("curl") is not None


def _lechange_cdn_url(url: str) -> bool:
    lowered = url.lower()
    return "lechange.com" in lowered and (":7080" in lowered or ":7088" in lowered)


def _sync_curl_response(url: str, range_header: str | None = None) -> UrlFetchResponse:
    with tempfile.TemporaryDirectory() as temp_dir:
        header_path = Path(temp_dir) / "headers"
        body_path = Path(temp_dir) / "body"
        cmd = [
            "curl",
            "-sS",
            "--max-time",
            _CURL_TIMEOUT,
            "--http1.1",
            "--dump-header",
            str(header_path),
            "--output",
            str(body_path),
        ]
        if range_header is not None:
            if "\r" in range_header or "\n" in range_header:
                msg = "Invalid Range header"
                raise ValueError(msg)
            cmd.extend(["--header", f"Range: {range_header}"])
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
        header_blocks = [
            block
            for block in header_path.read_text(errors="replace")
            .replace("\r\n", "\n")
            .split("\n\n")
            if block.startswith("HTTP/")
        ]
        if not header_blocks:
            msg = f"curl returned no HTTP response headers for {url}"
            raise OSError(msg)
        lines = header_blocks[-1].splitlines()
        try:
            status = int(lines[0].split(" ", 2)[1])
        except (IndexError, ValueError) as err:
            msg = f"curl returned an invalid HTTP status for {url}"
            raise OSError(msg) from err
        headers = {
            name.strip(): value.strip()
            for line in lines[1:]
            if ":" in line
            for name, value in [line.split(":", 1)]
        }
        return UrlFetchResponse(status, headers, body_path.read_bytes())


async def _async_aiohttp_get_response(
    session: ClientSession,
    url: str,
    range_header: str | None = None,
) -> UrlFetchResponse:
    headers = {"Range": range_header} if range_header is not None else None
    async with session.get(url, timeout=120, headers=headers) as response:
        return UrlFetchResponse(
            response.status,
            dict(response.headers),
            await response.read(),
        )


async def async_fetch_url_response(
    hass: HomeAssistant,
    url: str,
    *,
    range_header: str | None = None,
) -> UrlFetchResponse:
    """GET an Imou media URL while retaining status and response headers."""
    if range_header is not None and ("\r" in range_header or "\n" in range_header):
        msg = "Invalid Range header"
        raise ValueError(msg)
    if _curl_available():
        try:
            return await hass.async_add_executor_job(
                _sync_curl_response, url, range_header
            )
        except (OSError, ValueError):
            if _lechange_cdn_url(url):
                raise
            _LOGGER.debug(
                "curl fetch failed for %s, trying aiohttp", url, exc_info=True
            )
    session = async_get_clientsession(hass)
    try:
        return await _async_aiohttp_get_response(session, url, range_header)
    except Exception:
        if not _curl_available():
            raise
        _LOGGER.debug(
            "aiohttp fetch failed for %s, retrying with curl", url, exc_info=True
        )
    return await hass.async_add_executor_job(_sync_curl_response, url, range_header)


async def async_fetch_url_bytes(hass: HomeAssistant, url: str) -> bytes:
    """GET bytes from an Imou media CDN, using curl for Lechange TLS quirks."""
    response = await async_fetch_url_response(hass, url)
    if response.status >= 400:
        msg = f"HTTP {response.status} fetching {url}"
        raise OSError(msg)
    return response.body


async def async_fetch_url_text(hass: HomeAssistant, url: str) -> str:
    """GET text (m3u8) from Imou media CDN."""
    return (await async_fetch_url_bytes(hass, url)).decode()
