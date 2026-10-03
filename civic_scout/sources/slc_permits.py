"""SLC commercial building permits and Planning applications, imported read-only
from the slcbuilding project's committed data (which scrapes the city's Accela
portal and rates each record). Nothing here writes to that project.
"""

from __future__ import annotations

import json
import logging

from ..config import Config
from ..http import Http
from .text_utils import iso

log = logging.getLogger(__name__)


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
connections; they are not re-rated."""

    def fetch(self, cfg: Config, http: Http, seen: set[str]) -> list[dict]:
        items = []
        for line in http.text(cfg.slcbuilding_url).splitlines():
            if line.strip():
                rec = json.loads(line)
                if rec.get("record_number") and rec["record_number"] not in seen:
                    items.append(convert(rec))
        return items

    def probe(self, cfg: Config, http: Http) -> dict[str, bytes]:
        return {"slcbuilding-head.jsonl": "\n".join(http.text(cfg.slcbuilding_url).splitlines()[:5]).encode()}
