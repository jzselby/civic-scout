"""New restaurants, bars and cafes, gathered from every source.

When Claude rates a record it lists the eating and drinking places the record shows are
coming (`openings` on the record). A place usually turns up several times: a building
permit, a business license, a liquor license. Records are grouped into one place by
normalized address or name, so the briefing gets one line per place and the sheet's
Openings tab can show each place's steps toward opening.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from .link import item_key, normalize_address, normalize_name

# Areas in the order the briefing lists them. "elsewhere" (outside Salt Lake County)
# goes only to the sheet.
AREAS = ("Salt Lake City", "Salt Lake County", "elsewhere")
BRIEF_AREAS = AREAS[:2]
STAGE_LABELS = {
    "planning": "planning application",
    "building permit": "building permit",
    "trade permit": "trade permit",
    "business license": "business license",
    "liquor license": "liquor license",
    "health permit": "health permit",
    "opening": "opening announced",
}


TZ = ZoneInfo("America/Denver")


def _seen(item: dict, today: date) -> str:
    """The Mountain-time day a record was first seen (today for a record not stored yet)."""
    try:
        dt = datetime.fromisoformat(item["first_seen"])
    except (KeyError, TypeError, ValueError):
        return today.isoformat()
    return (dt.astimezone(TZ) if dt.tzinfo else dt).date().isoformat()


def sightings(items: list[dict], today: date) -> list[dict]:
    """One entry per (record, place) for every tagged record, oldest first."""
    out = []
    for item in items:
        if item.get("hidden"):
            continue
        for o in item.get("openings") or []:
            if not o.get("name"):
                continue
            out.append({**o, "seen": _seen(item, today), "date": item.get("date"), "url": item.get("url"),
                        "source": item["source"], "key": item_key(item)})
    return sorted(out, key=lambda s: (s["seen"], s.get("date") or "", s["key"]))


def _keys(s: dict) -> set[tuple[str, str]]:
    keys = set()
    if a := normalize_address(s.get("address")):
        keys.add(("address", a))
    if n := normalize_name(s.get("name")):
        keys.add(("name", n))
    return keys


def places(items: list[dict], today: date) -> list[dict]:
    """Sightings grouped into places, each {name, kind, address, city, area, sightings},
    in the order first seen. A sighting joins every place it shares an address or a
    name with, so a license naming both a permit's address and a liquor license's
    business name makes the three one place."""
    groups: list[dict] = []
    for s in sightings(items, today):
        keys = _keys(s)
        matched = [g for g in groups if g["keys"] & keys] if keys else []
        group = matched[0] if matched else {"keys": set(), "sightings": []}
        if not matched:
            groups.append(group)
        for other in matched[1:]:
            group["keys"] |= other["keys"]
            group["sightings"] += other["sightings"]
            groups.remove(other)
        group["keys"] |= keys
        group["sightings"].append(s)
    for g in groups:
        g["sightings"].sort(key=lambda s: (s["seen"], s.get("date") or "", s["key"]))
    out = []
    for g in groups:
        latest = g["sightings"][-1]

        def best(field: str) -> str:
            # The latest record that gives the field: later records name the place better.
            return next((s[field] for s in reversed(g["sightings"]) if s.get(field)), "")

        out.append({"name": latest["name"], "kind": best("kind"), "address": best("address"),
                    "city": best("city"), "area": best("area") or "elsewhere",
                    "first_seen": g["sightings"][0]["seen"], "last_seen": latest["seen"],
                    "sightings": g["sightings"]})
    return out


def is_new(item: dict, earlier: list[dict], today: date) -> bool:
    """Whether the record names a place no earlier record did."""
    known = set().union(*(_keys(s) for s in sightings(earlier, today)))
    return any(not (_keys(s) & known) for s in sightings([item], today) if _keys(s))


def _date(iso: str) -> str:
    d = date.fromisoformat(iso[:10])
    return f"{d:%b} {d.day}"


def _where(p: dict) -> str:
    return ", ".join(x for x in (p.get("address"), p.get("city")) if x)


def section(items: list[dict], today: date, labels: dict[str, str]) -> str | None:
    """The briefing's "New restaurants & bars" section: one line per Salt Lake place that
    took a new step today, Salt Lake City first, with what today's records show and when
    the place first turned up. A record repeating a step the place already showed (a
    second copy of council minutes about the same liquor license, another trade permit)
    goes only to the sheet. None when there are none."""
    day = today.isoformat()
    todays = [p for p in places(items, today)
              if p["last_seen"] == day and p["area"] in BRIEF_AREAS and _new_steps(p, day)]
    if not todays:
        return None
    todays.sort(key=lambda p: (BRIEF_AREAS.index(p["area"]), p["name"].upper()))
    lines = ["## New restaurants & bars", ""]
    for p in todays:
        head = f"**{p['name']}**" + (f", {p['kind']}" if p.get("kind") else "")
        if where := _where(p):
            head += f", {where}"
        steps = []
        for s in _new_steps(p, day):
            label = STAGE_LABELS.get(s.get("stage"), s.get("stage") or "record")
            source = labels.get(s["source"], s["source"])
            step = f"{label} ([{source}]({s['url']}))" if s.get("url") else f"{label} ({source})"
            if step not in steps:
                steps.append(step)
        line = f"- {head}: {'; '.join(steps)}."
        if p["first_seen"] < day:
            first = p["sightings"][0]
            line += (f" First seen {_date(first['seen'])}"
                     f" ({STAGE_LABELS.get(first.get('stage'), first.get('stage') or 'record')}).")
        lines.append(line)
    return "\n".join(lines)


def _new_steps(place: dict, day: str) -> list[dict]:
    """The place's sightings from today whose stage it hadn't shown on an earlier day."""
    before = {s.get("stage") for s in place["sightings"] if s["seen"] < day}
    return [s for s in place["sightings"] if s["seen"] == day and s.get("stage") not in before]


def add_section(briefing: str | None, section_md: str | None) -> str | None:
    """Put the section right after the briefing's Top stories (before the next heading),
    or first if there is no Top stories section."""
    if not section_md:
        return briefing
    if not briefing:
        return section_md
    lines = briefing.splitlines()
    top = next((n for n, l in enumerate(lines) if _is_heading(l) and "top stories" in l.lower()), None)
    if top is None:
        return section_md + "\n\n" + briefing
    nxt = next((n for n in range(top + 1, len(lines)) if _is_heading(lines[n])), len(lines))
    before = "\n".join(lines[:nxt]).rstrip()
    after = "\n".join(lines[nxt:]).strip()
    return "\n\n".join(p for p in (before, section_md, after) if p)


def _is_heading(line: str) -> bool:
    s = line.strip()
    # "## Coming up", or a line that is just "**Coming up**" or starts "**Coming up**:".
    return s.startswith("#") or (s.startswith("**") and "**" in s[2:]
                                 and s[2:].split("**", 1)[1][:1] in ("", ":"))
