from datetime import date
from types import SimpleNamespace

import gspread

from civic_scout import analyze, openings, pipeline, sheets
from civic_scout.config import Config
from civic_scout.store import Store

from .test_analyze_and_sheets import FakeSpreadsheet
from .test_pipeline import FakeSource

DAY = date(2026, 10, 23)


def opening(name, stage, address="", city="Salt Lake City", area="Salt Lake City", kind=""):
    return {"name": name, "kind": kind, "address": address, "city": city, "area": area, "stage": stage}


def record(source, id, seen, *found, day=None):
    return {"source": source, "id": id, "first_seen": seen, "date": day, "url": f"https://u/{id}",
            "openings": list(found)}


# A permit in September, then today a business license under a fuller name at the same
# address and a liquor license under that name; plus places in the county and elsewhere.
ITEMS = [
    record("slc_permits", "p1", "2026-09-23T15:00:00+00:00",
           opening("Suan", "building permit", "1234 South State Street, Suite 102", kind="Thai restaurant")),
    record("slc_licenses", "l1", "2026-10-23T14:00:00+00:00",
           opening("Suan Wasatch Thai", "business license", "1234 S State St"), day="2026-10-20"),
    record("pmn", "d1", "2026-10-23T14:00:00+00:00",
           opening("Suan Wasatch Thai", "liquor license"),
           opening("Hopkins Brewing", "liquor license", "1048 E 2100 S", city="Millcreek", area="Salt Lake County",
                   kind="brewery"),
           opening("Nicklaus Turnhouse", "liquor license", city="Park City", area="elsewhere"), day="2026-10-22"),
    {"source": "pmn", "id": "x", "first_seen": "2026-10-23T14:00:00+00:00", "title": "No openings"},
]


def test_records_about_one_place_group_by_address_or_name():
    found = openings.places(ITEMS, DAY)
    assert [p["name"] for p in found] == ["Suan Wasatch Thai", "Hopkins Brewing", "Nicklaus Turnhouse"]
    suan = found[0]
    assert [s["stage"] for s in suan["sightings"]] == ["building permit", "business license", "liquor license"]
    assert (suan["first_seen"], suan["last_seen"]) == ("2026-09-23", "2026-10-23")
    assert suan["kind"] == "Thai restaurant" and suan["address"] == "1234 S State St"


def test_briefing_section_lists_todays_salt_lake_places_city_first():
    md = openings.section(ITEMS, DAY, {"slc_licenses": "SLC licenses", "pmn": "Notices"})
    lines = md.splitlines()
    assert lines[0] == "## New restaurants & bars"
    assert lines[2].startswith("- **Suan Wasatch Thai**, Thai restaurant, 1234 S State St, Salt Lake City: "
                               "business license ([SLC licenses](https://u/l1)); liquor license ([Notices](https://u/d1)).")
    assert lines[2].endswith("First seen Sep 23 (building permit).")
    assert lines[3].startswith("- **Hopkins Brewing**, brewery, 1048 E 2100 S, Millcreek: liquor license")
    assert "Nicklaus" not in md  # outside Salt Lake County: sheet only
    assert openings.section(ITEMS[:1], DAY, {}) is None  # nothing new today


def test_section_goes_right_after_top_stories():
    for briefing in ("## Top stories\n\n- one\n- two\n\n## Coming up\n\n- hearing",
                     "**Top stories**\n- one\n- two\n**Coming up**: hearing"):
        out = openings.add_section(briefing, "## New restaurants & bars\n\n- **Suan**")
        assert out.index("- two") < out.index("New restaurants") < out.index("Coming up")
    assert openings.add_section("**Also notable**\n- x", "S").startswith("S\n\n**Also notable**")
    assert openings.add_section(None, "S") == "S"
    assert openings.add_section("B", None) == "B"


def test_first_record_of_a_place_is_new():
    assert openings.is_new(ITEMS[0], [], DAY)
    assert not openings.is_new(ITEMS[1], ITEMS[:1], DAY)  # same address
    assert openings.is_new(ITEMS[2], ITEMS[:1], DAY)  # names Hopkins Brewing, not seen before


def test_editor_cannot_hide_the_first_record_of_a_new_restaurant():
    items = [{"source": "s", "id": "0", "importance": "medium", "new_opening": True},
             {"source": "s", "id": "1", "importance": "medium"},
             {"source": "s", "id": "2", "importance": "medium", "new_opening": True}]
    changes = {
        "s:0": analyze.Change(key="s:0", importance="low", reason="routine_permit", note="Hood permit."),
        "s:1": analyze.Change(key="s:1", importance="low", reason="routine_permit", note="Hood permit."),
        "s:2": analyze.Change(key="s:2", importance="low", reason="placeholder", note="Void."),
    }
    analyze.apply_review(items, changes)
    assert [i["importance"] for i in items] == ["medium", "low", "low"]


def test_openings_flow_from_rating_and_prerated_records_into_the_briefing(tmp_path, monkeypatch):
    src = FakeSource([{"id": "7", "title": "Liquor commission agenda", "date": "2026-10-23", "text": "..."}])
    permits = FakeSource([{"id": "B1", "title": "Kitchen hood for Tony's Burritos", "date": "2026-10-22",
                           "importance": "low", "prerated": True, "headline": "Kitchen hood for Tony's Burritos"}])
    permits.name, permits.label = "slc_permits", "SLC permits"
    monkeypatch.setattr(pipeline, "selected", lambda names: [src, permits])
    monkeypatch.setattr(analyze, "available", lambda: True)
    monkeypatch.setattr(analyze.anthropic, "Anthropic", lambda: None)
    seen = {}

    def fake_call(client, model, system, prompt, output_format=None, effort="medium"):
        if output_format is analyze.Ratings:
            assert "Openings: list every new restaurant" in system
            return SimpleNamespace(parsed_output=analyze.Ratings(items=[analyze.ItemNotes(
                key="fake:7", headline="Liquor commission licenses new bars", importance="medium",
                category="Alcohol / licensing", why_it_matters="w",
                openings=[analyze.Opening(name="Bar Nine", stage="liquor license", area="Salt Lake City")])]))
        if output_format is analyze.FoundOpenings:
            assert '"key": "slc_permits:B1"' in prompt and "fake:7" not in prompt
            return SimpleNamespace(parsed_output=analyze.FoundOpenings(items=[analyze.ItemOpenings(
                key="slc_permits:B1", openings=[analyze.Opening(
                    name="Tony's Burritos", kind="burrito shop", stage="trade permit", area="Salt Lake City")])]))
        if output_format is analyze.Review:
            return SimpleNamespace(parsed_output=analyze.Review(changes=[]))
        seen["brief"] = prompt
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="## Top stories\n\nNothing urgent today.\n\n## Also notable\n\n- x")])

    monkeypatch.setattr(analyze, "_call", fake_call)
    cfg = Config(data_dir=tmp_path / "data")
    store = Store(cfg.data_dir)
    result = pipeline.collect(cfg, store, None, DAY)
    pipeline.enrich(cfg, store, result)
    by_id = {i["id"]: i for i in result.items}
    assert by_id["7"]["openings"][0]["name"] == "Bar Nine" and by_id["7"]["new_opening"]
    assert by_id["B1"]["openings"][0]["stage"] == "trade permit" and by_id["B1"]["importance"] == "low"
    assert '"opening": true' in seen["brief"]
    b = result.briefing
    assert b.index("Nothing urgent") < b.index("## New restaurants & bars") < b.index("## Also notable")
    assert "**Bar Nine**" in b and "**Tony's Burritos**, burrito shop" in b
    assert "New restaurants & bars" in result.report


def test_openings_tab_has_one_row_per_place(monkeypatch):
    sh = FakeSpreadsheet()
    monkeypatch.setattr(gspread, "service_account_from_dict", lambda info: SimpleNamespace(open_by_key=lambda k: sh))
    sheets.publish("id", "{}", DAY, [], {"pmn": "Notices"}, None, {}, ITEMS)
    grid = sh.tabs[sheets.OPENINGS].grid
    assert grid[0] == sheets.OPENING_HEADERS
    rows = [dict(zip(grid[0], r)) for r in grid[1:]]
    assert [r["Area"] for r in rows] == ["Salt Lake City", "Salt Lake County", "elsewhere"]
    assert rows[0]["Place"] == '=HYPERLINK("https://u/d1", "Suan Wasatch Thai")'
    assert rows[0]["Steps"].startswith("building permit (slc_permits, 2026-09-23); business license")
