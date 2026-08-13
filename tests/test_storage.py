"""Tests for the persistent history store and model serialization."""

from __future__ import annotations

import json
from pathlib import Path

from idea_search.models import Repo, SearchPlan, SearchResult
from idea_search.storage import MAX_ENTRIES, HistoryStore


def make_result(idea: str, n_repos: int = 2) -> SearchResult:
    return SearchResult(
        idea=idea,
        plan=SearchPlan(
            keywords=[f"kw-{idea}"],
            queries=[f"{idea} in:description"],
            languages=["python"],
            notes="note",
        ),
        repositories=[
            Repo(
                full_name=f"owner/repo{i}",
                url=f"https://github.com/owner/repo{i}",
                description=None if i == 0 else "desc",
                language="Python" if i == 0 else None,
                stars=10 + i,
                forks=2,
                updated_at="2026-01-01T00:00:00+00:00",
                topics=["a", "b"] if i == 0 else [],
                score=90 - i,
                reason="reason",
            )
            for i in range(n_repos)
        ],
        summary="summary",
    )


def test_default_path() -> None:
    store = HistoryStore()
    assert store.path == Path.home() / ".idea-search" / "history.json"


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "history.json")
    assert store.load() == []


def test_load_corrupt_file_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    path.write_bytes(b"\x00\xff\xfe garbage \x01")
    store = HistoryStore(path)
    assert store.load() == []


def test_add_and_load_round_trip(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "history.json")
    result = make_result("idea-one")
    entry = store.add(result)
    assert entry.id == 1
    loaded = store.load()
    assert len(loaded) == 1
    assert loaded[0].id == 1
    assert loaded[0].result == result


def test_newest_first(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "history.json")
    store.add(make_result("first"))
    store.add(make_result("second"))
    loaded = store.load()
    assert [e.result.idea for e in loaded] == ["second", "first"]
    assert [e.id for e in loaded] == [2, 1]


def test_dedup_by_idea_keeps_id(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "history.json")
    first = store.add(make_result("same-idea"))
    second = store.add(make_result("same-idea", n_repos=1))
    assert second.id == first.id == 1
    loaded = store.load()
    assert len(loaded) == 1
    assert loaded[0].result == second.result
    assert loaded[0].timestamp == second.timestamp


def test_max_entries_cap(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "history.json")
    for i in range(105):
        store.add(make_result(f"idea-{i:03d}"))
    loaded = store.load()
    assert len(loaded) == MAX_ENTRIES == 100
    assert loaded[0].result.idea == "idea-104"
    assert loaded[-1].result.idea == "idea-005"
    assert all(e.id >= 6 for e in loaded)


def test_delete_existing_entry(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "history.json")
    store.add(make_result("keep"))
    victim = store.add(make_result("remove"))
    assert store.delete(victim.id) is True
    loaded = store.load()
    assert [e.result.idea for e in loaded] == ["keep"]
    data = json.loads(store.path.read_text(encoding="utf-8"))
    assert len(data) == 1
    assert not list(tmp_path.glob("*.tmp"))


def test_delete_missing_id_returns_false(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "history.json")
    store.add(make_result("idea"))
    assert store.delete(999) is False
    loaded = store.load()
    assert [e.result.idea for e in loaded] == ["idea"]


def test_delete_on_empty_or_missing_file_returns_false(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "history.json")
    assert store.delete(1) is False
    assert not store.path.exists()
    store.add(make_result("idea"))
    store.clear()
    assert store.delete(1) is False


def test_delete_then_add_dedups_with_survivors(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "history.json")
    store.add(make_result("survivor"))
    victim = store.add(make_result("victim"))
    store.delete(victim.id)
    store.add(make_result("survivor", n_repos=1))
    loaded = store.load()
    assert len(loaded) == 1
    assert loaded[0].result.idea == "survivor"
    assert loaded[0].id == 1


def test_clear(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "history.json")
    store.add(make_result("idea"))
    store.clear()
    assert store.load() == []
    assert not store.path.exists()
    store.clear()  # no-op on missing file


def test_atomic_write_leaves_valid_file(tmp_path: Path) -> None:
    store = HistoryStore(tmp_path / "history.json")
    store.add(make_result("idea"))
    data = json.loads(store.path.read_text(encoding="utf-8"))
    assert len(data) == 1
    assert data[0]["result"]["idea"] == "idea"
    assert not list(tmp_path.glob("*.tmp"))


def test_serialization_round_trip_plan() -> None:
    plan = SearchPlan(keywords=["a"], queries=["b"], languages=["c"], notes="n")
    assert SearchPlan.from_dict(plan.to_dict()) == plan


def test_serialization_round_trip_repo() -> None:
    repo = Repo(
        full_name="o/r",
        url="https://github.com/o/r",
        description=None,
        language=None,
        stars=5,
        forks=1,
        updated_at=None,
        topics=["t"],
        score=42,
        reason="why",
    )
    assert Repo.from_dict(repo.to_dict()) == repo


def test_serialization_round_trip_repo_defaults() -> None:
    repo = Repo(full_name="o/r", url="https://github.com/o/r")
    assert Repo.from_dict(repo.to_dict()) == repo


def test_serialization_round_trip_result() -> None:
    result = make_result("idea", n_repos=3)
    assert SearchResult.from_dict(result.to_dict()) == result


def test_serialization_round_trip_result_defaults() -> None:
    result = SearchResult(idea="bare")
    assert SearchResult.from_dict(result.to_dict()) == result