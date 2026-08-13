"""Unit tests for idea_search.github - mocked transport, no network."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from idea_search.config import Settings
from idea_search.github import (
    GitHubClient,
    _extract_repos,
    _parse_retry_after,
)

SETTINGS = Settings(deepseek_api_key="test-key", github_token="abc")


async def _no_sleep(_: float) -> None:
    return None


def _payload(items: int = 2) -> dict[str, Any]:
    return {
        "items": [
            {
                "full_name": f"owner/repo{i}",
                "html_url": f"https://github.com/owner/repo{i}",
                "description": "desc",
                "language": "Python",
                "stargazers_count": 100 + i,
                "forks_count": 10,
                "updated_at": "2026-01-01T00:00:00Z",
                "topics": ["ai", "kb"],
            }
            for i in range(items)
        ]
    }


def test_extract_repos() -> None:
    repos = _extract_repos(_payload())
    assert len(repos) == 2
    assert repos[0].full_name == "owner/repo0"
    assert repos[0].stars == 100
    assert repos[0].topics == ["ai", "kb"]


def test_extract_repos_empty_on_bad_payload() -> None:
    assert _extract_repos({}) == []
    assert _extract_repos({"items": [None, {}]}) == []


def test_parse_retry_after() -> None:
    assert _parse_retry_after("25") == 25.0
    assert _parse_retry_after("0") == 0.0
    assert _parse_retry_after("not-a-number") == 60.0
    assert _parse_retry_after(None) == 60.0


@pytest.mark.asyncio
async def test_search_success(monkeypatch) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_payload())

    client = GitHubClient(SETTINGS)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    repos = await client.search("query")
    assert len(repos) == 2
    assert repos[0].full_name == "owner/repo0"
    await client.close()


@pytest.mark.asyncio
async def test_search_rate_limited_retries_then_empty(monkeypatch) -> None:
    calls = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(403, headers={"Retry-After": "0"})

    client = GitHubClient(SETTINGS)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    assert await client.search("query") == []
    assert calls["n"] == 3
    await client.close()


@pytest.mark.asyncio
async def test_search_network_failure_returns_empty(monkeypatch) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    client = GitHubClient(SETTINGS)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    assert await client.search("query") == []
    await client.close()


@pytest.mark.asyncio
async def test_search_server_error_returns_empty(monkeypatch) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = GitHubClient(SETTINGS)
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    assert await client.search("query") == []
    await client.close()