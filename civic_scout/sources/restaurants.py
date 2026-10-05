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
from .text_utils import iso, stable_id

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


class RestaurantClosures:
    name = "restaurants"
    label = "SL County restaurant closures"
    # Off until the parser is fitted to the live closures page (see the probe).
    default_enabled = False
    guidance = """\
Source "restaurants": Salt Lake County Health Department closures of food
establishments (restaurants, food trucks, markets) for imminent health hazards.
- high: any closure of a restaurant or business the public would recognize, a chain
  location, a repeat closure, or one with a striking reason (pests, sewage, no hot
  water for days, illness outbreak).
- medium: other closures.
- low: a listing that only records a reopening with nothing notable.
why_it_matters should give the name, address, closure date, the reason as stated, and
whether it has reopened."""

    def fetch(self, cfg: Config, http: Http, seen: set[str]) -> list[dict]:
        return []

    def probe(self, cfg: Config, http: Http) -> dict[str, bytes]:
        out = {}
        try:
            search = http.get(SITE)
            url, html = postback(http, search.url, search.text, CLOSURES_BUTTON)
            print(f"restaurants: closures postback -> {url} ({len(html)} chars)")
            out["closures-postback.html"] = html.encode()
        except Exception as exc:
            out["closures-postback.error.txt"] = f"{type(exc).__name__}: {exc}".encode()
        for name, url in (("welcome.html", WELCOME), ("search.html", SITE), ("county.html", COUNTY_PAGE)):
            try:
                html = http.text(url)
            except Exception as exc:
                out[name + ".error.txt"] = str(exc).encode()
                continue
            out[name] = html.encode()
            for i, link in enumerate(closure_links(html, url)[:3]):
                print(f"restaurants: closure link on {name}: {link}")
                try:
                    out[f"closures-{name}-{i}.html"] = http.text(link).encode()
                except Exception as exc:
                    out[f"closures-{name}-{i}.error.txt"] = str(exc).encode()
        return out
