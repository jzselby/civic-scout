"""Connect items across sources that share an address or a name.

A building permit, a liquor license on the commission's agenda and a new business
license at the same address are three views of one story. Matching is
deterministic: addresses and names are normalized, then compared exactly.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta

DIRECTIONS = {"NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W"}
SUFFIXES = {
    "STREET": "ST", "AVENUE": "AVE", "AV": "AVE", "BOULEVARD": "BLVD", "DRIVE": "DR", "ROAD": "RD",
    "LANE": "LN", "COURT": "CT", "CIRCLE": "CIR", "PLACE": "PL", "PARKWAY": "PKWY", "HIGHWAY": "HWY",
    "TERRACE": "TER", "WAY": "WAY",
}
_UNIT = re.compile(r"\s+(#|UNIT|STE|SUITE|APT|BLDG)\b.*$")

NAME_NOISE = {"LLC", "L.L.C", "INC", "INCORPORATED", "CO", "CORP", "CORPORATION", "COMPANY", "LTD", "LP",
              "LLP", "PLLC", "PC", "THE", "OF", "DBA", "AND", "&"}
# Names too generic to connect on.
NAME_STOP = {"SALT LAKE CITY", "SALT LAKE COUNTY", "STATE OF UTAH", "UTAH", "CITY", "COUNTY",
             "PLANNING COMMISSION", "CITY COUNCIL", "OWNER", "APPLICANT", "UNKNOWN", "NONE"}


# A city name ends the street part of an address written without commas.
_CITY = re.compile(r"\s(SALT LAKE CITY|SOUTH SALT LAKE|WEST VALLEY CITY|WEST JORDAN|SOUTH JORDAN|"
                   r"NORTH SALT LAKE|MILLCREEK|MURRAY|SANDY|DRAPER|TAYLORSVILLE|HOLLADAY|MIDVALE|"
                   r"COTTONWOOD HEIGHTS|SLC)\b.*$")
_STATE_ZIP = re.compile(r"\s(UT|UTAH)?\s*\d{5}(-\d{4})?\s*$|\s(UT|UTAH)\s*$")


def normalize_address(raw: str | None) -> str | None:
    """'1124 East 100 South, Unit 3, Salt Lake City UT 84102' -> '1124 E 100 S'."""
    if not raw:
        return None
    s = " " + " ".join(raw.upper().split(",")[0].split())
    s = _STATE_ZIP.sub("", s)
    s = _CITY.sub("", s)
    s = re.sub(r"[^\w\s#]", " ", s)
    s = _UNIT.sub("", s)
    words = [DIRECTIONS.get(w, SUFFIXES.get(w, w)) for w in s.split()]
    if len(words) < 2 or not words[0].isdigit():
        return None
    return " ".join(words[:5])


def normalize_name(raw: str | None) -> str | None:
    if not raw:
        return None
    words = [w for w in re.sub(r"[^\w\s&]", " ", raw.upper()).split() if w not in NAME_NOISE]
    name = " ".join(words)
    if len(name) < 4 or name in NAME_STOP:
        return None
    return name


def keys_for(item: dict) -> set[tuple[str, str]]:
    """('address', normalized) and ('name', normalized) keys an item can be matched on."""
    keys: set[tuple[str, str]] = set()
    for place in [item.get("place"), *(item.get("places") or [])]:
        if n := normalize_address(place):
            keys.add(("address", n))
    for name in [item.get("org"), *(item.get("names") or [])]:
        if n := normalize_name(name):
            keys.add(("name", n))
    return keys


def item_key(item: dict) -> str:
    return f"{item['source']}:{item['id']}"


def _recent(item: dict, cutoff: date) -> bool:
    seen = (item.get("date") or item.get("first_seen") or "")[:10]
    try:
        return date.fromisoformat(seen) >= cutoff
    except ValueError:
        return True


def connections(new_items: list[dict], all_items: list[dict], today: date, days: int = 365) -> dict[str, list[dict]]:
    """For each new item, items from *other* sources (last `days`) sharing an address or name.

    Matches within one source are left out: they're mostly one project's many
    trade permits, or one body's many agendas. Public bodies themselves (an item's `org` on agenda notices) are excluded from
    name matching, since every notice from one body shares that name.
    """
    cutoff = today - timedelta(days=days)
    bodies = {normalize_name(i.get("org")) for i in all_items + new_items if i.get("source") == "pmn"}
    index: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for item in all_items + new_items:
        if item.get("hidden") or not _recent(item, cutoff):
            continue
        for k in keys_for(item):
            if k[0] == "name" and k[1] in bodies:
                continue
            index[k].append(item)
    # Keys shared by very many items (a common street name, a big agency) carry no signal.
    out: dict[str, list[dict]] = {}
    for item in new_items:
        if item.get("hidden"):
            continue
        matches: dict[str, dict] = {}
        for k in keys_for(item):
            if k[0] == "name" and k[1] in bodies:
                continue
            others = [o for o in index.get(k, []) if o["source"] != item["source"]]
            if len(others) > 15:
                continue
            for other in others:
                entry = matches.setdefault(item_key(other), {
                    "key": item_key(other), "source": other["source"], "title": other.get("title"),
                    "date": other.get("date"), "url": other.get("url"), "via": [],
                })
                entry["via"].append(f"{k[0]} {k[1]}")
        if matches:
            out[item_key(item)] = list(matches.values())
    return out
