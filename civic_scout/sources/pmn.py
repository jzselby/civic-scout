"""Utah Public Notice Website (utah.gov/pmn): agendas and notices of public bodies.

Every Utah public body must post meeting agendas here, so one scraper covers city
councils, planning commissions, school boards, the liquor commission and more.
Uses the site's static "sitemap" pages:

    /pmn/sitemap/publicbody/<body id>.html   a body's notices (links to each notice)
    /pmn/sitemap/notice/<notice id>.html     one notice: title, date, description, files
    /pmn/files/<file id>.pdf                 attached agendas and packets

To watch another body, find it on https://www.utah.gov/pmn/, open its page and take
the number from the URL; add it to PMN_BODIES (comma-separated, "id" or "id=Name").
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..config import Config
from ..http import Http
from .text_utils import clip, field, iso, page_text, pdf_text

log = logging.getLogger(__name__)

BASE = "https://www.utah.gov/pmn/"
BODY_URL = BASE + "sitemap/publicbody/{}.html"
NOTICE_URL = BASE + "sitemap/notice/{}.html"

# (body id, display name, text the page's "Public Body Name" must contain, text its
# "Entity Name" must contain). The check guards against a wrong id silently
# reporting another body's meetings. Checked against the live site 2026-10-03.
DEFAULT_BODIES: list[tuple[str, str, str, str]] = [
    ("1360", "Salt Lake City Council", "Salt Lake City Council", "Salt Lake City"),
    ("1274", "SLC Planning Commission", "Planning Commission", "Salt Lake City"),
    ("1067", "SLC Board of Education", "Board of Education", "Salt Lake City School District"),
    ("6413", "Utah Inland Port Authority Board", "UIPA Board", "Inland Port"),
    ("709", "Salt Lake County Council", "Council", "Salt Lake County"),
    ("1266", "SLC Historic Landmark Commission", "Historic Landmark Commission", "Salt Lake City"),
    ("13", "Alcoholic Beverage Services Commission", "Alcoholic Beverage Services Commission",
     "Alcoholic Beverage Services"),
    # Education (checked 2026-10-05).
    ("767", "Granite School District Board", "Board of Education", "Granite School District"),
    ("738", "Jordan School District Board", "Board of Education", "Jordan School District"),
    ("1281", "Canyons School District Board", "Canyons School District Board of Education", "Canyons School District"),
    ("1094", "Murray School District Board", "Murray City School Board", "Murray City School District"),
    ("1499", "Utah State Board of Education", "State Board of Education", "State Board of Education"),
    ("83", "Utah Board of Higher Education", "Utah Board of Higher Education", "Utah Board of Higher Education"),
    ("1376", "University of Utah Board of Trustees", "University of Utah - Board of Trustees", "University of Utah"),
    ("1383", "Salt Lake Community College Trustees", "Salt Lake Community College--Board of Trustees",
     "Utah Board of Higher Education"),
]
# Not yet found: the current UTA Board of Trustees (940 is the deactivated old board;
# UTA's notices since 2026 don't appear under a new body yet)
# and the SLC Community Reinvestment Agency board (2017 is the County's RDA).

# Body pages list only upcoming notices, so this cap is rarely reached.
MAX_PER_BODY = 30
# Characters of notice text plus attachment text passed to Claude per notice.
MAX_TEXT = 60_000
MAX_FILES = 3

_NOTICE_LINK = re.compile(r"/pmn/sitemap/notice/(\d+)\.html")
_FILE_LINK = re.compile(r"/pmn/files/\d+[^\"'#?]*", re.I)
# Page chrome after the notice itself.
_FOOTER = re.compile(r"\n(Subscribe by Email|Subscribe\nSubscribe by Email|Public Notice Website\nHome\nSearch)\n")


def body_list(cfg: Config) -> list[tuple[str, str, str, str]]:
    if not cfg.pmn_bodies:
        return DEFAULT_BODIES
    out = []
    for spec in cfg.pmn_bodies:
        body_id, _, name = spec.partition("=")
        out.append((body_id.strip(), name.strip() or f"Public body {body_id.strip()}", "", ""))
    return out


def parse_body_page(html: str) -> tuple[str, str, list[tuple[str, str]]]:
    """(public body name, entity name, [(notice id, link text)] newest first)."""
    soup = BeautifulSoup(html, "lxml")
    text = page_text(html)
    notices: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        m = _NOTICE_LINK.search(a["href"])
        if m and m[1] not in notices:
            notices[m[1]] = a.get_text(" ", strip=True)
    ordered = sorted(notices.items(), key=lambda kv: int(kv[0]), reverse=True)
    return field(text, "Public Body Name") or "", field(text, "Entity Name") or "", ordered


def parse_notice_page(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    text = page_text(html)
    start = text.find("Notice Information")
    if start > 0:
        text = text[start:]
    m = _FOOTER.search(text)
    if m:
        text = text[:m.start()]
    title = field(text, "Notice Title")
    if not title:
        heading = soup.find("h1") or soup.find("title")
        title = heading.get_text(" ", strip=True) if heading else ""
    when = iso(field(text, "Event Start Date & Time") or field(text, "Event Date & Time") or "")
    files = []
    for a in soup.find_all("a", href=True):
        if _FILE_LINK.search(a["href"]):
            href = urljoin(url, a["href"])
            if href not in files:
                files.append(href)
    return {"title": title, "text": text, "date": when or iso(text), "files": files}


class PublicNotices:
    name = "pmn"
    label = "Public meeting notices"
    default_enabled = True
    guidance = """\
Source "pmn": agendas and notices posted to the Utah Public Notice Website by public
bodies (city and county councils, planning commissions, school district boards, the
state school and higher-education boards, university trustees, the state liquor
commission, the Inland Port Authority and others). `org` is the public body; `text`
holds the notice and the text of its attached agenda.
- high: votes on budgets, taxes, fees, bonds or large contracts; school closures,
  boundary changes, superintendent or president hires, contracts and departures,
  tuition and fee increases, curriculum or book-removal fights, school safety
  incidents; rezonings, master
  plan changes and large developments; new policies or ordinances with public
  impact; closures, layoffs, settlements, lawsuits, audits, firings or hirings of
  top officials; closed sessions on litigation or property; the liquor commission
  granting or denying licenses for recognizable bars, restaurants, clubs or
  distilleries; anything likely to be contentious.
- medium: routine items with a news hook, appointments, study sessions on big topics.
- low: routine minutes, cancellations, ceremonial items, consent agendas without
  anything notable.
For each notice, why_it_matters should name the specific agenda item(s) worth a
reporter's time, not summarize the whole agenda. List in `places` every street
address on the agenda and in `names` every business, developer or applicant named."""

    def fetch(self, cfg: Config, http: Http, seen: set[str]) -> list[dict]:
        items = []
        for body_id, body_name, want_body, want_entity in body_list(cfg):
            try:
                found_body, found_entity, notices = parse_body_page(http.text(BODY_URL.format(body_id)))
            except Exception as exc:
                log.error("PMN body %s (%s): could not load its page (%s)", body_id, body_name, exc)
                continue
            if (want_body.lower() not in found_body.lower()) or (want_entity.lower() not in found_entity.lower()):
                log.error("PMN body %s is %r (%r), expected %r (%r); skipping it. Fix its id in pmn.py.",
                          body_id, found_body, found_entity, want_body, want_entity)
                continue
            log.info("PMN %s (%s): %d notices listed", body_name, body_id, len(notices))
            for notice_id, link_text in notices[:MAX_PER_BODY]:
                if notice_id in seen:
                    continue
                item = self._notice(http, notice_id, link_text, body_name)
                if item:
                    items.append(item)
        return items

    def _notice(self, http: Http, notice_id: str, link_text: str, body_name: str) -> dict | None:
        url = NOTICE_URL.format(notice_id)
        try:
            notice = parse_notice_page(http.text(url), url)
        except Exception as exc:
            log.warning("PMN notice %s: could not load (%s)", notice_id, exc)
            return None
        parts = [notice["text"]]
        for file_url in notice["files"][:MAX_FILES]:
            if not file_url.lower().endswith(".pdf"):
                continue
            try:
                parts.append(f"--- Attachment {file_url} ---\n" + pdf_text(http.content(file_url)))
            except Exception as exc:
                log.warning("PMN notice %s: attachment %s not read (%s)", notice_id, file_url, exc)
        return {
            "id": notice_id,
            "title": notice["title"] or link_text,
            "date": notice["date"],
            "url": url,
            "org": body_name,
            "text": clip("\n\n".join(parts), MAX_TEXT),
            "details": f"Attachments: {len(notice['files'])}" if notice["files"] else "",
        }

    def probe(self, cfg: Config, http: Http) -> dict[str, bytes]:
        out = {}
        for nid in cfg.pmn_notices:
            try:
                html = http.text(NOTICE_URL.format(nid))
            except Exception as exc:
                print(f"PMN notice {nid}: {exc}")
                continue
            ids = sorted(set(re.findall(r"/pmn/sitemap/publicbody/(\d+)\.html", html)))
            text = page_text(html)
            print(f"PMN notice {nid}: body ids {ids}, body {field(text, 'Public Body')!r}, "
                  f"entity {field(text, 'Entity')!r}, title {field(text, 'Notice Title')!r}")
        for body_id, *_ in body_list(cfg):
            try:
                html = http.text(BODY_URL.format(body_id))
            except Exception as exc:
                out[f"body-{body_id}.error.txt"] = str(exc).encode()
                continue
            out[f"body-{body_id}.html"] = html.encode()
            body, entity, notices = parse_body_page(html)
            print(f"PMN body {body_id}: {body!r} of {entity!r}, {len(notices)} upcoming notices")
            if notices and not any(k.startswith("notice-") for k in out):
                nid = notices[0][0]
                notice_html = http.text(NOTICE_URL.format(nid))
                out[f"notice-{nid}.html"] = notice_html.encode()
        return out
