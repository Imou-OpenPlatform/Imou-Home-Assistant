"""Fetch Imou CDN URLs with curl when aiohttp TLS fails (7080/7088)."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import shutil
import socket
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from aiohttp import ClientSession, ClientTimeout, StreamReader, TCPConnector
from aiohttp.abc import AbstractResolver, ResolveResult
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

_CURL_TIMEOUT_SECONDS = 120
_CURL_MAX_FILESIZE = 256 * 1024 * 1024
_FETCH_CHUNK_BYTES = 64 * 1024
_MAX_REDIRECTS = 5


@dataclass(frozen=True, slots=True)
class UrlFetchResponse:
    """Status, headers, and body returned by an Imou media URL."""

    status: int
    headers: Mapping[str, str]
    body: bytes
    final_url: str | None = None


class UrlFetchBodyTooLargeError(ValueError):
    """Raised when a response exceeds the caller's configured body limit."""


class UrlFetchUnsafeError(ValueError):
    """Raised when a URL or its resolved addresses are unsafe to fetch."""


class UrlFetchText(str):
    """Fetched text carrying the final URL after validated redirects."""

    final_url: str

    def __new__(cls, value: str, final_url: str) -> UrlFetchText:
        instance = super().__new__(cls, value)
        instance.final_url = final_url
        return instance


@dataclass(frozen=True, slots=True)
class PinnedUrl:
    """A syntactically valid public URL and its one-time DNS result."""

    url: str
    host: str
    port: int
    addresses: tuple[str, ...]


class _PinnedResolver(AbstractResolver):
    """Resolve exactly one host from an already validated snapshot."""

    def __init__(self, pinned: PinnedUrl) -> None:
        self._pinned = pinned

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET
    ) -> list[ResolveResult]:
        """Return only the addresses validated for this request hop."""
        if host.rstrip(".").lower() != self._pinned.host.rstrip(".").lower():
            raise OSError("Unexpected outbound hostname")
        return [
            ResolveResult(
                hostname=host,
                host=address,
                port=self._pinned.port,
                family=socket.AF_INET6 if ":" in address else socket.AF_INET,
                proto=socket.IPPROTO_TCP,
                flags=socket.AI_NUMERICHOST,
            )
            for address in self._pinned.addresses
        ]

    async def close(self) -> None:
        """Release no resources."""


def _curl_available() -> bool:
    return shutil.which("curl") is not None


def _lechange_cdn_url(url: str) -> bool:
    parsed = urlsplit(url)
    return bool(
        parsed.hostname
        and (
            parsed.hostname.lower() == "lechange.com"
            or parsed.hostname.lower().endswith(".lechange.com")
        )
        and parsed.port in {7080, 7088}
    )


def _sync_curl_response(
    pinned: PinnedUrl,
    range_header: str | None = None,
    max_bytes: int | None = None,
    timeout_seconds: float = _CURL_TIMEOUT_SECONDS,
) -> UrlFetchResponse:
    max_filesize = _CURL_MAX_FILESIZE if max_bytes is None else max_bytes
    with tempfile.TemporaryDirectory() as temp_dir:
        header_path = Path(temp_dir) / "headers"
        body_path = Path(temp_dir) / "body"
        cmd = [
            "curl",
            "-sS",
            "--max-time",
            str(timeout_seconds),
            "--max-filesize",
            str(max_filesize),
            "--http1.1",
            "--proto",
            "=http,https",
            "--noproxy",
            "*",
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
        for address in pinned.addresses:
            resolved_address = f"[{address}]" if ":" in address else address
            cmd.extend(
                [
                    "--resolve",
                    f"{pinned.host}:{pinned.port}:{resolved_address}",
                ]
            )
        if _lechange_cdn_url(pinned.url):
            cmd.extend(["-k", "--tlsv1.2"])
        cmd.append(pinned.url)
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
        )
        if completed.returncode == 63:  # CURLE_FILESIZE_EXCEEDED
            raise UrlFetchBodyTooLargeError(
                f"Response body exceeds {max_filesize} bytes"
            )
        if completed.returncode != 0:
            msg = f"curl failed ({completed.returncode})"
            raise OSError(msg)
        header_blocks = [
            block
            for block in header_path.read_text(errors="replace")
            .replace("\r\n", "\n")
            .split("\n\n")
            if block.startswith("HTTP/")
        ]
        if not header_blocks:
            msg = "curl returned no HTTP response headers"
            raise OSError(msg)
        lines = header_blocks[-1].splitlines()
        try:
            status = int(lines[0].split(" ", 2)[1])
        except (IndexError, ValueError) as err:
            msg = "curl returned an invalid HTTP status"
            raise OSError(msg) from err
        headers = {
            name.strip(): value.strip()
            for line in lines[1:]
            if ":" in line
            for name, value in [line.split(":", 1)]
        }
        body = body_path.read_bytes()
        if len(body) > max_filesize:
            raise UrlFetchBodyTooLargeError(
                f"Response body exceeds {max_filesize} bytes"
            )
        return UrlFetchResponse(status, headers, body)


async def _async_read_response_body(
    content: StreamReader,
    headers: Mapping[str, str],
    max_bytes: int | None,
) -> bytes:
    """Drain a response without buffering more than the configured limit."""
    if max_bytes is not None:
        raw_length = next(
            (
                value
                for name, value in headers.items()
                if name.lower() == "content-length"
            ),
            None,
        )
        try:
            expected = int(raw_length) if raw_length is not None else None
        except (TypeError, ValueError):
            expected = None
        if expected is not None and expected > max_bytes:
            raise UrlFetchBodyTooLargeError(f"Response body exceeds {max_bytes} bytes")
    chunks: list[bytes] = []
    total = 0
    async for chunk in content.iter_chunked(_FETCH_CHUNK_BYTES):
        total += len(chunk)
        if max_bytes is not None and total > max_bytes:
            raise UrlFetchBodyTooLargeError(f"Response body exceeds {max_bytes} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


async def _async_aiohttp_get_response(
    pinned: PinnedUrl,
    range_header: str | None = None,
    max_bytes: int | None = None,
    timeout_seconds: float = _CURL_TIMEOUT_SECONDS,
) -> UrlFetchResponse:
    headers = {"Range": range_header} if range_header is not None else None
    connector = TCPConnector(
        resolver=_PinnedResolver(pinned),
        use_dns_cache=False,
    )
    async with (
        ClientSession(connector=connector) as session,
        session.get(
            pinned.url,
            timeout=ClientTimeout(total=timeout_seconds),
            headers=headers,
            allow_redirects=False,
        ) as response,
    ):
        body = await _async_read_response_body(
            response.content, dict(response.headers), max_bytes
        )
        return UrlFetchResponse(response.status, dict(response.headers), body)


async def async_pin_public_url(hass: HomeAssistant, url: str) -> PinnedUrl:
    """Validate a URL and pin its complete public DNS answer."""
    if url != url.strip() or any(
        ord(character) < 32 or 127 <= ord(character) <= 159 for character in url
    ):
        raise UrlFetchUnsafeError("Unsafe outbound media URL")
    try:
        parsed = urlsplit(url)
        explicit_port = parsed.port
    except ValueError as err:
        raise UrlFetchUnsafeError("Unsafe outbound media URL") from err
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or explicit_port == 0
    ):
        raise UrlFetchUnsafeError("Unsafe outbound media URL")
    port = explicit_port or (443 if parsed.scheme.lower() == "https" else 80)
    host = parsed.hostname
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        try:
            results = await hass.async_add_executor_job(
                socket.getaddrinfo,
                host,
                port,
                socket.AF_UNSPEC,
                socket.SOCK_STREAM,
            )
        except OSError as err:
            raise ValueError("Outbound media host could not be resolved") from err
        addresses = tuple(
            dict.fromkeys(str(result[4][0]).split("%", 1)[0] for result in results)
        )
    else:
        addresses = (str(literal),)
    if not addresses or any(
        not ipaddress.ip_address(address).is_global for address in addresses
    ):
        raise UrlFetchUnsafeError("Outbound media host is not public")
    return PinnedUrl(url, host, port, addresses)


def _redirect_location(response: UrlFetchResponse) -> str | None:
    if response.status not in {301, 302, 303, 307, 308}:
        return None
    return next(
        (
            value
            for name, value in response.headers.items()
            if name.lower() == "location"
        ),
        None,
    )


async def _async_fetch_pinned_once(
    hass: HomeAssistant,
    pinned: PinnedUrl,
    range_header: str | None,
    max_bytes: int | None = None,
    timeout_seconds: float = _CURL_TIMEOUT_SECONDS,
) -> UrlFetchResponse:
    try:
        return await _async_aiohttp_get_response(
            pinned,
            range_header,
            max_bytes=max_bytes,
            timeout_seconds=timeout_seconds,
        )
    except UrlFetchBodyTooLargeError:
        raise
    except Exception:
        if not _curl_available():
            raise OSError("aiohttp media fetch failed") from None
        _LOGGER.debug(
            "aiohttp media fetch failed for host %s; trying curl",
            pinned.host,
        )
    return await hass.async_add_executor_job(
        _sync_curl_response, pinned, range_header, max_bytes, timeout_seconds
    )


async def async_fetch_url_response(
    hass: HomeAssistant,
    url: str,
    *,
    range_header: str | None = None,
    max_bytes: int | None = None,
    timeout_seconds: float = _CURL_TIMEOUT_SECONDS,
) -> UrlFetchResponse:
    """GET an Imou media URL while retaining status and response headers."""
    if range_header is not None and ("\r" in range_header or "\n" in range_header):
        msg = "Invalid Range header"
        raise ValueError(msg)
    if max_bytes is not None and max_bytes <= 0:
        msg = "max_bytes must be positive"
        raise ValueError(msg)
    if timeout_seconds <= 0:
        msg = "timeout_seconds must be positive"
        raise ValueError(msg)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    current_url = url
    async with asyncio.timeout_at(deadline):
        for redirect_count in range(_MAX_REDIRECTS + 1):
            pinned = await async_pin_public_url(hass, current_url)
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError
            response = await _async_fetch_pinned_once(
                hass,
                pinned,
                range_header,
                max_bytes=max_bytes,
                timeout_seconds=remaining,
            )
            location = _redirect_location(response)
            if location is None:
                return UrlFetchResponse(
                    response.status,
                    response.headers,
                    response.body,
                    current_url,
                )
            if redirect_count == _MAX_REDIRECTS:
                raise OSError("Too many outbound media redirects")
            current_url = urljoin(current_url, location)
    raise OSError("Too many outbound media redirects")


async def async_fetch_url_bytes(hass: HomeAssistant, url: str) -> bytes:
    """GET bytes from an Imou media CDN, using curl for Lechange TLS quirks."""
    response = await async_fetch_url_response(hass, url)
    if response.status >= 400:
        msg = f"HTTP {response.status} fetching outbound media"
        raise OSError(msg)
    return response.body


async def async_fetch_url_text(
    hass: HomeAssistant, url: str, *, max_bytes: int | None = None
) -> str:
    """GET text (m3u8) from Imou media CDN."""
    response = await async_fetch_url_response(hass, url, max_bytes=max_bytes)
    if response.status >= 400:
        raise OSError(f"HTTP {response.status} fetching outbound media")
    return UrlFetchText(response.body.decode(), response.final_url or url)
