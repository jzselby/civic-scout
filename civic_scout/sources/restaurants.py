"""Salt Lake County Health Department: food establishment closures.

The County closes about one food establishment a week for an imminent health hazard
and lists current closures on its inspection site (public.cdpehs.com, "Closures").
Closed places that reopened stay listed two weeks; ones that haven't, six months.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..config import Config
from ..http import Http
from .text_utils import clip, iso, page_text, stable_id

log = logging.getLogger(__name__)

SITE = "https://public.cdpehs.com/UTEnvPbl/"
WELCOME = SITE + "ESTABLISHMENT/WelcomePage.aspx"
COUNTY_PAGE = "https://www.saltlakecounty.gov/health/food-protection/inspections/"


CLOSURES_BUTTON = "ctl00$PageContent$Closedbut$_Button"


def postback(http: Http, url: str, html: str, target: str) -> tuple[str, str]:
    """Press an ASP.NET WebForms button: post the page's form back with its hidden
    fields and __EVENTTARGET set to the button. Returns (response URL, HTML)."""
    soup = BeautifulSoup(html, "lxml")
    form = soup.find("form")
    data = {i["name"]: i.get("value", "") for i in (form or soup).find_all("input", attrs={"type": "hidden"})
            if i.get("name")}
    data["__EVENTTARGET"] = target
    data["__EVENTARGUMENT"] = ""
    action = urljoin(url, form["action"]) if form is not None and form.get("action") else url
    resp = http.post(action, data)
    return resp.url, resp.text


def closure_links(html: str, base: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    out = []
    for a in soup.find_all("a", href=True):
        if re.search(r"clos", a["href"] + " " + a.get_text(" ", strip=True), re.I):
            url = urljoin(base, a["href"])
            if url not in out:
                out.append(url)
    return out


_DATE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")
_INSP_BUTTON = re.compile(r"__doPostBack\('([^']*InspButton[^']*)'")


def parse_closures(html: str) -> list[dict]:
    """Rows of the Closures table: name, address, date closed, date reopened, and the
    postback target of the row's Inspections button."""
    soup = BeautifulSoup(html, "lxml")
    rows = []
    for tr in soup.find_all("tr"):
        # A closure row has five cells of its own, the first holding the Inspections
        # button (itself a small nested table). Layout rows wrapping the whole table
        # and the button's own rows don't fit that shape.
        tds = tr.find_all("td", recursive=False)
        if len(tds) != 5 or not tds[0].find("a", href=_INSP_BUTTON):
            continue
        cells = [td.get_text(" ", strip=True) for td in tds[1:]]
        cells = [c for c in cells if c]
        dates = [c for c in cells if _DATE.match(c)]
        words = [c for c in cells if not _DATE.match(c)]
        if not dates or len(words) < 2:
            continue
        button = None
        for a in tr.find_all("a", href=True):
            m = _INSP_BUTTON.search(a["href"])
            if m:
                button = m[1]
        rows.append({"name": words[0], "address": words[1], "closed": iso(dates[0]),
                     "reopened": iso(dates[1]) if len(dates) > 1 else None, "button": button})
    return rows


def open_closures(http: Http) -> tuple[str, str]:
    search = http.get(SITE)
    return postback(http, search.url, search.text, CLOSURES_BUTTON)


class RestaurantClosures:
    name = "restaurants"
    label = "SL County health closures"
    default_enabled = True
    guidance = """\
Source "restaurants": Salt Lake County Health Department closures for imminent health
hazards, mostly restaurants and food trucks but also pools, spas and lodging. `text`
(when present) is the establishment's inspection report, which usually gives the
reason; `details` gives the closing and reopening dates.
- high: any closure of a restaurant, chain location or business the public would
  recognize; a repeat closure; a striking reason (pests, sewage, no hot water,
  illness outbreak); a hotel or public pool.
- medium: other closures (an apartment complex pool, a small market).
- low: nothing notable.
why_it_matters should give the name, address, closure date, the reason as the
inspection report states it, and whether it has reopened."""

    def fetch(self, cfg: Config, http: Http, seen: set[str]) -> list[dict]:
        url, html = open_closures(http)
        rows = parse_closures(html)
        if not rows and "Date Closed" not in html:
            log.error("Health closures page didn't load as expected (%s)", url)
        items = []
        for row in rows:
            item_id = stable_id(row["name"], row["address"], row["closed"])
            if item_id in seen:
                continue
            text = ""
            if row["button"]:
                try:
                    _, report = postback(http, url, html, row["button"])
                    text = clip(page_text(report), 8000)
                except Exception as exc:
                    log.warning("Inspection report for %s not read (%s)", row["name"], exc)
            details = f"Closed {row['closed']}" + (f"; reopened {row['reopened']}" if row["reopened"] else "; not reopened")
            items.append({
                "id": item_id,
                "title": f"Health closure: {row['name'].title()}",
                "date": row["closed"],
                "url": WELCOME,
                "org": row["name"].title(),
                "place": row["address"],
                "details": details,
                "text": text,
            })
        return items

    def probe(self, cfg: Config, http: Http) -> dict[str, bytes]:
        out = {}
        try:
            url, html = open_closures(http)
            print(f"restaurants: closures postback -> {url} ({len(html)} chars)")
            out["closures.html"] = html.encode()
            at = html.find("Date Closed")
            print("restaurants: raw HTML from the table header on:\n" + html[at:at + 6000] if at >= 0
                  else "restaurants: no 'Date Closed' in the page")
            rows = parse_closures(html)
            print(f"restaurants: {len(rows)} closure rows: {rows[:5]}")
            if rows and rows[0]["button"]:
                _, report = postback(http, url, html, rows[0]["button"])
                out["inspection-report.html"] = report.encode()
                print("restaurants: first inspection report:", page_text(report)[:1500])
        except Exception as exc:
            out["closures.error.txt"] = f"{type(exc).__name__}: {exc}".encode()
        return out
