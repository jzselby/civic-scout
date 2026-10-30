"""Salt Lake City Council actions: what the council decided, after each meeting.

Agendas come in through the Public Notice Website (pmn); this source adds the outcomes,
from two places:

    https://www.slc.gov/council/category/news/meeting-recaps/feed/
        council staff's recap of each meeting, posted the next day (RSS; the item's
        page holds the full recap)
    https://slc.primegov.com/api/v2/PublicPortal/ListArchivedMeetings?year=<year>
        the city's meeting portal: every council, CRA and LBA meeting with its documents.
        Official minutes appear there once approved, typically about 100 days after
        the meeting; the HTML version reads at /Portal/Meeting?compiledMeetingDocumentFileId=<id>

A minutes record is dated by when the minutes were published, not the meeting, so
newly posted minutes count as new and the backlog counts as old.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from ..config import Config
from ..http import Http
from .text_utils import clip, iso, page_text

log = logging.getLogger(__name__)

RECAP_FEED = "https://www.slc.gov/council/category/news/meeting-recaps/feed/"
PORTAL = "https://slc.primegov.com/"
MEETINGS_URL = PORTAL + "api/v2/PublicPortal/ListArchivedMeetings?year={}"
MINUTES_URL = PORTAL + "Portal/Meeting?compiledMeetingDocumentFileId={}"
ORG = "Salt Lake City Council"
MAX_TEXT = 60_000


def today() -> date:
    return datetime.now(ZoneInfo("America/Denver")).date()


def _rss_date(tag) -> str | None:
    """RSS dates read "Wed, 07 Oct 2026 16:37:40 +0000", day before month."""
    try:
        return parsedate_to_datetime(tag.get_text(strip=True)).date().isoformat()
    except (AttributeError, TypeError, ValueError):
        return None


def parse_recap_feed(xml: str) -> list[dict]:
    """Recap items from the RSS feed, without their text (that's on each page)."""
    soup = BeautifulSoup(xml, "xml")
    items = []
    for entry in soup.find_all("item"):
        guid = entry.find("guid")
        link = entry.find("link")
        if not guid or not link:
            continue
        post_id = guid.get_text(strip=True).rsplit("p=", 1)[-1]
        items.append({
            "id": f"recap-{post_id}",
            "title": entry.find("title").get_text(strip=True),
            "date": _rss_date(entry.find("pubDate")),
            "url": link.get_text(strip=True),
            "org": ORG,
            "details": "Council staff recap",
        })
    return items


def recap_text(html: str, title: str) -> str:
    """The recap itself: from its heading (after the menu, which repeats it) to its tags."""
    text = page_text(html)
    start = text.rfind("\n" + title + "\n")
    if start >= 0:
        text = text[start + 1:]
    end = text.find("\nTags:")
    return text[:end] if end > 0 else text


def parse_meetings(data: list[dict]) -> list[dict]:
    """One item per meeting with published HTML minutes."""
    items = []
    for meeting in data:
        docs = {d.get("templateName"): d for d in meeting.get("documentList") or []}
        minutes = docs.get("HTML Minutes")
        if not minutes:
            continue
        title = (meeting.get("title") or "Council meeting").strip()
        held = iso((meeting.get("dateTime") or "")[:10])
        items.append({
            "id": f"minutes-{minutes['id']}",
            "title": f"Minutes: {title}, {meeting.get('date') or held}",
            "date": iso((minutes.get("publishDate") or "")[:10]) or held,
            "url": MINUTES_URL.format(minutes["id"]),
            "org": ORG,
            "meeting_date": held,
            "details": f"Meeting held {held}; minutes approved and posted later",
        })
    return items


def minutes_text(html: str) -> str:
    """The minutes, without the portal's subscribe and video controls."""
    text = page_text(html)
    start = text.find("Show Video\n")
    return text[start + len("Show Video\n"):] if start >= 0 else text


class SlcCouncil:
    name = "slc_council"
    label = "SLC Council actions"
    default_enabled = True
    guidance = """\
Source "slc_council": what the Salt Lake City Council (also sitting as the CRA board
and Local Building Authority) did at its meetings. The agendas for these meetings
already came in through "pmn"; these records are the outcomes. Two kinds:
- "Meeting Recap" (details "Council staff recap"): council staff's summary, posted the
  day after the meeting. It is promotional in tone and leaves out split votes and
  dissent; report what was adopted, approved, denied, delayed or sent back.
- "Minutes: ..." (details "Meeting held ..."): the official minutes, approved and posted
  weeks or months after the meeting, so most of it was reported at the time. Raise only
  what is still news: a split or failed vote, an amendment or item changed from the
  agenda, an item pulled or delayed, a closed session's subject, a sharp public
  comment, or anything that contradicts what was reported.
- high: final votes on taxes, fees, budgets, bonds, rezonings, land sales, police or
  jail policy, homelessness and housing policy, large contracts or CRA deals; a
  failed or split vote on any significant item; an item reversed or delayed; a
  straw poll or the council's direction on any of these, even without a final vote.
  The council sets direction in work sessions: a recap's "Briefing Updates" saying
  the council "discussed potential alterations", "expressed support" or asked for
  changes to a proposal, or minutes recording a straw poll, are decisions, not just
  briefings.
- medium: other substantive actions, briefings on big topics with no direction
  given, appointments.
- low: minutes or recaps of ceremonial, procedural or study-only meetings.
why_it_matters should give the action, the vote if stated and the meeting date, and
cover every high item in the recap or minutes, work session direction included, not
only the formal votes. List
in `places` every street address and in `names` every business, developer or project
named, so the outcome connects to the agenda item and permits."""

    def fetch(self, cfg: Config, http: Http, seen: set[str]) -> list[dict]:
        since = (today() - timedelta(days=cfg.days_back)).isoformat()
        items = []
        try:
            for item in parse_recap_feed(http.text(RECAP_FEED)):
                if item["id"] not in seen and (item["date"] or since) >= since:
                    try:
                        item["text"] = clip(recap_text(http.text(item["url"]), item["title"]), MAX_TEXT)
                    except Exception as exc:
                        log.warning("Council recap %s: page not read (%s)", item["url"], exc)
                items.append(item)
        except Exception as exc:
            log.error("Council recap feed could not be read (%s)", exc)
        year = today().year
        # Minutes post months late, so last year's meetings still get new minutes.
        for y in (year - 1, year):
            try:
                found = parse_meetings(json.loads(http.text(MEETINGS_URL.format(y))))
            except Exception as exc:
                log.error("Council meetings for %s could not be read (%s)", y, exc)
                continue
            for item in found:
                # Text only for new minutes in the look-back window; older ones are recorded unrated.
                if item["id"] not in seen and item["date"] >= since:
                    try:
                        item["text"] = clip(minutes_text(http.text(item["url"])), MAX_TEXT)
                    except Exception as exc:
                        log.warning("Council minutes %s: not read (%s)", item["url"], exc)
                items.append(item)
        if not items:
            raise RuntimeError("no council recaps or minutes found; the feed or portal may have changed")
        return items

    def probe(self, cfg: Config, http: Http) -> dict[str, bytes]:
        out = {"recaps.xml": http.text(RECAP_FEED).encode(),
               "meetings.json": http.text(MEETINGS_URL.format(today().year)).encode()}
        recaps = parse_recap_feed(out["recaps.xml"].decode())
        if recaps:
            out["recap.html"] = http.text(recaps[0]["url"]).encode()
        return out
