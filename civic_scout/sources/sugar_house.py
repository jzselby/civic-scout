"""Sugar House Community Council: the neighborhood council's agendas, minutes and land use work.

Community councils are recognized by Salt Lake City but aren't public bodies, so they
don't post on the Public Notice Website. Developers and city staff often bring projects
here first, months before the Planning Commission. The council's WordPress site has a
feed per category, each with the full text of its latest ten posts:

    https://sugarhousecouncil.org/category/agendas/feed/
        monthly meeting agendas (first Wednesday of the month)
    .../category/sugar-house-community-council-minutes/feed/
        meeting summaries, drafted by AI (QuorumScribe) and edited by the council
    .../category/public-meeting-review/feed/
        the same summaries plus short news stories written from a meeting
    .../category/land-use-zoning/feed/ and .../category/luz-reports/feed/
        the Land Use and Zoning committee's monthly report and one post per petition

A post can sit in several categories; it is recorded once, by its WordPress post id.
"""

from __future__ import annotations

import logging
import re
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from ..config import Config
from ..http import Http
from .text_utils import clip

log = logging.getLogger(__name__)

SITE = "https://sugarhousecouncil.org/"
FEED_URL = SITE + "category/{}/feed/"
FEEDS = ("agendas", "sugar-house-community-council-minutes", "public-meeting-review",
         "land-use-zoning", "luz-reports")
ORG = "Sugar House Community Council"
MAX_TEXT = 60_000


def _kind(title: str, categories: list[str]) -> str:
    """What the post is, from its categories (and title, since summaries share a category with news)."""
    if "Agendas" in categories:
        return "Meeting agenda"
    if "LUZ Reports" in categories:
        return "Land Use and Zoning committee report"
    if re.search(r"\bminutes\b|meeting summary", title, re.I):
        return "Meeting minutes (AI-drafted summary)"
    if "Current Petitions" in categories:
        return "Development petition before the Land Use committee"
    return "Council post"


def post_text(html: str) -> str:
    """A post's text, without WordPress's "The post ... first appeared on" footer."""
    text = BeautifulSoup(html, "lxml").get_text("\n")
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    end = text.rfind("\nThe post\n")
    return text[:end] if end > 0 else text


def parse_feed(xml: str) -> list[dict]:
    soup = BeautifulSoup(xml, "xml")
    items = []
    for entry in soup.find_all("item"):
        guid = entry.find("guid")
        link = entry.find("link")
        if not guid or not link:
            continue
        post_id = guid.get_text(strip=True).rsplit("p=", 1)[-1]
        title = entry.find("title").get_text(strip=True)
        categories = [c.get_text(strip=True) for c in entry.find_all("category")]
        try:
            posted = parsedate_to_datetime(entry.find("pubDate").get_text(strip=True))
            posted = posted.astimezone(ZoneInfo("America/Denver")).date().isoformat()
        except (AttributeError, TypeError, ValueError):
            posted = None
        content = entry.find("encoded") or entry.find("description")
        items.append({
            "id": f"post-{post_id}",
            "title": title,
            "date": posted,
            "url": link.get_text(strip=True),
            "org": ORG,
            "details": f"{_kind(title, categories)}; topics: {', '.join(categories)}",
            "text": clip(post_text(content.get_text()), MAX_TEXT) if content else "",
        })
    return items


class SugarHouseCouncil:
    name = "sugar_house"
    label = "Sugar House Community Council"
    default_enabled = True
    guidance = """\
Source "sugar_house": posts from the Sugar House Community Council, the volunteer
neighborhood council for Sugar House in Salt Lake City (City Council District 7). It
advises the city and votes no binding decisions, but developers, city departments,
UTA, police and elected officials present here first, often months before the
Planning Commission sees a project. Kinds of post (in details):
- "Meeting agenda": the monthly meeting (first Wednesday). Rate by the items on it:
  a developer or city presentation on a named project, a vote on a letter for or
  against a project, a new city facility, or a major transit or road change.
- "Meeting minutes": AI-drafted summaries edited by the council, with transcript
  excerpts. Report what was said or decided, attributed to the speaker, and note that
  the summary is the council's own. Leave out recurring reports (treasurer, police
  crime trends, business spotlights) unless something in them is new and specific.
- "Land Use and Zoning committee report" and "Development petition": the committee's
  review of rezonings, planned developments and city zoning changes affecting
  Sugar House.
- high: a new development of about 50+ homes or a major commercial project being
  unveiled or changed; a rezoning, demolition of a landmark or historic building, or
  a city or state project (school rebuild, transit line, liquor store, fire station,
  park overhaul) announced or changed; the council voting to support or oppose a
  project or policy; strong organized resident opposition; officials revealing
  something new (a timeline, cost, closure or decision).
- medium: presentations on smaller projects, updates with some new detail on
  projects already known, the committee's monthly report when it has new petitions,
  Capital Improvement Program requests, agendas with a named project presentation.
- low: agendas or minutes with only standing reports, elections and introductions;
  history talks; event announcements.
why_it_matters should name the project, its address, the developer and what happens
next (a Planning Commission hearing, a council vote, a comment deadline) if stated.
List in `places` every street address and intersection and in `names` every
developer, business and project named, so the item connects to permits, planning
applications and city agendas."""

    def fetch(self, cfg: Config, http: Http, seen: set[str]) -> list[dict]:
        items: dict[str, dict] = {}
        for category in FEEDS:
            try:
                found = parse_feed(http.text(FEED_URL.format(category)))
            except Exception as exc:
                log.error("Sugar House council feed %s could not be read (%s)", category, exc)
                continue
            for item in found:
                items.setdefault(item["id"], item)
        if not items:
            raise RuntimeError("no Sugar House council posts found; the site's feeds may have changed")
        return list(items.values())

    def probe(self, cfg: Config, http: Http) -> dict[str, bytes]:
        return {f"{category}.xml": http.text(FEED_URL.format(category)).encode() for category in FEEDS}
