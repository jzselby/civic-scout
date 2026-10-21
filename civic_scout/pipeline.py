"""One run: fetch every source, keep what's new, rate it, connect it, brief it, publish it."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import analyze, link, report
from .config import Config
from .http import Http
from .sources import ALL, selected
from .store import Store, is_notable, was_reported

log = logging.getLogger(__name__)

TZ = ZoneInfo("America/Denver")


def today() -> date:
    return datetime.now(TZ).date()


def apply_rules(item: dict) -> None:
    """Deterministic floors, so big items surface even without Claude, and ceilings."""
    if item["source"] == "warn" and (item.get("workers") or 0) >= 50:
        item["importance"] = "high"
        item.setdefault("category", "Jobs / economy")
    cap = item.get("max_importance")
    if cap and analyze.LEVEL.get(item.get("importance"), 0) > analyze.LEVEL[cap]:
        item["importance"] = cap


@dataclass
class RunResult:
    day: date
    items: list[dict] = field(default_factory=list)
    old: list[dict] = field(default_factory=list)
    # New records Claude should have rated but didn't (an API error): not reported and
    # not marked seen, so the next run retries them.
    held: list[dict] = field(default_factory=list)
    health: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    connections: dict[str, list[dict]] = field(default_factory=dict)
    briefing: str | None = None
    # Everything reported today, this run's records included: the briefing covers the
    # whole day, so several runs in one day still make one briefing.
    today_items: list[dict] = field(default_factory=list)
    report: str = ""


def local_date(stamp: str | None) -> date | None:
    """A first_seen timestamp (UTC ISO) as a Mountain-time date."""
    if not stamp:
        return None
    try:
        dt = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    return (dt.astimezone(TZ) if dt.tzinfo else dt).date()


def forget_day(store: Store, day: date) -> int:
    """Drop the records first seen on this day from the store, in memory only, so a dry
    run finds and rates them again as if the day's run hadn't happened."""
    n = 0
    for path in sorted(store.data_dir.glob("*.jsonl")):
        items = store.source(path.stem).items
        for item_id in [k for k, i in items.items() if local_date(i.get("first_seen")) == day]:
            del items[item_id]
            n += 1
    return n


def reported_on(store: Store, day: date, new_items: list[dict] = ()) -> list[dict]:
    """Every record reported on this day (by first_seen, Mountain time), plus new ones."""
    out = {link.item_key(i): i for i in store.everything()
           if local_date(i.get("first_seen")) == day and was_reported(i) and not i.get("hidden")}
    out.update({link.item_key(i): i for i in new_items if not i.get("hidden")})
    return list(out.values())


def day_connections(store: Store, day: date, items: list[dict]) -> dict[str, list[dict]]:
    return link.connections(items, store.everything() + items, day)


def results_to_check(store: Store, day: date) -> list[dict]:
    """High and medium agenda items (Salt Lake bodies, not regional ones) from meetings
    held since the last weekday, so reporters check what was decided: notices show
    only what a body planned to do."""
    start = day - timedelta(days=1)
    while start.weekday() >= 5:
        start -= timedelta(days=1)
    held = [i for i in store.source("pmn").items.values()
            if start.isoformat() <= (i.get("date") or "") < day.isoformat()
            and is_notable(i) and not i.get("regional") and not i.get("hidden")]
    return sorted(held, key=lambda i: (i.get("importance") != "high", i["date"], i.get("org") or ""))


def results_section(items: list[dict]) -> str:
    lines = ["**Results to check**: these meetings have happened; the records show only the agenda."]
    for i in items:
        lines.append(f"- {i.get('org') or 'Public body'} ({date.fromisoformat(i['date']):%b %d}): "
                     f"{i.get('headline') or i.get('title')} ([notice]({i.get('url')}))")
    return "\n".join(lines)


def day_briefing(cfg: Config, store: Store, day: date, items: list[dict], labels: dict[str, str]) -> str | None:
    briefing = analyze.brief(items, day_connections(store, day, items), labels, day, cfg.model)
    held = results_to_check(store, day)
    if not held:
        return briefing
    return "\n\n".join(p for p in (briefing, results_section(held)) if p)


def day_report(store: Store, day: date, items: list[dict], health: dict[str, str], briefing: str | None,
               labels: dict[str, str] | None = None) -> str:
    """The day's report: every record reported today, whichever run found it, so a
    repeat run rewrites the day's one report instead of adding a second."""
    labels = {**{s.name: s.label for s in ALL}, **(labels or {})}
    return report.build(day, items, labels, health, briefing, day_connections(store, day, items))


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
    attempted = use_claude and analyze.available()
    notes = analyze.rate(to_rate, guidance, cfg.model) if attempted else {}
    if attempted:
        result.held = [i for i in to_rate if link.item_key(i) not in notes]
        if result.held:
            log.warning("%d record(s) weren't rated; holding them for the next run", len(result.held))
            held = {link.item_key(i) for i in result.held}
            result.items = [i for i in result.items if link.item_key(i) not in held]
    for item in result.items:
        note = notes.get(link.item_key(item))
        if note:
            item.update(headline=note.headline, importance=note.importance, category=note.category,
                        why_it_matters=note.why_it_matters, names=note.names, places=note.places)
        apply_rules(item)
    if use_claude:
        changed = analyze.apply_review(result.items, analyze.review(result.items, result.labels, cfg.review_model))
        if changed:
            log.info("Editor review changed %d rating(s)", changed)
    result.connections = link.connections(result.items, store.everything(), result.day)
    result.today_items = reported_on(store, result.day, result.items)
    if use_claude:
        result.briefing = day_briefing(cfg, store, result.day, result.today_items, result.labels)
    result.report = day_report(store, result.day, result.today_items, result.health, result.briefing,
                               result.labels)
