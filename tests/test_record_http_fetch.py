"""Security tests for outbound recording media fetches."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.imou_life.record_http_fetch import (
    PinnedUrl,
    UrlFetchBodyTooLargeError,
    _async_fetch_pinned_once,
    _async_read_response_body,
    _PinnedResolver,
    _sync_curl_response,
    async_fetch_url_response,
    async_pin_public_url,
)
from homeassistant.core import HomeAssistant


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/media.ts",
        "http://[::1]/media.ts",
        "http://10.0.0.1/media.ts",
        "http://192.168.1.2/media.ts",
        "http://169.254.169.254/media.ts",
        "http://user:secret@example.com/media.ts",
        "http://2130706433/media.ts",
        "https://8.8.8.8:0/media.ts",
        "file:///etc/passwd",
    ],
)
async def test_pin_public_url_rejects_unsafe_targets(
    hass: HomeAssistant, url: str
) -> None:
    with (
        patch(
            "custom_components.imou_life.record_http_fetch.socket.getaddrinfo",
            return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))
            ],
        ),
        pytest.raises(ValueError),
    ):
        await async_pin_public_url(hass, url)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/\r\nX-Injected: yes",
        "https://example.com/\timage.jpg",
        "https://example.com/\x00image.jpg",
        "https://example.com/\x7fimage.jpg",
        " https://example.com/image.jpg",
    ],
)
async def test_pin_public_url_rejects_control_characters_before_dns(
    hass: HomeAssistant, url: str
) -> None:
    with (
        patch(
            "custom_components.imou_life.record_http_fetch.socket.getaddrinfo",
            return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))
            ],
        ) as dns,
        pytest.raises(ValueError, match="Unsafe outbound media URL"),
    ):
        await async_pin_public_url(hass, url)
    dns.assert_not_called()


async def test_pin_public_url_rejects_mixed_dns_results(
    hass: HomeAssistant,
) -> None:
    with (
        patch(
            "custom_components.imou_life.record_http_fetch.socket.getaddrinfo",
            return_value=[
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443)),
            ],
        ),
        pytest.raises(ValueError),
    ):
        await async_pin_public_url(hass, "https://media.example/video.ts")


async def test_pinned_resolver_never_requeries_dns(hass: HomeAssistant) -> None:
    with patch(
        "custom_components.imou_life.record_http_fetch.socket.getaddrinfo",
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))],
    ) as dns:
        pinned = await async_pin_public_url(
            hass, "https://media.example/video.ts?token=secret"
        )
    resolver = _PinnedResolver(pinned)

    resolved = await resolver.resolve("media.example", 443)
    assert [entry["host"] for entry in resolved] == ["8.8.8.8"]
    assert dns.call_count == 1


async def _aiter(chunks: tuple[bytes, ...]) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


async def test_aiohttp_stream_reads_all_chunks() -> None:
    content = SimpleNamespace(
        iter_chunked=lambda _size: _aiter((b"DHAV", b"\x00" * 32, b"end"))
    )

    body = await _async_read_response_body(content, {}, None)

    assert body == b"DHAV" + b"\x00" * 32 + b"end"


async def test_aiohttp_stream_rejects_oversized_body() -> None:
    content = SimpleNamespace(iter_chunked=lambda _size: _aiter((b"123", b"456")))

    with pytest.raises(UrlFetchBodyTooLargeError, match="exceeds 5 bytes"):
        await _async_read_response_body(content, {}, max_bytes=5)


async def test_aiohttp_stream_rejects_oversized_content_length() -> None:
    content = SimpleNamespace(iter_chunked=MagicMock())

    with pytest.raises(UrlFetchBodyTooLargeError, match="exceeds 5 bytes"):
        await _async_read_response_body(content, {"content-length": "6"}, max_bytes=5)
    content.iter_chunked.assert_not_called()


async def test_fetch_passes_body_limit_and_remaining_timeout(
    hass: HomeAssistant,
) -> None:
    pinned = PinnedUrl("https://media.example/pic", "media.example", 443, ("8.8.8.8",))
    response = SimpleNamespace(status=200, headers={}, body=b"pic")
    with (
        patch(
            "custom_components.imou_life.record_http_fetch.async_pin_public_url",
            new_callable=AsyncMock,
            return_value=pinned,
        ),
        patch(
            "custom_components.imou_life.record_http_fetch._async_fetch_pinned_once",
            new_callable=AsyncMock,
            return_value=response,
        ) as fetch,
    ):
        result = await async_fetch_url_response(
            hass, pinned.url, max_bytes=1234, timeout_seconds=7
        )

    assert result.body == b"pic"
    fetch.assert_awaited_once()
    assert fetch.await_args.args == (hass, pinned, None)
    assert fetch.await_args.kwargs["max_bytes"] == 1234
    assert 0 < fetch.await_args.kwargs["timeout_seconds"] <= 7


async def test_fetch_timeout_covers_the_whole_redirect_chain(
    hass: HomeAssistant,
) -> None:
    pinned = PinnedUrl("https://media.example/pic", "media.example", 443, ("8.8.8.8",))

    async def fetch(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    with (
        patch(
            "custom_components.imou_life.record_http_fetch.async_pin_public_url",
            new_callable=AsyncMock,
            return_value=pinned,
        ),
        patch(
            "custom_components.imou_life.record_http_fetch._async_fetch_pinned_once",
            side_effect=fetch,
        ),
        pytest.raises(TimeoutError),
    ):
        await async_fetch_url_response(hass, pinned.url, timeout_seconds=0.01)


async def test_curl_fallback_receives_body_limit_and_timeout(
    hass: HomeAssistant,
) -> None:
    pinned = PinnedUrl("https://media.example/pic", "media.example", 443, ("8.8.8.8",))
    response = SimpleNamespace(status=200, headers={}, body=b"pic")
    with (
        patch(
            "custom_components.imou_life.record_http_fetch._async_aiohttp_get_response",
            new_callable=AsyncMock,
            side_effect=OSError("TLS failure"),
        ),
        patch(
            "custom_components.imou_life.record_http_fetch._curl_available",
            return_value=True,
        ),
        patch(
            "custom_components.imou_life.record_http_fetch._sync_curl_response",
            return_value=response,
        ) as curl,
    ):
        result = await _async_fetch_pinned_once(
            hass, pinned, None, max_bytes=20, timeout_seconds=7
        )

    assert result is response
    curl.assert_called_once_with(pinned, None, 20, 7)


async def test_oversized_aiohttp_body_is_not_retried_with_curl(
    hass: HomeAssistant,
) -> None:
    pinned = PinnedUrl("https://media.example/pic", "media.example", 443, ("8.8.8.8",))
    with (
        patch(
            "custom_components.imou_life.record_http_fetch._async_aiohttp_get_response",
            new_callable=AsyncMock,
            side_effect=UrlFetchBodyTooLargeError("too large"),
        ),
        patch(
            "custom_components.imou_life.record_http_fetch._curl_available",
        ) as curl_available,
        pytest.raises(UrlFetchBodyTooLargeError),
    ):
        await _async_fetch_pinned_once(hass, pinned, None, max_bytes=20)
    curl_available.assert_not_called()


async def test_redirect_to_private_target_is_rejected(
    hass: HomeAssistant,
) -> None:
    async def pin(_hass: HomeAssistant, url: str) -> PinnedUrl:
        if "private.example" in url:
            raise ValueError("non-public")
        return PinnedUrl(url, "public.example", 443, ("8.8.8.8",))

    with (
        patch(
            "custom_components.imou_life.record_http_fetch.async_pin_public_url",
            side_effect=pin,
        ),
        patch(
            "custom_components.imou_life.record_http_fetch._async_fetch_pinned_once",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(
                status=302,
                headers={"Location": "http://private.example/secret"},
                body=b"",
            ),
        ),
        pytest.raises(ValueError),
    ):
        await async_fetch_url_response(hass, "https://public.example/start")


async def test_public_redirect_reports_final_pinned_url(
    hass: HomeAssistant,
) -> None:
    async def pin(_hass: HomeAssistant, url: str) -> PinnedUrl:
        return PinnedUrl(url, "public.example", 443, ("8.8.8.8",))

    with (
        patch(
            "custom_components.imou_life.record_http_fetch.async_pin_public_url",
            side_effect=pin,
        ),
        patch(
            "custom_components.imou_life.record_http_fetch._async_fetch_pinned_once",
            new_callable=AsyncMock,
            side_effect=[
                SimpleNamespace(
                    status=302,
                    headers={"Location": "/media/index.m3u8"},
                    body=b"",
                ),
                SimpleNamespace(status=200, headers={}, body=b"#EXTM3U\n"),
            ],
        ),
    ):
        response = await async_fetch_url_response(
            hass, "https://public.example/start.m3u8"
        )

    assert response.final_url == "https://public.example/media/index.m3u8"


def test_curl_uses_pinned_ip_and_never_follows_redirects() -> None:
    pinned = PinnedUrl(
        "https://media.example:8443/video.ts?token=secret",
        "media.example",
        8443,
        ("8.8.8.8",),
    )
    completed = MagicMock(returncode=1, stderr=b"failure")
    with (
        patch(
            "custom_components.imou_life.record_http_fetch.subprocess.run",
            return_value=completed,
        ) as run,
        pytest.raises(OSError, match="curl failed"),
    ):
        _sync_curl_response(pinned)

    command = run.call_args.args[0]
    assert command[command.index("--resolve") : command.index("--resolve") + 2] == [
        "--resolve",
        "media.example:8443:8.8.8.8",
    ]
    assert "--location" not in command
    assert "--proto" in command
    assert command[command.index("--max-filesize") + 1] == "268435456"


def test_curl_uses_configured_max_filesize() -> None:
    pinned = PinnedUrl("https://media.example/pic", "media.example", 443, ("8.8.8.8",))
    completed = MagicMock(returncode=63, stderr=b"too large")
    with (
        patch(
            "custom_components.imou_life.record_http_fetch.subprocess.run",
            return_value=completed,
        ) as run,
        pytest.raises(UrlFetchBodyTooLargeError, match="exceeds 20 bytes"),
    ):
        _sync_curl_response(pinned, max_bytes=20)

    command = run.call_args.args[0]
    assert command[command.index("--max-filesize") + 1] == "20"
