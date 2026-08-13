"""Persistent search-history storage for the TUI."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from idea_search.models import SearchResult

MAX_ENTRIES = 100


@dataclass
class HistoryEntry:
    """One stored search: id, ISO-8601 UTC timestamp, and the result."""

    id: int
    timestamp: str
    result: SearchResult


class HistoryStore:
    """JSON-file-backed history, newest first, deduped by idea."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or Path.home() / ".idea-search" / "history.json"

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> list[HistoryEntry]:
        """Return all entries newest first; missing/corrupt file -> []."""
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        entries: list[HistoryEntry] = []
        for item in data:
            try:
                entries.append(
                    HistoryEntry(
                        id=int(item["id"]),
                        timestamp=str(item["timestamp"]),
                        result=SearchResult.from_dict(item["result"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        entries.sort(key=lambda e: (e.timestamp, e.id), reverse=True)
        return entries

    def add(self, result: SearchResult) -> HistoryEntry:
        """Store a result, deduping by idea; returns the stored entry."""
        entries = self.load()
        existing = next(
            (e for e in entries if e.result.idea == result.idea), None
        )
        if existing is not None:
            existing.result = result
            existing.timestamp = datetime.now(timezone.utc).isoformat()
            entry = existing
        else:
            entry = HistoryEntry(
                id=max((e.id for e in entries), default=0) + 1,
                timestamp=datetime.now(timezone.utc).isoformat(),
                result=result,
            )
            entries.append(entry)
        entries.sort(key=lambda e: (e.timestamp, e.id), reverse=True)
        del entries[MAX_ENTRIES:]
        self._write(entries)
        return entry

    def delete(self, entry_id: int) -> bool:
        """Remove the entry with the given id; True if one was removed."""
        entries = self.load()
        remaining = [e for e in entries if e.id != entry_id]
        if len(remaining) == len(entries):
            return False
        self._write(remaining)
        return True

    def clear(self) -> None:
        """Delete the history file; no-op if it does not exist."""
        self._path.unlink(missing_ok=True)

    def _write(self, entries: list[HistoryEntry]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = [
            {"id": e.id, "timestamp": e.timestamp, "result": e.result.to_dict()}
            for e in entries
        ]
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)