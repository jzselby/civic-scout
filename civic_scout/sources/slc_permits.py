"""SLC commercial building permits and Planning applications, imported read-only
from the slcbuilding project's committed data (which scrapes the city's Accela
portal and rates each record). Nothing here writes to that project.
"""

from __future__ import annotations

import json
import logging
import re

from ..config import Config
from ..http import Http
from .text_utils import iso

log = logging.getLogger(__name__)

# Trade permits for work on a project or tenant that already has a record are routine:
# they only repeat what the first record said. The first record about a place stays.
TRADE_TYPES = {"Commercial Electrical", "Commercial Mechanical", "Commercial Plumbing",
               "Commercial Fire Sprinkler", "Commercial Fire Alarm", "Commercial SolarPV",
               "Commercial Roofing", "Commercial Fencing", "Commercial Low Voltage"}
BIG_JOB = 1_000_000
_TEST = re.compile(r"\btest\b", re.I)


def place_keys(rec: dict) -> set[str]:
    """The street address (without suite or city) and business name a record is about."""
    keys = set()
    if rec.get("address"):
        keys.add("a:" + rec["address"].split(",")[0].strip().upper())
    if rec.get("business"):
        keys.add("b:" + rec["business"].strip().upper())
    return keys


def is_placeholder(rec: dict) -> bool:
    text = f"{rec.get('scope') or ''} {rec.get('description') or ''}"
    return (rec.get("status") or "").lower() == "void" or bool(_TEST.search(text))


def convert(rec: dict) -> dict:
    value = rec.get("job_value")
    facts = [f"Record type: {rec.get('record_type')}" if rec.get("record_type") else "",
             f"Job value: ${value:,.0f}" if isinstance(value, (int, float)) else "",
             f"Status: {rec.get('status')}" if rec.get("status") else ""]
    return {
        "id": rec["record_number"],
        "title": rec.get("scope") or rec.get("description") or rec.get("record_type") or rec["record_number"],
        "date": iso(rec.get("date")),
        "url": rec.get("detail_url"),
        "org": rec.get("business") or rec.get("applicant"),
        "place": rec.get("address"),
        "details": "; ".join(f for f in facts if f),
        "headline": rec.get("scope"),
        "importance": rec.get("importance") or "low",
        "category": "Development / housing",
        "why_it_matters": rec.get("why_it_matters") or "",
        "names": [n for n in (rec.get("business"), rec.get("applicant")) if n],
        "places": [rec["address"]] if rec.get("address") else [],
        "prerated": True,
    }


class SlcPermits:
    name = "slc_permits"
    label = "SLC permits & planning (slcbuilding)"
    default_enabled = True
    guidance = """\
Source "slc_permits": Salt Lake City commercial building permits and Planning
applications, already rated by another pipeline. Use them for context and
connections; they are not re-rated. A trade permit (electrical, plumbing, mechanical,
fire, solar, roofing) at a place that already has a record, and test or void records,
come in as low."""

    def fetch(self, cfg: Config, http: Http, seen: set[str]) -> list[dict]:
        records = [json.loads(line) for line in http.text(cfg.slcbuilding_url).splitlines() if line.strip()]
        records = [r for r in records if r.get("record_number")]
        known = set().union(*(place_keys(r) for r in records if r["record_number"] in seen))
        items = []
        for rec in sorted((r for r in records if r["record_number"] not in seen),
                          key=lambda r: (iso(r.get("date")) or "", r["record_number"])):
            item = convert(rec)
            value = rec.get("job_value") if isinstance(rec.get("job_value"), (int, float)) else 0
            if is_placeholder(rec) or (rec.get("record_type") in TRADE_TYPES and value < BIG_JOB
                                       and place_keys(rec) & known):
                item["importance"] = "low"
            known |= place_keys(rec)
            items.append(item)
        return items

    def probe(self, cfg: Config, http: Http) -> dict[str, bytes]:
        return {"slcbuilding-head.jsonl": "\n".join(http.text(cfg.slcbuilding_url).splitlines()[:5]).encode()}
