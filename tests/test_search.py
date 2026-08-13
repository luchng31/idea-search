"""End-to-end pipeline tests for SearchService with fakes (no network/LLM)."""

from __future__ import annotations

import pytest

from idea_search.models import Repo, SearchPlan
from idea_search.search import SearchService

CANNED_PLAN = SearchPlan(
    keywords=["knowledge base", "ai"],
    queries=["kb ai in:description", "knowledge base llm"],
    languages=["python", "typescript"],
    notes="",
)


class FakeLLM:
    def __init__(self, plan: SearchPlan = CANNED_PLAN) -> None:
        self.plan = plan
        self.assessed: list[str] = []

    async def generate_plan(self, idea: str) -> SearchPlan:
        return self.plan

    async def assess(
        self, idea: str, repos: list[Repo]
    ) -> tuple[list[tuple[int, str]], str]:
        if not repos:
            return [], ""
        self.assessed = [r.full_name for r in repos]
        return (
            [(90 if i == 0 else 50, f"reason{i}") for i in range(len(repos))],
            "整体总结",
        )


class FakeGithub:
    def __init__(self, per_query: dict[str, list[Repo]]) -> None:
        self.per_query = per_query

    async def search(self, query: str, per_page: int = 15) -> list[Repo]:
        return list(self.per_query.get(query, []))


def make_repo(name: str, stars: int, language: str = "Python") -> Repo:
    return Repo(
        full_name=name,
        url=f"https://github.com/{name}",
        description="desc",
        language=language,
        stars=stars,
        forks=1,
        updated_at="2026-01-01",
        topics=[],
    )


@pytest.mark.asyncio
async def test_search_full_pipeline() -> None:
    llm = FakeLLM()
    github = FakeGithub(
        {
            "kb ai in:description": [
                make_repo("a/low", 10),
                make_repo("b/high", 1000),
            ],
            "knowledge base llm": [make_repo("b/high", 1000)],
        }
    )
    progress: list[str] = []
    service = SearchService(llm=llm, github=github, max_repos=25)

    result = await service.search("自托管知识库", on_progress=progress.append)

    assert result.idea == "自托管知识库"
    assert result.plan is CANNED_PLAN
    assert {r.full_name for r in result.repositories} == {"a/low", "b/high"}
    assert len(result.repositories) == 2
    assert result.repositories[0].full_name == "b/high"
    assert result.repositories[0].score == 90
    assert result.repositories[1].score == 50
    assert result.repositories[1].reason == "reason1"
    assert result.summary == "整体总结"
    assert progress[0] == "正在生成搜索策略..."
    assert any("正在搜索 GitHub" in m for m in progress)
    assert any("评审" in m for m in progress)


@pytest.mark.asyncio
async def test_search_dedupes_across_queries() -> None:
    llm = FakeLLM()
    github = FakeGithub(
        {
            "kb ai in:description": [
                make_repo("dup/repo", 100),
                make_repo("only/first", 50),
            ],
            "knowledge base llm": [make_repo("dup/repo", 100)],
        }
    )
    service = SearchService(llm=llm, github=github, max_repos=25)
    result = await service.search("idea")
    assert {r.full_name for r in result.repositories} == {"dup/repo", "only/first"}


@pytest.mark.asyncio
async def test_search_language_filter() -> None:
    llm = FakeLLM()
    github = FakeGithub(
        {
            "kb ai in:description": [
                make_repo("py/repo", 100, language="Python"),
                make_repo("js/repo", 90, language="JavaScript"),
            ]
        }
    )
    service = SearchService(llm=llm, github=github, max_repos=25)
    result = await service.search("idea", language_filter="python")
    assert [r.full_name for r in result.repositories] == ["py/repo"]


@pytest.mark.asyncio
async def test_search_caps_at_max_repos() -> None:
    llm = FakeLLM()
    many = [make_repo(f"r{i:02d}", 1000 - i) for i in range(30)]
    github = FakeGithub({"kb ai in:description": many})
    service = SearchService(llm=llm, github=github, max_repos=5)
    result = await service.search("idea")
    assert len(result.repositories) == 5
    assert llm.assessed == ["r00", "r01", "r02", "r03", "r04"]


@pytest.mark.asyncio
async def test_search_empty_results() -> None:
    llm = FakeLLM()
    github = FakeGithub({})
    service = SearchService(llm=llm, github=github, max_repos=25)
    result = await service.search("idea")
    assert result.repositories == []
    assert result.summary == ""