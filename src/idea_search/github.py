"""GitHub Search API client with rate-limit aware throttling."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from .config import Settings
from .models import Repo

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com/search/repositories"
ACCEPT_HEADER = "application/vnd.github+json"
USER_AGENT = "idea-search/0.1"


def _extract_repos(payload: dict[str, Any]) -> list[Repo]:
    """Pure function: convert a GitHub search API payload into Repo objects."""
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    repos: list[Repo] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        full_name = item.get("full_name")
        if not isinstance(full_name, str) or not full_name:
            continue
        repos.append(
            Repo(
                full_name=full_name,
                url=str(item.get("html_url") or ""),
                description=item.get("description"),
                language=item.get("language"),
                stars=int(item.get("stargazers_count") or 0),
                forks=int(item.get("forks_count") or 0),
                updated_at=item.get("updated_at"),
                topics=[t for t in (item.get("topics") or []) if isinstance(t, str)],
            )
        )
    return repos


def _parse_retry_after(value: str | None) -> float:
    """Parse a Retry-After header value (seconds); fallback 60s."""
    if not value:
        return 60.0
    try:
        return max(0.0, float(value))
    except ValueError:
        return 60.0


class GitHubClient:
    """Async GitHub Search API client.

    ``search()`` never raises: transient failures (network, 403/429 rate
    limits, 5xx) are retried up to 2 times, and the final fallback is an
    empty list so the pipeline can continue with other queries.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.from_env()
        headers: dict[str, str] = {
            "Accept": ACCEPT_HEADER,
            "User-Agent": USER_AGENT,
        }
        if self.settings.has_git_token:
            headers["Authorization"] = f"Bearer {self.settings.github_token}"
        self._client = httpx.AsyncClient(
            headers=headers,
            timeout=30.0,
            follow_redirects=True,
        )
        # Unauthenticated search limit is 10 req/min -> 7s spacing;
        # with a token it is 30 req/min -> 2.5s spacing.
        self._delay = 2.5 if self.settings.has_git_token else 7.0
        self._last_call_at: float | None = None

    async def close(self) -> None:
        await self._client.aclose()

    async def _throttle(self) -> None:
        """Sleep so calls are spaced by ``self._delay`` (first call is free)."""
        now = time.monotonic()
        if self._last_call_at is not None:
            elapsed = now - self._last_call_at
            if elapsed < self._delay:
                await asyncio.sleep(self._delay - elapsed)
        self._last_call_at = time.monotonic()

    async def search(self, query: str, per_page: int = 15) -> list[Repo]:
        """Search repositories, sorted by stars. Never raises."""
        params = {"q": query, "per_page": per_page, "sort": "stars"}
        await self._throttle()
        for attempt in range(3):
            try:
                resp = await self._client.get(GITHUB_API, params=params)
            except httpx.HTTPError as exc:
                logger.warning("GitHub search network error: %s", exc)
                if attempt < 2:
                    await asyncio.sleep(self._delay)
                    continue
                return []
            if resp.status_code == 200:
                try:
                    return _extract_repos(resp.json())
                except ValueError:
                    logger.warning("GitHub search returned invalid JSON")
                    return []
            if resp.status_code in (403, 429):
                retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
                logger.warning(
                    "GitHub rate limited (%s), retrying in %ss",
                    resp.status_code,
                    retry_after,
                )
                if attempt < 2:
                    await asyncio.sleep(retry_after)
                    continue
                return []
            if 500 <= resp.status_code < 600:
                logger.warning("GitHub search server error %s", resp.status_code)
                if attempt < 2:
                    await asyncio.sleep(self._delay)
                    continue
                return []
            # Other 4xx (401, 422, ...): not retryable
            logger.warning("GitHub search failed with status %s", resp.status_code)
            return []
        return []