"""Orchestration: idea -> plan -> GitHub search -> LLM assessment."""

from __future__ import annotations

from collections.abc import Callable

from .github import GitHubClient
from .llm import LLMClient
from .models import Repo, SearchResult, repos_sorted


class SearchService:
    """Coordinates the LLM and GitHub clients to answer an idea."""

    def __init__(
        self,
        llm: LLMClient | None = None,
        github: GitHubClient | None = None,
        max_repos: int = 25,
    ) -> None:
        self.llm = llm if llm is not None else LLMClient()
        self.github = github if github is not None else GitHubClient()
        self.max_repos = max_repos

    async def search(
        self,
        idea: str,
        language_filter: str | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> SearchResult:
        def progress(msg: str) -> None:
            if on_progress is not None:
                on_progress(msg)

        progress("正在生成搜索策略...")
        plan = await self.llm.generate_plan(idea)

        seen: dict[str, Repo] = {}
        for query in plan.queries[:4]:
            progress(f"正在搜索 GitHub... {query}")
            for repo in await self.github.search(query):
                seen.setdefault(repo.full_name, repo)

        candidates = list(seen.values())
        if language_filter:
            wanted = language_filter.lower()
            candidates = [
                r for r in candidates if r.language and r.language.lower() == wanted
            ]

        selected = sorted(candidates, key=lambda r: r.stars, reverse=True)[
            : self.max_repos
        ]
        progress("正在让 AI 评审匹配度...")
        assessments, summary = await self.llm.assess(idea, selected)
        for repo, (score, reason) in zip(selected, assessments):
            repo.score = score
            repo.reason = reason

        return SearchResult(
            idea=idea,
            plan=plan,
            repositories=repos_sorted(selected),
            summary=summary,
        )