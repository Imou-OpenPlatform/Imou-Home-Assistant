"""Security tests for outbound recording media fetches."""

from __future__ import annotations

import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.imou_life.record_http_fetch import (
    PinnedUrl,
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
