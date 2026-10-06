"""Remember which items have been reported, one JSON Lines file per source.

Append-only, so the daily commit is a small, readable diff. Raw text fetched for
Claude (`text`) is never stored.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

TRANSIENT_FIELDS = ("text",)


def was_reported(item: dict, days_back: int = 14) -> bool:
    """Whether a stored item was reported, or only recorded as seen because it was older
    than the look-back window. Items stored before the `reported` flag existed are
    judged by their date against when they were first seen."""
    if "reported" in item:
        return bool(item["reported"])
    seen, dated = (item.get("first_seen") or "")[:10], (item.get("date") or "")[:10]
    if not seen or not dated:
        return True
    try:
        return date.fromisoformat(dated) >= date.fromisoformat(seen) - timedelta(days=days_back)
    except ValueError:
        return True


def is_notable(item: dict) -> bool:
    """Whether a record belongs in Top stories and the briefing: high or medium, except
    that records from regional sources (bodies outside Salt Lake County) count only
    when high. The rest stay in All records."""
    if item.get("regional"):
        return item.get("importance") == "high"
    return item.get("importance") in ("high", "medium")


def _clean(item: dict) -> dict:
    return {k: v for k, v in item.items() if k not in TRANSIENT_FIELDS}


class SourceStore:
    def __init__(self, path: Path):
        self.path = path
        self.items: dict[str, dict] = {}
        if path.exists():
            with path.open(encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        item = json.loads(line)
                        self.items[item["id"]] = item

    def __contains__(self, item_id: str) -> bool:
        return item_id in self.items

    def __len__(self) -> int:
        return len(self.items)

    def add(self, items: list[dict]) -> None:
        if not items:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.path.open("a", encoding="utf-8") as f:
            for item in items:
                item = _clean(item)
                item.setdefault("first_seen", now)
                self.items[item["id"]] = item
                f.write(json.dumps(item, sort_keys=True) + "\n")

    def all(self) -> list[dict]:
        return [dict(i) for i in self.items.values()]

    def update(self, items: list[dict], fields: tuple[str, ...]) -> None:
        """Merge these fields from items into stored ones and rewrite the file."""
        for item in items:
            stored = self.items.get(item["id"])
            if stored is not None:
                stored.update({f: item[f] for f in fields if f in item})
        tmp = self.path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for item in self.items.values():
                f.write(json.dumps(item, sort_keys=True) + "\n")
        tmp.replace(self.path)


class Store:
    """All sources' stores, under data/<source>.jsonl."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self._stores: dict[str, SourceStore] = {}

    def source(self, name: str) -> SourceStore:
        if name not in self._stores:
            self._stores[name] = SourceStore(self.data_dir / f"{name}.jsonl")
        return self._stores[name]

    def everything(self) -> list[dict]:
        """Every stored item from every source on disk (for cross-referencing)."""
        for path in sorted(self.data_dir.glob("*.jsonl")):
            self.source(path.stem)
        return [i for s in self._stores.values() for i in s.all()]
