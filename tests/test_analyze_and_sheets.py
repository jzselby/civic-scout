from datetime import date
from types import SimpleNamespace

import gspread

from civic_scout import analyze, pipeline, sheets
from civic_scout.config import Config
from civic_scout.store import Store

from .test_pipeline import FakeSource


def test_claude_notes_merge_into_items_and_feed_the_briefing(tmp_path, monkeypatch):
    src = FakeSource([{"id": "7", "title": "Council agenda", "date": "2026-10-07", "text": "Rezone 730 W 900 S"}])
    monkeypatch.setattr(pipeline, "selected", lambda names: [src])
    monkeypatch.setattr(analyze, "available", lambda: True)
    monkeypatch.setattr(analyze.anthropic, "Anthropic", lambda: None)
    calls = []

    def fake_call(client, model, system, prompt, output_format=None, effort="medium"):
        calls.append((system, prompt))
        if output_format is analyze.Ratings:
            assert '"key": "fake:7"' in prompt and "Rezone 730 W 900 S" in prompt
            assert "Source fake." in system
            notes = analyze.Ratings(items=[analyze.ItemNotes(
                key="fake:7", headline="Council weighs West End rezone", importance="high",
                category="Development / housing", why_it_matters="A rezone for 200 apartments.",
                places=["730 W 900 S"], names=["West End Apartments"])])
            return SimpleNamespace(parsed_output=notes)
        if output_format is analyze.Review:
            assert model == "claude-sonnet-5-5" and '"importance": "high"' in prompt
            return SimpleNamespace(parsed_output=analyze.Review(changes=[analyze.Change(
                key="fake:7", importance="medium", note="Routine step in a known rezoning."),
                analyze.Change(key="made:up", importance="high", note="ignored")]))
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="# Morning briefing\n\n**Top stories**\n- rezone")])

    monkeypatch.setattr(analyze, "_call", fake_call)
    cfg = Config(data_dir=tmp_path / "data")
    store = Store(cfg.data_dir)
    result = pipeline.collect(cfg, store, None, date(2026, 10, 3))
    pipeline.enrich(cfg, store, result)
    [item] = result.items
    assert item["places"] == ["730 W 900 S"]
    # The editor's review lowered it, keeping the screener's rating and a note.
    assert (item["importance"], item["first_importance"]) == ("medium", "high")
    assert item["editor_note"] == "Routine step in a known rezoning."
    assert "Editor: Routine step" in result.report
    assert result.briefing.startswith("**Top stories**")
    assert "Council weighs West End rezone" in calls[1][1]
    assert "Council weighs West End rezone" in result.report


def test_records_claude_failed_to_rate_are_held_for_the_next_run(tmp_path, monkeypatch):
    src = FakeSource([{"id": "1", "title": "Rated", "date": "2026-10-02"},
                      {"id": "2", "title": "Dropped by the API", "date": "2026-10-02"}])
    monkeypatch.setattr(pipeline, "selected", lambda names: [src])
    monkeypatch.setattr(analyze, "available", lambda: True)
    monkeypatch.setattr(analyze, "rate", lambda items, guidance, model: {"fake:1": analyze.ItemNotes(
        key="fake:1", headline="h", importance="low", category="Other", why_it_matters="w")})
    monkeypatch.setattr(analyze, "brief", lambda *a: None)
    monkeypatch.setattr(analyze, "review", lambda *a: {})
    cfg = Config(data_dir=tmp_path / "data")
    store = Store(cfg.data_dir)
    result = pipeline.collect(cfg, store, None, date(2026, 10, 3))
    pipeline.enrich(cfg, store, result)
    assert [i["id"] for i in result.items] == ["1"]
    assert [i["id"] for i in result.held] == ["2"]
    assert "Dropped by the API" not in result.report


def test_run_refuses_to_record_anything_without_a_claude_key(tmp_path, monkeypatch):
    from civic_scout.__main__ import main
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    assert main(["run", "--require-claude"]) == 1
    assert not (tmp_path / "data").exists()


class FakeWorksheet:
    _ids = iter(range(100, 10_000))

    def __init__(self, title, rows=1000, cols=10):
        self.title, self.row_count, self.col_count = title, rows, cols
        self.id = next(self._ids)
        self.grid: list[list] = []

    def update(self, values, range_name, value_input_option=None):
        row, col = gspread.utils.a1_to_rowcol(range_name)
        row, col = row - 1, col - 1
        while len(self.grid) < row + len(values):
            self.grid.append([])
        for i, v in enumerate(values):
            line = self.grid[row + i]
            line.extend([""] * (col + len(v) - len(line)))
            line[col:col + len(v)] = list(v)

    def row_values(self, n):
        return list(self.grid[n - 1]) if len(self.grid) >= n else []

    def insert_cols(self, values, col, inherit_from_before=False):
        for i, column in enumerate(values):
            for r, line in enumerate(self.grid):
                line.insert(col - 1 + i, column[r] if r < len(column) else "")

    def append_rows(self, rows, value_input_option=None, table_range=None):
        self.grid += [list(r) for r in rows]

    def insert_rows(self, rows, row, value_input_option=None):
        self.grid[row - 1:row - 1] = [list(r) for r in rows]

    def batch_clear(self, ranges):
        self.grid = self.grid[:1]

    def freeze(self, rows):
        pass

    def format(self, rng, fmt):
        pass

    def add_cols(self, n):
        self.col_count += n

    def add_rows(self, n):
        self.row_count += n

    def get_all_values(self, value_render_option=None):
        return self.grid

    def col_values(self, n, value_render_option=None):
        return [r[n - 1] if len(r) >= n else "" for r in self.grid]


class FakeSpreadsheet:
    def __init__(self):
        self.tabs = {}
        self.requests = []
        self.versions = {}

    def batch_update(self, body):
        self.requests += body["requests"]
        for r in body["requests"]:
            if "deleteDimension" in r:
                rng = r["deleteDimension"]["range"]
                ws = next(w for w in self.tabs.values() if w.id == rng["sheetId"])
                del ws.grid[rng["startIndex"]:rng["endIndex"]]
            if "createDeveloperMetadata" in r:
                dm = r["createDeveloperMetadata"]["developerMetadata"]
                self.versions[dm["location"]["sheetId"]] = dm["metadataValue"]

    def fetch_sheet_metadata(self, params=None):
        return {"sheets": [{"properties": {"sheetId": ws.id},
                            "developerMetadata": ([{"metadataKey": sheets.FORMAT_KEY,
                                                    "metadataValue": self.versions[ws.id]}]
                                                  if ws.id in self.versions else []),
                            "conditionalFormats": []} for ws in self.tabs.values()]}

    def worksheet(self, title):
        if title not in self.tabs:
            raise gspread.WorksheetNotFound(title)
        return self.tabs[title]

    def del_worksheet(self, ws):
        del self.tabs[ws.title]

    def add_worksheet(self, title, rows, cols):
        self.tabs[title] = FakeWorksheet(title, rows, cols)
        return self.tabs[title]


def test_sheet_tabs_are_created_and_filled(monkeypatch):
    sh = FakeSpreadsheet()
    sh.add_worksheet("Sheet1", 1000, 26)
    gc = SimpleNamespace(open_by_key=lambda key: sh)
    monkeypatch.setattr(gspread, "service_account_from_dict", lambda info: gc)
    items = [
        {"source": "pmn", "id": "1", "title": "Agenda", "importance": "high", "headline": "Rezone vote",
         "url": "https://u/1", "date": "2026-10-07"},
        {"source": "warn", "id": "2", "title": "=cmd()", "importance": "low", "date": "2026-10-01"},
        {"source": "slc_licenses", "id": "f", "title": "file", "hidden": True},
    ]
    stored = [{"source": "pmn", "id": "0", "title": "Old high", "importance": "high", "first_seen": "2026-09-30"},
              {"source": "pmn", "id": "x", "title": "Too old", "importance": "high", "first_seen": "2026-01-01"}]
    # Users added a column and reordered: values still land under the right headings.
    sh.add_worksheet(sheets.ALL, 10, 3).update([["Notes", "Key", "Headline"]], "A1")

    sheets.publish("id", "{}", date(2026, 10, 3), items, {"pmn": "Notices", "warn": "WARN"},
                   "brief", {}, stored)

    assert sh.tabs[sheets.BRIEFINGS].grid[1][:3] == ["2026-10-03", 2, 1]
    all_rows = sh.tabs[sheets.ALL].grid
    assert all_rows[0][:3] == ["Notes", "Key", "Headline"]
    assert sorted(r[1] for r in all_rows[1:]) == ["pmn:1", "warn:2"]  # the hidden file marker isn't shown
    row = {r[1]: r for r in all_rows[1:]}
    assert row["pmn:1"][2] == '=HYPERLINK("https://u/1", "Rezone vote")'
    assert row["warn:2"][2] == "'=cmd()"  # scraped text never runs as a formula
    assert any("sortRange" in r for r in sh.requests)
    top = sh.tabs[sheets.TOP].grid
    keys = [r[top[0].index("Key")] for r in top[1:]]
    assert keys == ["pmn:1", "pmn:0"]
    assert "Sheet1" not in sh.tabs
    # Each tab is formatted once, then left alone on later runs.
    formatted = [r for r in sh.requests if "createDeveloperMetadata" in r]
    assert len(formatted) == 3
    sheets.publish("id", "{}", date(2026, 10, 4), [], {}, None, {}, stored)
    assert len([r for r in sh.requests if "createDeveloperMetadata" in r]) == 3


def test_refresh_rewrites_connections_by_key(monkeypatch):
    sh = FakeSpreadsheet()
    monkeypatch.setattr(gspread, "service_account_from_dict", lambda info: SimpleNamespace(open_by_key=lambda k: sh))
    ws = sh.add_worksheet(sheets.ALL, 10, 13)
    ws.update([["Key", "Connections", "Notes"], ["pmn:1", "junk roof permit", "keep me"], ["warn:2", "", ""]], "A1")
    conns = {"warn:2": [{"title": "Permit", "source": "slc_permits", "date": "2026-10-01"}]}
    changed = sheets.refresh_connections("id", "{}", conns, {"slc_permits": "SLC permits"})
    assert changed == 2
    assert ws.grid[1] == ["pmn:1", "", "keep me"]
    assert ws.grid[2][:2] == ["warn:2", "Permit (SLC permits, 2026-10-01)"]


def test_briefing_markdown_becomes_rich_text_with_links():
    md = ("## Top stories\n\n- **Domo lays off 175.** Filed Sept. 23 "
          "([WARN](https://jobs.utah.gov/w)).\n  - nested café note")
    plain, runs = sheets.md_to_rich(md)
    assert plain == "TOP STORIES\n\n• Domo lays off 175. Filed Sept. 23 (WARN).\n    • nested café note"
    link = plain.index("WARN")
    assert {"startIndex": link, "format": {"link": {"uri": "https://jobs.utah.gov/w"}}} in runs
    assert {"startIndex": link + 4, "format": {}} in runs
    assert runs[0] == {"startIndex": 0, "format": {"bold": True}}
    assert sheets.md_to_rich("plain text only") == ("plain text only", [])


def test_format_existing_upgrades_old_rows_and_briefings(monkeypatch):
    sh = FakeSpreadsheet()
    monkeypatch.setattr(gspread, "service_account_from_dict", lambda info: SimpleNamespace(open_by_key=lambda k: sh))
    rec = sh.add_worksheet(sheets.ALL, 10, 13)
    rec.update([["Importance", "Headline", "Link"],
                ["high", "Domo lays off 175", '=HYPERLINK("https://j/w", "Open")'],
                ["low", "No link", ""]], "A1")
    brief = sh.add_worksheet(sheets.BRIEFINGS, 10, 4)
    brief.update([["Run date", "Briefing"], ["2026-10-05", "## Top\n- **A** ([x](https://x))"]], "A1")
    sheets.format_existing("id", "{}")
    assert rec.grid[1][:2] == ["High", '=HYPERLINK("https://j/w", "Domo lays off 175")']
    assert rec.grid[2][:2] == ["Low", "No link"]
    rich = [r["updateCells"] for r in sh.requests if "updateCells" in r]
    assert rich and rich[0]["rows"][0]["values"][0]["userEnteredValue"]["stringValue"] == "TOP\n• A (x)"
    assert len([r for r in sh.requests if "createDeveloperMetadata" in r]) == 2


def test_a_new_column_is_inserted_next_to_its_neighbor(monkeypatch):
    sh = FakeSpreadsheet()
    ws = sh.add_worksheet(sheets.ALL, 10, 13)
    old = [h for h in sheets.RECORD_HEADERS if h != "Editor's note"]
    ws.update([old, ["x"] * len(old)], "A1")
    _, headers = sheets._worksheet(sh, sheets.ALL, sheets.RECORD_HEADERS)
    assert headers == sheets.RECORD_HEADERS
    assert ws.grid[0] == sheets.RECORD_HEADERS
    assert ws.grid[1][headers.index("Editor's note")] == ""


def test_update_ratings_rewrites_changed_rows_and_top_stories(monkeypatch):
    sh = FakeSpreadsheet()
    monkeypatch.setattr(gspread, "service_account_from_dict", lambda info: SimpleNamespace(open_by_key=lambda k: sh))
    ws = sh.add_worksheet(sheets.ALL, 10, 14)
    h = sheets.RECORD_HEADERS
    row = lambda key, imp: [imp if c == "Importance" else key if c == "Key" else "" for c in h]
    ws.update([h, row("pmn:1", "High"), row("pmn:2", "High")], "A1")
    lowered = {"source": "pmn", "id": "1", "title": "t1", "importance": "low", "editor_note": "Routine.",
               "first_seen": "2026-10-05"}
    kept = {"source": "pmn", "id": "2", "title": "t2", "importance": "high", "first_seen": "2026-10-05"}
    sheets.update_ratings("id", "{}", [lowered], [lowered, kept], {}, date(2026, 10, 5))
    assert ws.grid[1][h.index("Importance")] == "Low" and ws.grid[1][h.index("Editor's note")] == "Routine."
    assert ws.grid[2][h.index("Importance")] == "High"
    top = sh.tabs[sheets.TOP].grid
    assert [r[h.index("Key")] for r in top[1:]] == ["pmn:2"]


def test_republishing_the_same_records_adds_no_rows_and_refreshes_the_briefing(monkeypatch):
    sh = FakeSpreadsheet()
    monkeypatch.setattr(gspread, "service_account_from_dict", lambda info: SimpleNamespace(open_by_key=lambda k: sh))
    items = [{"source": "pmn", "id": "1", "title": "Agenda", "importance": "high", "date": "2026-10-07"}]
    sheets.publish("id", "{}", date(2026, 10, 5), items, {}, "first", {}, [])
    sheets.publish("id", "{}", date(2026, 10, 5), [dict(i) for i in items], {}, "second", {}, [])
    assert len(sh.tabs[sheets.ALL].grid) == 2
    briefs = sh.tabs[sheets.BRIEFINGS].grid
    assert len(briefs) == 2 and briefs[1][-1] == "second"


def test_editor_can_hide_a_record_only_for_a_routine_reason():
    items = [{"source": "s", "id": str(n), "importance": imp} for n, imp in
             enumerate(["high", "high", "medium", "low", "medium"])]
    changes = {
        "s:0": analyze.Change(key="s:0", importance="low", reason="other", note="Not urgent."),
        "s:1": analyze.Change(key="s:1", importance="low", reason="routine_permit", note="Trade permit."),
        "s:2": analyze.Change(key="s:2", importance="low", reason="duplicate", note="Covered by s:4."),
        "s:3": analyze.Change(key="s:3", importance="high", reason="other", note="Underrated closure."),
    }
    assert analyze.apply_review(items, changes) == 4
    assert [i["importance"] for i in items] == ["medium", "low", "low", "high", "medium"]
    assert items[0]["first_importance"] == "high"


def test_top_stories_holds_high_for_30_days_and_medium_for_7(monkeypatch):
    sh = FakeSpreadsheet()
    pool = [
        {"source": "s", "id": "h-old", "title": "a", "importance": "high", "first_seen": "2026-09-10"},
        {"source": "s", "id": "m-old", "title": "b", "importance": "medium", "first_seen": "2026-09-25"},
        {"source": "s", "id": "m-new", "title": "c", "importance": "medium", "first_seen": "2026-10-05"},
        {"source": "s", "id": "h-new", "title": "d", "importance": "high", "first_seen": "2026-10-05"},
        {"source": "s", "id": "l-new", "title": "e", "importance": "low", "first_seen": "2026-10-05"},
    ]
    ws, headers = sheets.write_top(sh, date(2026, 10, 5), pool, {}, {})
    keys = [r[headers.index("Key")] for r in ws.grid[1:]]
    assert keys == ["s:h-new", "s:m-new", "s:h-old"]


def test_top_stories_holds_medium_until_its_date_within_30_days():
    sh = FakeSpreadsheet()
    pool = [
        {"source": "s", "id": "hearing", "title": "a", "importance": "medium", "first_seen": "2026-09-20",
         "date": "2026-10-14"},
        {"source": "s", "id": "held", "title": "b", "importance": "medium", "first_seen": "2026-09-20",
         "date": "2026-09-22"},
        {"source": "s", "id": "far", "title": "c", "importance": "medium", "first_seen": "2026-08-20",
         "date": "2026-12-10"},
    ]
    ws, headers = sheets.write_top(sh, date(2026, 10, 5), pool, {}, {})
    assert [r[headers.index("Key")] for r in ws.grid[1:]] == ["s:hearing"]


def test_several_runs_in_one_day_leave_one_briefing_row(monkeypatch):
    sh = FakeSpreadsheet()
    monkeypatch.setattr(gspread, "service_account_from_dict", lambda info: SimpleNamespace(open_by_key=lambda k: sh))
    a = {"source": "pmn", "id": "1", "title": "A", "importance": "high"}
    b = {"source": "warn", "id": "2", "title": "B", "importance": "medium"}
    sheets.publish("id", "{}", date(2026, 10, 4), [a], {}, "yesterday", {}, [], today_items=[a])
    sheets.publish("id", "{}", date(2026, 10, 5), [a | {"id": "3"}], {}, "morning", {}, [])
    sheets.publish("id", "{}", date(2026, 10, 5), [b], {}, "afternoon", {}, [], today_items=[a | {"id": "3"}, b])
    briefs = sh.tabs[sheets.BRIEFINGS].grid
    assert [r[0] for r in briefs[1:]] == ["2026-10-05", "2026-10-04"]
    assert briefs[1][1:] == [2, 1, "afternoon"]
