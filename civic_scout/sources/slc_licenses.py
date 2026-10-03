"""Salt Lake City new business licenses.

The city's Business Licensing office posts a list each month of every business
that applied for a license the previous month, linked from LIST_PAGE. Spreadsheet
lists become one item per business; a PDF list becomes one item for the month,
and Claude picks out the notable names.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..config import Config
from ..http import Http
from .text_utils import clip, iso, pdf_text, sheet_rows, stable_id

log = logging.getLogger(__name__)

LIST_PAGE = "https://www.slc.gov/Finance/business-licensing/license-data/"
# Only the newest monthly list: older ones were reported in earlier months.
MAX_FILES = 1
_FILE = re.compile(r"\.(xlsx|csv|pdf)(\?|$)", re.I)


def list_files(html: str, base: str = LIST_PAGE) -> list[tuple[str, str]]:
    """[(url, link text)] of monthly list files, in page order (newest first on the city's page)."""
    soup = BeautifulSoup(html, "lxml")
    out = []
    for a in soup.find_all("a", href=True):
        text = a.get_text(" ", strip=True)
        if _FILE.search(a["href"]) and re.search(r"licen|business", text + a["href"], re.I):
            url = urljoin(base, a["href"])
            if url not in [u for u, _ in out]:
                out.append((url, text))
    return out


def _pick(row: dict, *words: str):
    for key, value in row.items():
        if value not in (None, "") and any(w in str(key).lower() for w in words):
            return value
    return None


def rows_to_items(rows: list[dict], file_url: str, month: str) -> list[dict]:
    items = []
    for row in rows:
        name = _pick(row, "dba", "business name", "name")
        if not name:
            continue
        address = _pick(row, "address", "location")
        number = _pick(row, "license", "number", "id")
        kind = _pick(row, "type", "class", "category", "description", "naics")
        items.append({
            "id": str(number) if number else stable_id(name, address),
            "title": f"New business license: {name}",
            "date": iso(_pick(row, "date")) or month,
            "url": file_url,
            "org": str(name),
            "place": str(address) if address else None,
            "details": "; ".join(f"{k}: {v}" for k, v in row.items() if v not in (None, "")),
        })
    return items


class SlcBusinessLicenses:
    name = "slc_licenses"
    label = "SLC new business licenses"
    default_enabled = True
    guidance = """\
Source "slc_licenses": businesses that applied for a Salt Lake City business license.
An item is one business or, when the city published a PDF, a whole month's list in
`text`.
- high: a business the public would recognize or notice (a known chain, a notable
  local restaurant or venue, a large employer); bars, clubs, cannabis, vape or
  tattoo shops, short-term rental operators, payday lenders, data centers, anything
  likely to draw interest. For a monthly list, high if it contains any such names.
- medium: new restaurants, shops and offices with a storefront.
- low: home occupations, contractors, sole proprietors, renewals.
For a monthly list, why_it_matters should name the notable businesses and their
addresses; put every notable address in `places` and name in `names`."""

    def fetch(self, cfg: Config, http: Http, seen: set[str]) -> list[dict]:
        files = list_files(http.text(LIST_PAGE))
        if not files:
            log.error("No license list files found on %s; the page layout may have changed", LIST_PAGE)
        items: list[dict] = []
        for url, text in files[:MAX_FILES]:
            # Dated the day it was found: the list covers the previous month, which
            # would otherwise fall outside the look-back window.
            month = date.today().isoformat()
            file_id = stable_id(url)
            if file_id in seen:
                continue
            data = http.content(url)
            if url.lower().split("?")[0].endswith(".xlsx"):
                rows = rows_to_items(sheet_rows(data), url, month)
                log.info("Business licenses %s: %d rows", text, len(rows))
                items += rows
                # Remember the file too, so it isn't downloaded again.
                items.append({"id": file_id, "title": f"Business license list: {text}", "date": month,
                              "url": url, "details": f"{len(rows)} businesses", "hidden": True})
            elif url.lower().split("?")[0].endswith(".pdf"):
                items.append({"id": file_id, "title": text if "licen" in text.lower() else f"New business licenses: {text}", "date": month,
                              "url": url, "org": "Salt Lake City Business Licensing",
                              "text": clip(pdf_text(data, max_pages=80), 150_000)})
            else:
                log.warning("Skipping license file with unsupported format: %s", url)
        return items

    def probe(self, cfg: Config, http: Http) -> dict[str, bytes]:
        html = http.text(LIST_PAGE)
        out = {"license-page.html": html.encode()}
        files = list_files(html)
        if files:
            url = files[0][0]
            out["license-file" + url[url.rfind("."):].split("?")[0]] = http.content(url)
        return out
