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
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="**Top stories**\n- rezone")])

    monkeypatch.setattr(analyze, "_call", fake_call)
    cfg = Config(data_dir=tmp_path / "data")
    store = Store(cfg.data_dir)
    result = pipeline.collect(cfg, store, None, date(2026, 10, 3))
    pipeline.enrich(cfg, store, result)
    [item] = result.items
    assert item["importance"] == "high" and item["places"] == ["730 W 900 S"]
    assert result.briefing.startswith("**Top stories**")
    assert "Council weighs West End rezone" in calls[1][1]
    assert "Council weighs West End rezone" in result.report


class FakeWorksheet:
    def __init__(self, title, rows=1000, cols=10):
        self.title, self.row_count, self.col_count = title, rows, cols
        self.grid: list[list] = []

    def update(self, values, range_name, value_input_option=None):
        row = int(range_name[1:]) - 1
        while len(self.grid) < row + len(values):
            self.grid.append([])
        for i, v in enumerate(values):
            self.grid[row + i] = list(v)

    def row_values(self, n):
        return self.grid[n - 1] if len(self.grid) >= n else []

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


class FakeSpreadsheet:
    def __init__(self):
        self.tabs = {}

    def worksheet(self, title):
        if title not in self.tabs:
            raise gspread.WorksheetNotFound(title)
        return self.tabs[title]

    def add_worksheet(self, title, rows, cols):
        self.tabs[title] = FakeWorksheet(title, rows, cols)
        return self.tabs[title]


def test_sheet_tabs_are_created_and_filled(monkeypatch):
    sh = FakeSpreadsheet()
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
    assert all_rows[1][:3] == ["", "pmn:1", "Rezone vote"]
    assert all_rows[2][2] == "'=cmd()"  # scraped text never runs as a formula
    assert len(all_rows) == 3  # the hidden file marker isn't shown
    top = sh.tabs[sheets.TOP].grid
    keys = [r[top[0].index("Key")] for r in top[1:]]
    assert keys == ["pmn:1", "pmn:0"]
