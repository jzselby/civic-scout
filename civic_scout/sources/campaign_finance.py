"""Utah campaign finance reports, from the Lieutenant Governor's disclosure site.

Every state candidate, PAC, political issue committee, party and corporation that gives
to campaigns files its contributions and expenditures at disclosures.utah.gov. One CSV
per report year and filer type holds every transaction:

    https://disclosures.utah.gov/Search/AdvancedSearch/GenerateReport?ReportYear=<year>&EntityType=<type>

with the columns FILED ("X" once the report is filed), the filer's name (the column
is named after the type, e.g. PCC), REPORT (the period: "Primary", "September 30th",
"General", ...), TRAN_ID, TRAN_TYPE, TRAN_DATE, TRAN_AMT (expenditures negative),
INKIND, LOAN, AMENDS, NAME, PURPOSE, ADDRESS1, ADDRESS2, CITY, STATE, ZIP.

Transactions show up before their report is filed (FILED blank). An item is one filed
report: a filer's transactions for one period, summed, with the largest contributions
and expenditures. It is dated by its latest transaction, so reports from past periods
count as old on the first run.
"""

from __future__ import annotations

import csv
import io
import logging
from collections import defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo

from ..config import Config
from ..http import Http
from .text_utils import iso

log = logging.getLogger(__name__)

SITE = "https://disclosures.utah.gov/"
CSV_URL = SITE + "Search/AdvancedSearch/GenerateReport?ReportYear={}&EntityType={}"
SEARCH_URL = SITE + "Search/AdvancedSearch"
# Filer types, with how a reporter would say them.
TYPES = {
    "PCC": "candidate",
    "PAC": "political action committee",
    "PIC": "political issue committee",
    "PARTY": "political party",
    "CORP": "corporation",
    "INDEXP": "independent expenditure filer",
}
# Reports moving less money than this are recorded as routine without asking Claude.
MIN_ACTIVITY = 5_000
TOP_ROWS = 25
MAX_BYTES = 100_000_000


def today() -> date:
    return datetime.now(ZoneInfo("America/Denver")).date()


def _amount(raw: str | None) -> float:
    try:
        return float((raw or "").replace(",", "").replace("$", "") or 0)
    except ValueError:
        return 0.0


def parse_csv(text: str) -> list[dict]:
    """Rows of one CSV, with the filer under "FILER" whatever the type's column is called.
    Rows that can't be read are skipped."""
    reader = csv.reader(io.StringIO(text))
    try:
        header = [h.strip() for h in next(reader)]
    except StopIteration:
        return []
    if "TRAN_ID" not in header or len(header) < 2:
        return []
    header[1] = "FILER"
    rows = []
    for cells in reader:
        if len(cells) < len(header) - 1:
            continue
        row = dict(zip(header, (c.strip() for c in cells)))
        if row.get("TRAN_ID") and row.get("FILER"):
            rows.append(row)
    return rows


def _money(x: float) -> str:
    return f"${x:,.0f}"


def _who(row: dict) -> str:
    place = ", ".join(p for p in (row.get("CITY"), row.get("STATE")) if p)
    return row.get("NAME", "") + (f" ({place})" if place else "")


def _line(row: dict) -> str:
    flags = [f for f, col in (("in-kind", "INKIND"), ("loan", "LOAN"), ("amended", "AMENDS")) if row.get(col)]
    purpose = row.get("PURPOSE") or ""
    return (f"{iso(row.get('TRAN_DATE')) or row.get('TRAN_DATE')}  {_money(abs(_amount(row.get('TRAN_AMT'))))}  "
            f"{_who(row)}" + (f"  {purpose}" if purpose else "") + (f"  [{', '.join(flags)}]" if flags else ""))


def reports(rows: list[dict], kind: str, year: int) -> list[dict]:
    """One item per filed report (filer and period)."""
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("FILED") == "X":
            groups[(row["FILER"], row.get("REPORT") or "Report")].append(row)
    items = []
    for (filer, period), txns in groups.items():
        given = [t for t in txns if t.get("TRAN_TYPE") == "Contribution"]
        spent = [t for t in txns if t.get("TRAN_TYPE") != "Contribution"]
        raised = sum(_amount(t.get("TRAN_AMT")) for t in given)
        paid = sum(abs(_amount(t.get("TRAN_AMT"))) for t in spent)
        given.sort(key=lambda t: -_amount(t.get("TRAN_AMT")))
        spent.sort(key=lambda t: -abs(_amount(t.get("TRAN_AMT"))))
        dates = [d for d in (iso(t.get("TRAN_DATE")) for t in txns) if d]
        role = TYPES.get(kind, kind)
        details = (f"{role.capitalize()}; {year} {period} report; raised {_money(raised)} in {len(given)} "
                   f"contribution(s), spent {_money(paid)} in {len(spent)} expenditure(s)")
        largest = [f"{_money(_amount(t.get('TRAN_AMT')))} from {t.get('NAME')}" for t in given[:3]]
        if largest:
            details += "; largest: " + ", ".join(largest)
        text = "\n".join(
            [f"{filer}, {role}: {year} {period} report", details, "",
             f"Largest contributions (of {len(given)}):", *(_line(t) for t in given[:TOP_ROWS]), "",
             f"Largest expenditures (of {len(spent)}):", *(_line(t) for t in spent[:TOP_ROWS])])
        item = {
            "id": f"{year}:{kind}:{filer}:{period}",
            "title": f"{filer}: {year} {period} campaign finance report",
            "date": max(dates) if dates else f"{year}-01-01",
            "url": SEARCH_URL,
            "org": filer,
            "details": details,
            "text": text,
            "raised": round(raised, 2),
            "spent": round(paid, 2),
        }
        if raised + paid < MIN_ACTIVITY:
            item.update(headline=f"{filer} reports {_money(raised)} raised, {_money(paid)} spent ({period})",
                        importance="low", category="Government / politics",
                        why_it_matters="Small campaign finance report.",
                        names=[filer], places=[], prerated=True)
        items.append(item)
    return items


class CampaignFinance:
    name = "campaign_finance"
    label = "Campaign finance reports"
    default_enabled = True
    guidance = f"""\
Source "campaign_finance": campaign finance reports filed with the Lieutenant Governor
by Utah candidates, PACs, political issue committees (ballot campaigns), parties,
corporations and independent expenditure filers. One record is one filed report:
`details` gives the filer type, the period and the totals; `text` lists the largest
contributions and expenditures (date, amount, name and city, purpose). Reports moving
under ${MIN_ACTIVITY:,} are already marked routine.
- high: a single contribution of $10,000 or more to a candidate, or $50,000 or more to
  a committee; money from developers, utilities, contractors or other businesses with
  matters before state or local government; large sums from out of state or from
  groups that don't disclose donors; a candidate's large self-funding or loans; heavy
  independent or late spending in a contested race; a ballot campaign's major funders;
  a large transfer between PACs or to a party.
- medium: other substantial reports in legislative, county or statewide races or ballot
  fights; notable donors.
- low: small or routine reports, refunds, fees and bookkeeping.
why_it_matters should name the filer, the race if the record or the filer's name makes
it clear (don't guess an office), the period, the totals, and the largest donors or
payees by name and amount. List in `names` every business, PAC, union, party or
organization that gave or was paid a notable sum, and notable individual donors such as
developers; leave out small individual donors. Leave `places` empty: donor addresses
are home addresses."""

    def fetch(self, cfg: Config, http: Http, seen: set[str]) -> list[dict]:
        year = today().year
        # The year-end report for last year is filed in January.
        years = [year - 1, year] if today().month <= 2 else [year]
        items, failures = [], []
        for y in years:
            for kind in TYPES:
                url = CSV_URL.format(y, kind)
                try:
                    resp = http.get(url)
                    if "csv" not in resp.headers.get("content-type", ""):
                        # The site answers with an HTML page when a type has no filings.
                        log.info("Campaign finance %s %s: no CSV (no filings yet?)", y, kind)
                        continue
                    if len(resp.content) > MAX_BYTES:
                        raise ValueError(f"{len(resp.content)} bytes")
                    rows = parse_csv(resp.content.decode("utf-8-sig", errors="replace"))
                except Exception as exc:
                    log.error("Campaign finance %s %s could not be read (%s)", y, kind, exc)
                    failures.append(kind)
                    continue
                found = [i for i in reports(rows, kind, y) if i["id"] not in seen]
                log.info("Campaign finance %s %s: %d transactions, %d new filed reports", y, kind, len(rows),
                         len(found))
                items += found
        if failures and len(failures) == len(TYPES) * len(years):
            raise RuntimeError("no campaign finance CSV could be read; the site may have changed")
        return items

    def probe(self, cfg: Config, http: Http) -> dict[str, bytes]:
        year = today().year
        out = {}
        for kind in TYPES:
            resp = http.get(CSV_URL.format(year, kind))
            print(f"campaign_finance {kind}: {resp.status_code} {resp.headers.get('content-type')} "
                  f"{len(resp.content)} bytes")
            out[f"{kind}-head.csv"] = b"\n".join(resp.content.splitlines()[:50])
        return out
