"""LLM integration: plan generation and repo assessment.

Uses the OpenAI SDK (``AsyncOpenAI``) against any OpenAI-compatible endpoint
(DeepSeek by default). All JSON parsing lives in pure module-level functions
so tests can exercise them without a network.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from openai import AsyncOpenAI

from .config import Settings
from .models import Repo, SearchPlan


class LLMError(Exception):
    """Raised when the LLM call itself fails (network, auth, ...)."""


# ---------------------------------------------------------------------------
# Pure JSON parsing helpers
# ---------------------------------------------------------------------------


def _strip_fences(raw: str) -> str:
    """Remove ```json ... ``` fences and surrounding whitespace."""
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    return fence.group(1).strip() if fence else text


def _first_json_block(text: str) -> str:
    """Locate the first balanced {...} block in ``text``."""
    start = text.find("{")
    if start == -1:
        return ""
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text[start:]


def _try_quote_key(out: list[str], text: str, i: int, n: int) -> int | None:
    """Quote an unquoted key starting at ``i``; return the new indexor None."""
    j = i
    while j < n and text[j].isspace():
        j += 1
    m = re.match(r"[A-Za-z_][A-Za-z0-9_]*", text[j:])
    if not m:
        return None
    key_end = j + m.end()
    k = key_end
    while k < n and text[k].isspace():
        k += 1
    if k >= n or text[k] != ":":
        return None
    out.append('"')
    out.append(text[j:key_end])
    out.append('"')
    out.append(":")
    return k + 1


def _repair_json(text: str) -> str:
    """Fix common LLM JSON mistakes: trailing commas and unquoted keys.

    Tracks string state so values containing ``,`` or ``}`` stay intact.
    """
    out: list[str] = []
    in_string = False
    escape = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "{":
            out.append(ch)
            next_i = _try_quote_key(out, text, i + 1, n)
            i = next_i if next_i is not None else i + 1
            continue
        if ch == ",":
            j = i + 1
            while j < n and text[j].isspace():
                j += 1
            if j < n and text[j] in "}]":
                i += 1  # drop a trailing comma
                continue
            out.append(ch)
            next_i = _try_quote_key(out, text, i + 1, n)
            i = next_i if next_i is not None else i + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _try_parse(raw: str) -> dict[str, Any]:
    """Parse possibly-fenced/malformed JSON into a dict; {} on any failure."""
    text = _strip_fences(raw)
    block = _first_json_block(text)
    data: dict[str, Any] = {}
    if block:
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            try:
                data = json.loads(_repair_json(block))
            except json.JSONDecodeError:
                data = {}
    return data if isinstance(data, dict) else {}


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v) for v in value if isinstance(v, (str, int, float))][:20]


def parse_search_plan_json(raw: str) -> SearchPlan:
    """Parse the LLM plan response into a SearchPlan. Never raises."""
    data = _try_parse(raw)
    return SearchPlan(
        keywords=_as_str_list(data.get("keywords")),
        queries=_as_str_list(data.get("queries")),
        languages=_as_str_list(data.get("languages")),
        notes=str(data.get("notes", "")),
    )


def parse_assess_json(raw: str) -> tuple[list[tuple[str, int, str]], str]:
    """Parse the LLM assessment response.

    Returns ``(assessments, summary)`` where ``assessments`` is a list of
    ``(full_name, score, reason)`` triples. Never raises.
    """
    data = _try_parse(raw)
    assessments: list[tuple[str, int, str]] = []
    repos_raw = data.get("repos")
    if isinstance(repos_raw, list):
        for item in repos_raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("full_name", ""))
            if not name:
                continue
            try:
                score = int(item.get("score", 0))
            except (TypeError, ValueError):
                score = 0
            score = max(0, min(100, score))
            assessments.append((name, score, str(item.get("reason", "") or "")))
    return assessments, str(data.get("summary", ""))


def _fallback_plan(idea: str) -> SearchPlan:
    """Minimal SearchPlan from the raw idea when the LLM output is unusable."""
    tokens = [t for t in re.split(r"[\s,，。;；、+]+", idea.strip()) if t]
    queries = [f'"{idea.strip()}"']
    if tokens:
        queries.append(" ".join(tokens[:5]))
    return SearchPlan(
        keywords=tokens[:5],
        queries=queries,
        languages=[],
        notes="LLM 返回格式异常，已退化为基于原始关键词的搜索策略。",
    )


# ---------------------------------------------------------------------------
# Async client
# ---------------------------------------------------------------------------


class LLMClient:
    """Async OpenAI-compatible client for plan generation and assessment."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.from_env()
        # Accessing llm_api_key raises ConfigError here when the key is missing.
        self._client = AsyncOpenAI(
            api_key=self.settings.llm_api_key,
            base_url=self.settings.llm_base_url,
            timeout=60.0,
            max_retries=2,
        )

    async def close(self) -> None:
        await self._client.close()

    async def _chat_json(self, system: str, user: str) -> str:
        """One chat completion; returns the raw assistant text."""
        try:
            resp = await self._client.chat.completions.create(
                model=self.settings.llm_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.2,
            )
        except Exception as exc:
            raise LLMError(f"LLM 调用失败: {exc}") from exc
        content = getattr(resp.choices[0].message, "content", None)
        return content or ""

    async def generate_plan(self, idea: str) -> SearchPlan:
        system = (
            "You translate a user's idea (which may be in Chinese) into a GitHub "
            "search strategy. Respond with STRICT JSON only, no prose, no markdown "
            "fences:\n"
            '{"keywords": ["..."], "queries": ["..."], "languages": ["..."], "notes": "..."}\n'
            "Rules:\n"
            "- keywords: 3-5 English keywords capturing the idea.\n"
            "- queries: 3-4 full GitHub search query strings using operators such as "
            "in:name, in:description, in:readme, language:, stars:>, pushed:>, topic:, "
            "and quoted phrases.\n"
            "- languages: 1-3 suggested programming languages (empty list if none).\n"
            "- notes: a short English note about the strategy."
        )
        raw = await self._chat_json(system, f"User idea: {idea}")
        plan = parse_search_plan_json(raw)
        return plan if plan.queries else _fallback_plan(idea)

    async def assess(
        self, idea: str, repos: list[Repo]
    ) -> tuple[list[tuple[int, str]], str]:
        """Assess up to 25 repos; returns (score, reason) aligned to input order."""
        if not repos:
            return [], ""
        selected = repos[:25]
        compact = [
            {
                "full_name": r.full_name,
                "description": (r.description or "")[:200],
                "language": r.language,
                "stars": r.stars,
                "topics": r.topics[:8],
            }
            for r in selected
        ]
        system = (
            "You assess how well GitHub repositories match a user's idea. "
            "Respond with STRICT JSON only, no prose, no markdown fences:\n"
            '{"repos": [{"full_name": "...", "score": 0-100, "reason": "..."}], '
            '"summary": "..."}\n'
            "Rules:\n"
            "- Include EVERY repo from the input, using its exact full_name.\n"
            "- score: an integer 0-100 reflecting how well the repo matches the idea.\n"
            "- reason: one short sentence in Chinese explaining the score.\n"
            "- summary: a 2-3 sentence overall landscape summary in Chinese."
        )
        raw = await self._chat_json(
            system,
            f"User idea: {idea}\n\nRepositories:\n"
            + json.dumps(compact, ensure_ascii=False),
        )
        parsed, summary = parse_assess_json(raw)
        by_name = {name: (score, reason) for name, score, reason in parsed}
        aligned = [by_name.get(r.full_name, (0, "")) for r in selected]
        return aligned, summary