"""Utah WARN notices: employers' advance notice of mass layoffs and plant closures.

Published by the Department of Workforce Services as an HTML table (date of
notice, company, location, affected workers).
"""

from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from ..config import Config
from ..http import Http
from .text_utils import iso, stable_id

log = logging.getLogger(__name__)

URL = "https://jobs.utah.gov/employer/business/warnnotices.html"


def _norm(header: str) -> str:
    h = header.lower()
    if "date" in h:
        return "date"
    if "company" in h or "employer" in h or "name" in h:
        return "company"
    if "location" in h or "city" in h or "address" in h:
        return "location"
    if "affected" in h or "worker" in h or "employee" in h or "number" in h:
        return "workers"
    return h.strip()


def parse(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    rows: list[dict] = []
    for table in soup.find_all("table"):
        trs = table.find_all("tr")
        if not trs:
            continue
        headers = [_norm(c.get_text(" ", strip=True)) for c in trs[0].find_all(["th", "td"])]
        if "company" not in headers:
            continue
        for tr in trs[1:]:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) < 2:
                continue
            rows.append(dict(zip(headers, cells)))
    items = []
    for row in rows:
        company = row.get("company", "").strip()
        if not company:
            continue
        m = re.search(r"[\d,]+", row.get("workers", ""))
        workers = int(m[0].replace(",", "")) if m else None
        location = row.get("location", "").strip()
        items.append({
            "id": stable_id(row.get("date"), company, location),
            "title": f"{company}: WARN notice" + (f", {workers} workers" if workers else ""),
            "date": iso(row.get("date")),
            "url": URL,
            "org": company,
            "place": location,
            "workers": workers,
            "details": "; ".join(f"{k}: {v}" for k, v in row.items() if v),
        })
    return items


class WarnNotices:
    name = "warn"
    label = "WARN layoff notices"
    default_enabled = True
    guidance = """\
Source "warn": Utah WARN Act notices, filed when an employer plans a mass layoff or
closure. `workers` is the number of employees affected.
- high: any notice in Salt Lake County or affecting 50+ workers; any recognizable
  employer; closures of a whole site.
- medium: other notices.
why_it_matters should give employer, place, worker count and layoff date."""

    def fetch(self, cfg: Config, http: Http, seen: set[str]) -> list[dict]:
        items = parse(http.text(URL))
        if not items:
            log.error("WARN page parsed to zero notices; the page layout may have changed")
        return items

    def probe(self, cfg: Config, http: Http) -> dict[str, bytes]:
        return {"warn.html": http.text(URL).encode()}
