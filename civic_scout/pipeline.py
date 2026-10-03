"""One run: fetch every source, keep what's new, rate it, connect it, brief it, publish it."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import analyze, link, report
from .config import Config
from .http import Http
from .sources import selected
from .store import Store

log = logging.getLogger(__name__)

TZ = ZoneInfo("America/Denver")


def today() -> date:
    return datetime.now(TZ).date()


def apply_rules(item: dict) -> None:
    """Deterministic floors, so big items surface even without Claude."""
    if item["source"] == "warn" and (item.get("workers") or 0) >= 50:
        item["importance"] = "high"
        item.setdefault("category", "Jobs / economy")


@dataclass
class RunResult:
    day: date
    items: list[dict] = field(default_factory=list)
    old: list[dict] = field(default_factory=list)
    health: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    connections: dict[str, list[dict]] = field(default_factory=dict)
    briefing: str | None = None
    report: str = ""


def collect(cfg: Config, store: Store, http: Http, day: date) -> RunResult:
    result = RunResult(day=day)
    since = (day - timedelta(days=cfg.days_back)).isoformat()
    for source in selected(cfg.sources):
        result.labels[source.name] = source.label
        seen = set(store.source(source.name).items)
        try:
            fetched = source.fetch(cfg, http, seen)
        except Exception as exc:
            log.exception("Source %s failed", source.name)
            result.health[source.name] = f"FAILED: {type(exc).__name__}: {exc}"[:300]
            continue
        new, old = [], []
        for item in fetched:
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            item["source"] = source.name
            # Older than the look-back window: remember it, but don't report it.
            (old if item.get("date") and item["date"] < since else new).append(item)
        result.items += new
        result.old += old
        result.health[source.name] = f"ok ({len(fetched)} fetched)"
        log.info("%s: %d fetched, %d new, %d older than %s (recorded, not reported)",
                 source.name, len(fetched), len(new), len(old), since)
    return result


def enrich(cfg: Config, store: Store, result: RunResult, use_claude: bool = True) -> None:
    sources = selected(cfg.sources)
    guidance = "\n\n".join(s.guidance for s in sources)
    to_rate = [i for i in result.items if not i.get("prerated") and not i.get("hidden")]
    notes = analyze.rate(to_rate, guidance, cfg.model) if use_claude else {}
    for item in result.items:
        note = notes.get(link.item_key(item))
        if note:
            item.update(headline=note.headline, importance=note.importance, category=note.category,
                        why_it_matters=note.why_it_matters, names=note.names, places=note.places)
        apply_rules(item)
    result.connections = link.connections(result.items, store.everything(), result.day)
    if use_claude:
        result.briefing = analyze.brief(result.items, result.connections, result.labels, result.day, cfg.model)
    result.report = report.build(result.day, result.items, result.labels, result.health, result.briefing,
                                 result.connections)
