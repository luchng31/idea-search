"""Shared data-model contract between the search pipeline and the TUI.

Both ``idea_search.service`` (backend) and ``idea_search.tui`` (frontend)
depend on this module. Do NOT change field names or types without
coordinating both sides.
"""

from __future__ import annotations

from dataclasses import dataclass, field


def _opt_str(value: object) -> str | None:
    """Coerce a JSON value to str, keeping None as None."""
    return None if value is None else str(value)


@dataclass
class SearchPlan:
    """The LLM-generated plan for how to search GitHub for an idea."""

    keywords: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict[str, object]:
        """Serialize to a JSON-compatible dict."""
        return {
            "keywords": list(self.keywords),
            "queries": list(self.queries),
            "languages": list(self.languages),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> SearchPlan:
        """Reconstruct from :meth:`to_dict` output."""
        return cls(
            keywords=[str(k) for k in data.get("keywords") or []],
            queries=[str(q) for q in data.get("queries") or []],
            languages=[str(l) for l in data.get("languages") or []],
            notes=str(data.get("notes") or ""),
        )


@dataclass
class Repo:
    """A single GitHub repository result with an LLM assessment."""

    full_name: str
    url: str
    description: str | None = None
    language: str | None = None
    stars: int = 0
    forks: int = 0
    updated_at: str | None = None
    topics: list[str] = field(default_factory=list)
    score: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        """Serialize to a JSON-compatible dict."""
        return {
            "full_name": self.full_name,
            "url": self.url,
            "description": self.description,
            "language": self.language,
            "stars": self.stars,
            "forks": self.forks,
            "updated_at": self.updated_at,
            "topics": list(self.topics),
            "score": self.score,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> Repo:
        """Reconstruct from :meth:`to_dict` output."""
        return cls(
            full_name=str(data["full_name"]),
            url=str(data["url"]),
            description=_opt_str(data.get("description")),
            language=_opt_str(data.get("language")),
            stars=int(data.get("stars") or 0),
            forks=int(data.get("forks") or 0),
            updated_at=_opt_str(data.get("updated_at")),
            topics=[str(t) for t in data.get("topics") or []],
            score=int(data.get("score") or 0),
            reason=str(data.get("reason") or ""),
        )


@dataclass
class SearchResult:
    """Everything the TUI needs to render: plan, hits and a summary."""

    idea: str
    plan: SearchPlan = field(default_factory=SearchPlan)
    repositories: list[Repo] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, object]:
        """Serialize to a JSON-compatible dict."""
        return {
            "idea": self.idea,
            "plan": self.plan.to_dict(),
            "repositories": [r.to_dict() for r in self.repositories],
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> SearchResult:
        """Reconstruct from :meth:`to_dict` output."""
        plan = (
            SearchPlan.from_dict(data["plan"])
            if isinstance(data.get("plan"), dict)
            else SearchPlan()
        )
        repositories = (
            [Repo.from_dict(r) for r in data["repositories"]]
            if isinstance(data.get("repositories"), list)
            else []
        )
        return cls(
            idea=str(data["idea"]),
            plan=plan,
            repositories=repositories,
            summary=str(data.get("summary") or ""),
        )


def repos_sorted(repositories: list[Repo]) -> list[Repo]:
    """Rank repositories by LLM score, then stars as a tiebreaker."""
    return sorted(
        repositories,
        key=lambda r: (r.score, r.stars),
        reverse=True,
    )