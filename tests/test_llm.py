"""Unit tests for idea_search.llm - pure parsers + client behavior (mocked)."""

from __future__ import annotations

import pytest

from idea_search.config import Settings
from idea_search.llm import (
    LLMClient,
    parse_assess_json,
    parse_search_plan_json,
)
from idea_search.models import Repo

SETTINGS = Settings(deepseek_api_key="test-key")


def test_parse_search_plan_valid() -> None:
    raw = (
        '{"keywords": ["self-hosted", "knowledge base"], '
        '"queries": ["self-hosted knowledge base in:name"], '
        '"languages": ["Python"], "notes": "ok"}'
    )
    plan = parse_search_plan_json(raw)
    assert plan.keywords == ["self-hosted", "knowledge base"]
    assert plan.queries == ["self-hosted knowledge base in:name"]
    assert plan.languages == ["Python"]
    assert plan.notes == "ok"


def test_parse_search_plan_markdown_fences() -> None:
    raw = '```json\n{"keywords": ["a"], "queries": ["b"], "languages": [], "notes": "n"}\n```'
    plan = parse_search_plan_json(raw)
    assert plan.keywords == ["a"]
    assert plan.queries == ["b"]


def test_parse_search_plan_trailing_comma_repaired() -> None:
    raw = '{"keywords": ["a",], "queries": ["b",], "languages": [], "notes": "n",}'
    plan = parse_search_plan_json(raw)
    assert plan.keywords == ["a"]
    assert plan.queries == ["b"]


def test_parse_search_plan_unquoted_keys_repaired() -> None:
    raw = '{keywords: ["a"], queries: ["b"], languages: [], notes: "n"}'
    plan = parse_search_plan_json(raw)
    assert plan.keywords == ["a"]
    assert plan.queries == ["b"]


def test_parse_search_plan_garbage_returns_empty() -> None:
    plan = parse_search_plan_json("this is not json at all")
    assert plan.keywords == []
    assert plan.queries == []
    assert plan.languages == []
    assert plan.notes == ""


async def test_generate_plan_falls_back_to_raw_keywords(monkeypatch) -> None:
    llm = LLMClient(SETTINGS)

    async def fake_chat(system: str, user: str) -> str:
        return "not json"

    monkeypatch.setattr(llm, "_chat_json", fake_chat)
    plan = await llm.generate_plan("自托管的个人知识库 + AI 问答")
    assert plan.queries
    assert "自托管的个人知识库" in plan.queries[0]
    assert "AI" in plan.keywords
    assert "退化为" in plan.notes


def test_parse_assess_json_valid() -> None:
    raw = (
        '{"repos": [{"full_name": "a/b", "score": 90, "reason": "很匹配"}, '
        '{"full_name": "c/d", "score": 30, "reason": "一般"}], '
        '"summary": "整体不错"}'
    )
    assessments, summary = parse_assess_json(raw)
    assert assessments == [("a/b", 90, "很匹配"), ("c/d", 30, "一般")]
    assert summary == "整体不错"


def test_parse_assess_json_clamps_and_skips() -> None:
    raw = (
        '{"repos": [{"full_name": "a/b", "score": 500, "reason": "x"}, '
        '{"score": 50, "reason": "no name"}, {"full_name": "", "score": 1}], '
        '"summary": ""}'
    )
    assessments, summary = parse_assess_json(raw)
    assert assessments == [("a/b", 100, "x")]
    assert summary == ""


async def test_assess_aligns_and_defaults_missing_to_zero(monkeypatch) -> None:
    llm = LLMClient(SETTINGS)

    async def fake_chat(system: str, user: str) -> str:
        return '{"repos": [{"full_name": "a/b", "score": 80, "reason": "好"}], "summary": "总结"}'

    monkeypatch.setattr(llm, "_chat_json", fake_chat)
    repos = [
        Repo(full_name="a/b", url="u", stars=10),
        Repo(full_name="x/y", url="u", stars=5),
    ]
    assessments, summary = await llm.assess("idea", repos)
    assert assessments == [(80, "好"), (0, "")]
    assert summary == "总结"


async def test_assess_caps_at_25_repos(monkeypatch) -> None:
    llm = LLMClient(SETTINGS)
    sent: list[str] = []

    async def fake_chat(system: str, user: str) -> str:
        sent.append(user)
        return '{"repos": [], "summary": ""}'

    monkeypatch.setattr(llm, "_chat_json", fake_chat)
    repos = [Repo(full_name=f"o/r{i}", url="u", stars=i) for i in range(30)]
    assessments, _ = await llm.assess("idea", repos)
    assert len(sent) == 1
    assert "o/r24" in sent[0]
    assert "o/r25" not in sent[0]
    assert len(assessments) == 25