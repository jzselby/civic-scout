"""Write the run to a Google Sheet.

Authenticates as a Google Cloud service account; share the sheet with the service
account's email (Editor). Three tabs:

  Briefings      one row per run, newest first: the cross-source briefing
  Top stories    rebuilt every run: high records (last TOP_DAYS days) and medium
                 ones (last MEDIUM_DAYS days): the tab reporters work from
  All records    every new record, sorted newest first; add your own columns freely

Values are written under their column headings, so people can reorder columns or
add their own (e.g. "Notes", "Assigned to"); runs leave those alone.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, timedelta
from typing import Any

import gspread
from gspread.utils import ValueRenderOption

from . import link
from .link import item_key
from .store import was_reported

log = logging.getLogger(__name__)

BRIEFINGS = "Briefings"
TOP = "Top stories"
ALL = "All records"
BRIEFING_HEADERS = ["Run date", "New records", "High importance", "Briefing"]
RECORD_HEADERS = ["First seen", "Date", "Source", "Importance", "Category", "Headline", "Why it matters",
                  "Editor's note", "Who", "Where", "Details", "Connections", "Link", "Key"]
# Top stories keeps high records this many days, medium ones MEDIUM_DAYS.
TOP_DAYS = 30
MEDIUM_DAYS = 7
MAX_CELL = 50_000


def text(value) -> str:
    """Literal text: rows are USER_ENTERED, so text starting with = + - @ would run as a formula."""
    s = "" if value is None else str(value)
    s = s[:MAX_CELL]
    return "'" + s if s[:1] in ("=", "+", "-", "@") else s


def hyperlink(url: str | None, label: str) -> str:
    if not url:
        return text(label)
    return '=HYPERLINK("{}", "{}")'.format(url.replace('"', '""'), label[:250].replace('"', '""'))


def connections_text(connections: list[dict] | None, labels: dict[str, str]) -> str:
    return "; ".join(f"{c['title']} ({labels.get(c['source'], c['source'])}, {c.get('date') or ''})"
                     for c in (connections or [])[:5])


def record_values(item: dict, label: str, connections: list[dict] | None,
                  labels: dict[str, str] | None = None) -> dict[str, Any]:
    conn = connections_text(connections, labels or {})
    return {
        "First seen": (item.get("first_seen") or date.today().isoformat())[:10],
        "Date": text(item.get("date")),
        "Source": text(label),
        "Importance": text((item.get("importance") or "").capitalize()),
        "Category": text(item.get("category")),
        "Headline": hyperlink(item.get("url"), item.get("headline") or item.get("title") or ""),
        "Why it matters": text(item.get("why_it_matters")),
        "Editor's note": text(item.get("editor_note")),
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
    for n, name in enumerate(headers):
        if name in existing:
            continue
        # A new column goes right after its neighbor in our order (e.g. "Editor's note"
        # after "Why it matters"), or at the end if that neighbor isn't there.
        before = next((h for h in reversed(headers[:n]) if h in existing), None)
        if before is None:
            if ws.col_count < len(existing) + 1:
                ws.add_cols(1)
            ws.update([[name]], gspread.utils.rowcol_to_a1(1, len(existing) + 1))
            existing.append(name)
        else:
            at = existing.index(before) + 1
            ws.insert_cols([[name]], col=at + 1, inherit_from_before=True)
            existing.insert(at, name)
        log.info("Added %r column to %r", name, ws.title)
    return ws, existing


def _rows(headers: list[str], values: list[dict[str, Any]]) -> list[list[Any]]:
    return [[v.get(h, "") for h in headers] for v in values]


def _as_date(value) -> date | None:
    """A date cell read unformatted: a serial number, or ISO text."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return date(1899, 12, 30) + timedelta(days=int(value))
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _insert_top(ws: gspread.Worksheet, headers: list[str], values: list[dict[str, Any]]) -> None:
    """Insert rows under the header, so the newest are always at the top."""
    if values:
        ws.insert_rows(_rows(headers, values), row=2, value_input_option="USER_ENTERED")


# --- rich text for the briefing cell -----------------------------------------

_INLINE = re.compile(r"\*\*(.+?)\*\*|\[([^\]]+)\]\((https?://[^)\s]+)\)")


def _u16(s: str) -> int:
    """Length in UTF-16 code units, which is how Sheets counts text-run positions."""
    return len(s.encode("utf-16-le")) // 2


def md_to_rich(markdown: str) -> tuple[str, list[dict]]:
    """Claude's Markdown briefing as cell text plus Sheets text-format runs: headings
    and **bold** stay bold, [label](url) becomes a clickable label, bullets become •."""
    segments: list[tuple[str, dict]] = []
    lines = markdown.strip().splitlines()
    for n, line in enumerate(lines):
        heading = re.match(r"^\s*#{1,6}\s+(.*)$", line) or re.fullmatch(r"\s*\*\*([^*]+)\*\*:?\s*", line)
        if heading:
            segments.append((heading.group(1).strip().upper(), {"bold": True}))
        else:
            line = re.sub(r"^(\s*)[-*]\s+", lambda m: "    " * (len(m[1]) // 2) + "• ", line)
            pos = 0
            for m in _INLINE.finditer(line):
                segments.append((line[pos:m.start()], {}))
                if m[1] is not None:  # bold; links inside it keep just their label
                    segments.append((re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", m[1]), {"bold": True}))
                else:
                    segments.append((m[2], {"link": {"uri": m[3]}}))
                pos = m.end()
            segments.append((line[pos:], {}))
        if n < len(lines) - 1:
            segments.append(("\n", {}))
    plain, runs, at, current = "", [], 0, None
    for chunk, fmt in segments:
        if not chunk:
            continue
        if fmt != current:
            runs.append({"startIndex": at, "format": fmt})
            current = fmt
        plain += chunk
        at += _u16(chunk)
    if all(not r["format"] for r in runs):
        runs = []
    return plain[:MAX_CELL], [r for r in runs if r["startIndex"] < _u16(plain[:MAX_CELL])]


def rich_cell(sheet_id: int, row: int, col: int, plain: str, runs: list[dict]) -> dict:
    """updateCells request writing rich text into one cell (0-based row and column)."""
    return {"updateCells": {
        "rows": [{"values": [{"userEnteredValue": {"stringValue": plain}, "textFormatRuns": runs}]}],
        "fields": "userEnteredValue,textFormatRuns",
        "start": {"sheetId": sheet_id, "rowIndex": row, "columnIndex": col}}}


# --- formatting (applied once per FORMAT_VERSION, so later manual formatting stays) ---

FORMAT_KEY = "civic_scout_format"
# Bumping this re-applies the formatting on the next run, replacing each tab's
# conditional-format rules (including any added by hand).
FORMAT_VERSION = "2"


def _rgb(hex_color: str) -> dict:
    h = hex_color.lstrip("#")
    return {"red": int(h[0:2], 16) / 255, "green": int(h[2:4], 16) / 255, "blue": int(h[4:6], 16) / 255}


# Color means one of two things: the row is from the latest run (NEW_BG), or how
# important it is (a High or Medium accent on the Importance cell; Low rows in gray).
HEADER_BG, HEADER_FG = _rgb("#1F3864"), _rgb("#FFFFFF")
NEW_BG = _rgb("#E8F0FE")
HIGH_BG, HIGH_FG = _rgb("#FCE8E6"), _rgb("#A50E0E")
MEDIUM_BG, MEDIUM_FG = _rgb("#FEF7E0"), _rgb("#8A5300")
MUTED_FG = _rgb("#999999")
DATE_FORMAT = {"type": "DATE", "pattern": "mmm d, yyyy"}

# Per-column (width in pixels, wrap strategy, number format).
COLUMN_STYLE: dict[str, tuple[int, str | None, dict | None]] = {
    "First seen": (95, None, DATE_FORMAT),
    "Date": (95, None, DATE_FORMAT),
    "Source": (150, "WRAP", None),
    "Importance": (90, None, None),
    "Category": (140, "WRAP", None),
    "Headline": (320, "WRAP", None),
    "Why it matters": (440, "WRAP", None),
    "Editor's note": (240, "WRAP", None),
    "Who": (170, "WRAP", None),
    "Where": (190, "WRAP", None),
    "Details": (220, "CLIP", None),
    "Connections": (300, "WRAP", None),
    "Link": (60, None, None),
    "Key": (160, None, None),
    "Run date": (95, None, DATE_FORMAT),
    "New records": (80, None, None),
    "High importance": (85, None, None),
    "Briefing": (1000, "WRAP", None),
}
# Still written (for anyone who filters on them), but hidden: the headline is the link,
# and Key is an internal id.
HIDDEN = ("Link", "Key")


def _cells(sheet_id: int, col: int | None = None, header: bool = False) -> dict:
    rng: dict[str, Any] = {"sheetId": sheet_id}
    rng.update({"startRowIndex": 0, "endRowIndex": 1} if header else {"startRowIndex": 1})
    if col is not None:
        rng.update({"startColumnIndex": col, "endColumnIndex": col + 1})
    return rng


def _repeat(rng: dict, fmt: dict, fields: str) -> dict:
    return {"repeatCell": {"range": rng, "cell": {"userEnteredFormat": fmt}, "fields": fields}}


def _letter(index: int) -> str:
    return gspread.utils.rowcol_to_a1(1, index + 1).rstrip("1")


def _common_requests(sheet_id: int, headers: list[str]) -> list[dict]:
    reqs = [
        _repeat(_cells(sheet_id, header=True),
                {"backgroundColor": HEADER_BG, "textFormat": {"bold": True, "foregroundColor": HEADER_FG},
                 "wrapStrategy": "WRAP", "verticalAlignment": "MIDDLE"},
                "userEnteredFormat(backgroundColor,textFormat,wrapStrategy,verticalAlignment)"),
        _repeat(_cells(sheet_id), {"verticalAlignment": "TOP"}, "userEnteredFormat.verticalAlignment"),
        {"updateSheetProperties": {
            "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
            "fields": "gridProperties.frozenRowCount"}},
    ]
    for name in headers:
        if name not in COLUMN_STYLE:
            continue
        i = headers.index(name)
        width, wrap, number = COLUMN_STYLE[name]
        reqs.append({"updateDimensionProperties": {
            "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": i, "endIndex": i + 1},
            "properties": {"pixelSize": width, "hiddenByUser": name in HIDDEN},
            "fields": "pixelSize,hiddenByUser"}})
        if wrap:
            reqs.append(_repeat(_cells(sheet_id, i), {"wrapStrategy": wrap}, "userEnteredFormat.wrapStrategy"))
        if number:
            reqs.append(_repeat(_cells(sheet_id, i), {"numberFormat": number}, "userEnteredFormat.numberFormat"))
    return reqs


def _rule(ranges: list[dict], expr: str, fmt: dict, index: int) -> dict:
    return {"addConditionalFormatRule": {"index": index, "rule": {"ranges": ranges, "booleanRule": {
        "condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": expr}]}, "format": fmt}}}}


def _row_rules(sheet_id: int, headers: list[str]) -> list[dict]:
    """Sheets applies only the first matching rule to a cell, so combinations get their
    own rules ahead of the singles. Text comparisons in Sheets ignore case."""
    rules: list[tuple[list[dict], str, dict]] = []
    imp = f"${_letter(headers.index('Importance'))}2" if "Importance" in headers else None
    new = None
    if "First seen" in headers:
        c = _letter(headers.index("First seen"))
        new = f'AND(${c}2<>"",${c}2=MAX(${c}$2:${c}))'
    if imp:
        cell = [_cells(sheet_id, headers.index("Importance"))]
        rules.append((cell, f'={imp}="High"',
                      {"backgroundColor": HIGH_BG, "textFormat": {"bold": True, "foregroundColor": HIGH_FG}}))
        rules.append((cell, f'={imp}="Medium"', {"backgroundColor": MEDIUM_BG, "textFormat": {"foregroundColor": MEDIUM_FG}}))
    rows = [_cells(sheet_id)]
    low = f'{imp}="Low"' if imp else None
    if new and low:
        rules.append((rows, f"=AND({new},{low})", {"backgroundColor": NEW_BG, "textFormat": {"foregroundColor": MUTED_FG}}))
    if new:
        rules.append((rows, f"={new}", {"backgroundColor": NEW_BG}))
    if low:
        rules.append((rows, f"={low}", {"textFormat": {"foregroundColor": MUTED_FG}}))
    return [_rule(r, e, f, i) for i, (r, e, f) in enumerate(rules)]


def record_format_requests(sheet_id: int, headers: list[str]) -> list[dict]:
    return _common_requests(sheet_id, headers) + _row_rules(sheet_id, headers) + [{"setBasicFilter": {"filter": {
        "range": {"sheetId": sheet_id, "startRowIndex": 0, "startColumnIndex": 0, "endColumnIndex": len(headers)}}}}]


def briefing_format_requests(sheet_id: int, headers: list[str]) -> list[dict]:
    return _common_requests(sheet_id, headers)


def _format_state(sh: gspread.Spreadsheet) -> dict[int, tuple[str | None, int]]:
    """Each tab's (format version, number of conditional-format rules)."""
    meta = sh.fetch_sheet_metadata({"fields": "sheets(properties(sheetId),developerMetadata,conditionalFormats)"})
    state = {}
    for sheet in meta.get("sheets", []):
        version = next((dm.get("metadataValue") for dm in sheet.get("developerMetadata", [])
                        if dm.get("metadataKey") == FORMAT_KEY), None)
        state[sheet.get("properties", {}).get("sheetId")] = (version, len(sheet.get("conditionalFormats", [])))
    return state


def _needs_format(state: dict, ws: gspread.Worksheet) -> bool:
    return state.get(ws.id, (None, 0))[0] != FORMAT_VERSION


def _apply_format(sh: gspread.Spreadsheet, ws: gspread.Worksheet, state: dict, requests: list[dict]) -> None:
    """Replace the tab's formatting: old rules and version marker out, new ones in."""
    version, rule_count = state.get(ws.id, (None, 0))
    clear = [{"deleteConditionalFormatRule": {"sheetId": ws.id, "index": 0}}] * rule_count
    if version is not None:
        clear.append({"deleteDeveloperMetadata": {"dataFilter": {"developerMetadataLookup": {
            "metadataKey": FORMAT_KEY, "metadataLocation": {"sheetId": ws.id}}}}})
    marker = {"createDeveloperMetadata": {"developerMetadata": {
        "metadataKey": FORMAT_KEY, "metadataValue": FORMAT_VERSION,
        "location": {"sheetId": ws.id}, "visibility": "DOCUMENT"}}}
    sh.batch_update({"requests": clear + requests + [marker]})
    log.info("Formatted %r (format version %s)", ws.title, FORMAT_VERSION)


_LINK_FORMULA = re.compile(r'^=HYPERLINK\("((?:[^"]|"")*)",\s*"(?:[^"]|"")*"\)$')


def _upgrade_rows(ws: gspread.Worksheet) -> int:
    """Bring rows written by format-version-0 runs up to date: the headline carries
    the link (from the Link column) and importance is capitalized. Returns rows changed."""
    grid = ws.get_all_values(value_render_option=ValueRenderOption.formula)
    if len(grid) < 2:
        return 0
    headers = grid[0]
    if not {"Headline", "Link", "Importance"} <= set(headers):
        return 0
    h, l, imp = headers.index("Headline"), headers.index("Link"), headers.index("Importance")
    heads, imps, changed = [], [], 0
    for row in grid[1:]:
        row = row + [""] * (len(headers) - len(row))
        head, new_head = row[h], row[h]
        m = _LINK_FORMULA.match(str(row[l]))
        if m and not str(head).startswith("="):
            new_head = hyperlink(m[1].replace('""', '"'), str(head))
        new_imp = str(row[imp]).capitalize()
        changed += (new_head != head) or (new_imp != row[imp])
        heads.append([new_head])
        imps.append([new_imp])
    if changed:
        ws.update(heads, f"{_letter(h)}2", value_input_option="USER_ENTERED")
        ws.update(imps, f"{_letter(imp)}2", value_input_option="USER_ENTERED")
    return changed


def _sort_newest_first(sh: gspread.Spreadsheet, ws: gspread.Worksheet, headers: list[str]) -> None:
    specs = [{"dimensionIndex": headers.index(n), "sortOrder": "DESCENDING"}
             for n in ("First seen", "Date") if n in headers]
    if specs:
        sh.batch_update({"requests": [{"sortRange": {"range": {
            "sheetId": ws.id, "startRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": len(headers)},
            "sortSpecs": specs}}]})


def format_existing(sheet_id: str, service_account_json: str) -> None:
    """Apply the current formatting to an existing sheet without fetching anything:
    style the tabs, upgrade older rows, and turn Markdown briefings into rich text."""
    gc = gspread.service_account_from_dict(json.loads(service_account_json))
    sh = gc.open_by_key(sheet_id)
    for title in (ALL, TOP):
        try:
            log.info("%r: %d row(s) upgraded", title, _upgrade_rows(sh.worksheet(title)))
        except gspread.WorksheetNotFound:
            pass
    state = _format_state(sh)
    for title, reqs in ((ALL, record_format_requests), (TOP, record_format_requests),
                        (BRIEFINGS, briefing_format_requests)):
        try:
            ws = sh.worksheet(title)
        except gspread.WorksheetNotFound:
            continue
        headers = ws.row_values(1)
        if _needs_format(state, ws):
            _apply_format(sh, ws, state, reqs(ws.id, headers))
        if title == BRIEFINGS and "Briefing" in headers:
            col = headers.index("Briefing")
            requests = []
            for row, md in enumerate(ws.col_values(col + 1)[1:], start=1):
                plain, runs = md_to_rich(md)
                if runs and plain != md:
                    requests.append(rich_cell(ws.id, row, col, plain, runs))
            if requests:
                sh.batch_update({"requests": requests})
                log.info("Briefings: %d cell(s) converted to rich text", len(requests))
    try:
        blank = sh.worksheet("Sheet1")
        if not any(blank.get_all_values()):
            sh.del_worksheet(blank)
    except gspread.WorksheetNotFound:
        pass


def write_top(sh: gspread.Spreadsheet, today: date, pool_items: list[dict], connections: dict[str, list[dict]],
              labels: dict[str, str]) -> tuple[gspread.Worksheet, list[str]]:
    """Regenerate Top stories, the tab reporters work from: high records first seen in
    the last TOP_DAYS days and medium ones from the last MEDIUM_DAYS, newest run first
    and high before medium within a run."""
    keep = {"high": (today - timedelta(days=TOP_DAYS)).isoformat(),
            "medium": (today - timedelta(days=MEDIUM_DAYS)).isoformat()}
    pool = {item_key(i): i for i in pool_items}

    def seen(i: dict) -> str:
        return (i.get("first_seen") or today.isoformat())[:10]

    top = [i for i in pool.values() if i.get("importance") in keep and not i.get("hidden")
           and was_reported(i) and seen(i) >= keep[i["importance"]]]
    top.sort(key=lambda i: (seen(i), i["importance"] == "high", i.get("date") or ""), reverse=True)
    # Connections for every row, not just this run's: older top stories gain links too.
    top_connections = {**link.connections(top, list(pool.values()), today), **connections}
    ws, headers = _worksheet(sh, TOP, RECORD_HEADERS)
    ws.batch_clear([f"A2:{gspread.utils.rowcol_to_a1(max(ws.row_count, 2), len(headers))}"])
    rows = _rows(headers, [record_values(i, labels.get(i["source"], i["source"]),
                                         top_connections.get(item_key(i)), labels) for i in top])
    if rows:
        if ws.row_count < len(rows) + 1:
            ws.add_rows(len(rows) + 1 - ws.row_count)
        ws.update(rows, "A2", value_input_option="USER_ENTERED")
    log.info("Top stories: %d record(s)", len(rows))
    return ws, headers


def update_ratings(sheet_id: str, service_account_json: str, changed: list[dict], all_items: list[dict],
                   labels: dict[str, str], today: date) -> None:
    """After a re-review: rewrite Importance and Editor's note on All records rows (by
    Key) for the changed records, then regenerate Top stories."""
    gc = gspread.service_account_from_dict(json.loads(service_account_json))
    sh = gc.open_by_key(sheet_id)
    ws, headers = _worksheet(sh, ALL, RECORD_HEADERS)
    by_key = {item_key(i): i for i in changed}
    grid = ws.get_all_values(value_render_option=ValueRenderOption.formula)
    k, imp, note = headers.index("Key"), headers.index("Importance"), headers.index("Editor's note")
    imps, notes, n = [], [], 0
    for row in grid[1:]:
        row = row + [""] * (len(headers) - len(row))
        item = by_key.get(row[k])
        if item:
            n += 1
            imps.append([(item.get("importance") or "").capitalize()])
            notes.append([text(item.get("editor_note"))])
        else:
            imps.append([row[imp]])
            notes.append([row[note]])
    if imps:
        ws.update(imps, f"{_letter(imp)}2", value_input_option="USER_ENTERED")
        ws.update(notes, f"{_letter(note)}2", value_input_option="USER_ENTERED")
    log.info("All records: %d rating(s) updated", n)
    write_top(sh, today, all_items, {}, labels)
    state = _format_state(sh)
    for tab, reqs in ((ws, record_format_requests), (sh.worksheet(TOP), record_format_requests)):
        if _needs_format(state, tab):
            _apply_format(sh, tab, state, reqs(tab.id, tab.row_values(1)))


def write_briefing(sh: gspread.Spreadsheet, today: date, day_items: list[dict],
                   briefing: str | None) -> tuple[gspread.Worksheet, list[str]]:
    """One Briefings row per day: replace any rows already there for today with one
    covering all of today's records."""
    ws, headers = _worksheet(sh, BRIEFINGS, BRIEFING_HEADERS)
    if "Run date" in headers:
        cells = ws.col_values(headers.index("Run date") + 1, value_render_option=ValueRenderOption.unformatted)
        same_day = [n for n, v in enumerate(cells) if n > 0 and _as_date(v) == today]
        if same_day:
            sh.batch_update({"requests": [{"deleteDimension": {"range": {
                "sheetId": ws.id, "dimension": "ROWS", "startIndex": n, "endIndex": n + 1}}}
                for n in sorted(same_day, reverse=True)]})
    plain, runs = md_to_rich(briefing or "")
    values = {"Run date": today.isoformat(), "New records": len(day_items),
              "High importance": sum(1 for i in day_items if i.get("importance") == "high"), "Briefing": text(plain)}
    _insert_top(ws, headers, [values])
    if runs and "Briefing" in headers:
        try:
            sh.batch_update({"requests": [rich_cell(ws.id, 1, headers.index("Briefing"), plain, runs)]})
        except Exception:
            log.exception("Couldn't add links to the briefing cell; it stays as plain text")
    return ws, headers


def replace_briefing(sheet_id: str, service_account_json: str, today: date, day_items: list[dict],
                     briefing: str | None) -> None:
    gc = gspread.service_account_from_dict(json.loads(service_account_json))
    write_briefing(gc.open_by_key(sheet_id), today, day_items, briefing)


def publish(sheet_id: str, service_account_json: str, today: date, items: list[dict], labels: dict[str, str],
            briefing: str | None, connections: dict[str, list[dict]], all_stored: list[dict],
            today_items: list[dict] | None = None) -> None:
    gc = gspread.service_account_from_dict(json.loads(service_account_json))
    sh = gc.open_by_key(sheet_id)
    items = [i for i in items if not i.get("hidden")]
    high = [i for i in items if i.get("importance") == "high"]

    # Idempotent: records already on All records (by Key) aren't added again, so a
    # re-run after a failure doesn't duplicate rows.
    all_ws, all_headers = _worksheet(sh, ALL, RECORD_HEADERS)
    existing = set(all_ws.col_values(all_headers.index("Key") + 1)[1:]) if "Key" in all_headers else set()
    fresh = [i for i in items if item_key(i) not in existing]
    if len(fresh) < len(items):
        log.info("%d record(s) already on the sheet; not added again", len(items) - len(fresh))

    day = [i for i in (today_items if today_items is not None else items) if not i.get("hidden")]
    briefings, b_headers = write_briefing(sh, today, day, briefing)

    # Appended, then sorted newest first below: inserting at row 2 would shift the
    # filter and color rules (which start at row 2) down past the new rows.
    if fresh:
        all_ws.append_rows(_rows(all_headers, [record_values(i, labels.get(i["source"], i["source"]),
                                                             connections.get(item_key(i)), labels) for i in fresh]),
                           value_input_option="USER_ENTERED", table_range="A1")

    top_ws, top_headers = write_top(sh, today, all_stored + items, connections, labels)
    # Presentation only: a failure here must not lose the data written above.
    try:
        state = _format_state(sh)
        for tab, tab_headers, reqs in ((all_ws, all_headers, record_format_requests),
                                       (top_ws, top_headers, record_format_requests),
                                       (briefings, b_headers, briefing_format_requests)):
            if _needs_format(state, tab):
                _apply_format(sh, tab, state, reqs(tab.id, tab_headers))
        _sort_newest_first(sh, all_ws, all_headers)
    except Exception:
        log.exception("Formatting the sheet failed; the data was written")
    # A new spreadsheet starts with an empty "Sheet1"; remove it once the real tabs exist.
    try:
        blank = sh.worksheet("Sheet1")
        if not any(blank.get_all_values()):
            sh.del_worksheet(blank)
    except gspread.WorksheetNotFound:
        pass
    log.info("Sheet updated: %d new records", len(items))


def refresh_connections(sheet_id: str, service_account_json: str, connections: dict[str, list[dict]],
                        labels: dict[str, str]) -> int:
    """Rewrite the Connections column of every row, matched by Key, from freshly computed
    connections (e.g. after the matching rules change). Returns the number of cells changed."""
    gc = gspread.service_account_from_dict(json.loads(service_account_json))
    sh = gc.open_by_key(sheet_id)
    changed = 0
    for title in (ALL, TOP):
        try:
            ws = sh.worksheet(title)
        except gspread.WorksheetNotFound:
            continue
        grid = ws.get_all_values()
        if len(grid) < 2 or "Key" not in grid[0] or "Connections" not in grid[0]:
            continue
        key_col, conn_col = grid[0].index("Key"), grid[0].index("Connections")
        column = []
        for row in grid[1:]:
            key = row[key_col] if key_col < len(row) else ""
            old = row[conn_col] if conn_col < len(row) else ""
            new = text(connections_text(connections.get(key), labels)) if key else old
            changed += new != old
            column.append([new])
        letter = gspread.utils.rowcol_to_a1(1, conn_col + 1).rstrip("1")
        ws.update(column, f"{letter}2", value_input_option="USER_ENTERED")
    log.info("Refreshed connections: %d cell(s) changed", changed)
    return changed
