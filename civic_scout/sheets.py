"""Write the run to a Google Sheet.

Authenticates as a Google Cloud service account; share the sheet with the service
account's email (Editor). Three tabs:

  Briefings      one row per run, newest first: the cross-source briefing
  Top stories    rebuilt every run: high-importance items from the last TOP_DAYS days
  All records    every new record appended, newest first; add your own columns freely

Values are written under their column headings, so people can reorder columns or
add their own (e.g. "Notes", "Assigned to"); runs leave those alone.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any

import gspread

from .link import item_key

log = logging.getLogger(__name__)

BRIEFINGS = "Briefings"
TOP = "Top stories"
ALL = "All records"
BRIEFING_HEADERS = ["Run date", "New records", "High importance", "Briefing"]
RECORD_HEADERS = ["First seen", "Date", "Source", "Importance", "Category", "Headline", "Why it matters",
                  "Who", "Where", "Details", "Connections", "Link", "Key"]
TOP_DAYS = 30
MAX_CELL = 50_000


def text(value) -> str:
    """Literal text: rows are USER_ENTERED, so text starting with = + - @ would run as a formula."""
    s = "" if value is None else str(value)
    s = s[:MAX_CELL]
    return "'" + s if s[:1] in ("=", "+", "-", "@") else s


def hyperlink(url: str | None, label: str) -> str:
    if not url:
        return text(label)
    return '=HYPERLINK("{}", "{}")'.format(url.replace('"', '""'), label[:200].replace('"', '""'))


def record_values(item: dict, label: str, connections: list[dict] | None) -> dict[str, Any]:
    conn = "; ".join(f"{c['title']} ({c['source']}, {c.get('date') or ''})" for c in (connections or [])[:5])
    return {
        "First seen": (item.get("first_seen") or date.today().isoformat())[:10],
        "Date": text(item.get("date")),
        "Source": text(label),
        "Importance": text(item.get("importance")),
        "Category": text(item.get("category")),
        "Headline": text(item.get("headline") or item.get("title")),
        "Why it matters": text(item.get("why_it_matters")),
        "Who": text(item.get("org")),
        "Where": text(item.get("place") or ", ".join((item.get("places") or [])[:3])),
        "Details": text(item.get("details")),
        "Connections": text(conn),
        "Link": hyperlink(item.get("url"), "Open"),
        "Key": text(item_key(item)),
    }


def _worksheet(sh: gspread.Spreadsheet, title: str, headers: list[str]) -> tuple[gspread.Worksheet, list[str]]:
    try:
        ws = sh.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title, rows=1000, cols=len(headers))
        ws.update([headers], "A1")
        ws.freeze(rows=1)
        ws.format("1:1", {"textFormat": {"bold": True}})
        return ws, list(headers)
    existing = ws.row_values(1)
    missing = [h for h in headers if h not in existing]
    if missing:
        if ws.col_count < len(existing) + len(missing):
            ws.add_cols(len(existing) + len(missing) - ws.col_count)
        ws.update([existing + missing], "A1")
        existing += missing
    return ws, existing


def _rows(headers: list[str], values: list[dict[str, Any]]) -> list[list[Any]]:
    return [[v.get(h, "") for h in headers] for v in values]


def _insert_top(ws: gspread.Worksheet, headers: list[str], values: list[dict[str, Any]]) -> None:
    """Insert rows under the header, so the newest are always at the top."""
    if values:
        ws.insert_rows(_rows(headers, values), row=2, value_input_option="USER_ENTERED")


def publish(sheet_id: str, service_account_json: str, today: date, items: list[dict], labels: dict[str, str],
            briefing: str | None, connections: dict[str, list[dict]], all_stored: list[dict]) -> None:
    gc = gspread.service_account_from_dict(json.loads(service_account_json))
    sh = gc.open_by_key(sheet_id)
    items = [i for i in items if not i.get("hidden")]
    high = [i for i in items if i.get("importance") == "high"]

    ws, headers = _worksheet(sh, BRIEFINGS, BRIEFING_HEADERS)
    _insert_top(ws, headers, [{"Run date": today.isoformat(), "New records": len(items),
                               "High importance": len(high), "Briefing": text(briefing or "")}])

    ws, headers = _worksheet(sh, ALL, RECORD_HEADERS)
    order = {"high": 0, "medium": 1, "low": 2}
    ranked = sorted(items, key=lambda i: (order.get(i.get("importance"), 3), i.get("date") or ""))
    _insert_top(ws, headers, [record_values(i, labels.get(i["source"], i["source"]),
                                            connections.get(item_key(i))) for i in ranked])

    # Top stories is regenerated from the store each run, newest first.
    cutoff = (today - timedelta(days=TOP_DAYS)).isoformat()
    new_keys = {item_key(i) for i in items}
    pool = {item_key(i): i for i in all_stored + items}
    top = [i for i in pool.values() if i.get("importance") == "high" and not i.get("hidden")
           and (i.get("first_seen") or today.isoformat())[:10] >= cutoff]
    top.sort(key=lambda i: ((i.get("first_seen") or today.isoformat())[:10], i.get("date") or ""), reverse=True)
    ws, headers = _worksheet(sh, TOP, RECORD_HEADERS)
    ws.batch_clear([f"A2:{gspread.utils.rowcol_to_a1(max(ws.row_count, 2), len(headers))}"])
    rows = _rows(headers, [record_values(i, labels.get(i["source"], i["source"]),
                                         connections.get(item_key(i))) for i in top])
    if rows:
        if ws.row_count < len(rows) + 1:
            ws.add_rows(len(rows) + 1 - ws.row_count)
        ws.update(rows, "A2", value_input_option="USER_ENTERED")
    log.info("Sheet updated: %d new records, %d top stories (%d from this run)",
             len(items), len(top), len([i for i in top if item_key(i) in new_keys]))
