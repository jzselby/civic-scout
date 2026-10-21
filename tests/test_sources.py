from pathlib import Path

from civic_scout.sources import pmn, slc_licenses, slc_permits, warn
from civic_scout.sources.text_utils import iso

FIX = Path(__file__).parent / "fixtures"


def read(name):
    return (FIX / name).read_text()


def test_pmn_body_page_gives_body_entity_and_notices_newest_first():
    body, entity, notices = pmn.parse_body_page(read("pmn_body.html"))
    assert (body, entity) == ("Salt Lake City Council", "Salt Lake City")
    assert [n for n, _ in notices] == ["1112241", "1112213", "1110609"]


def test_pmn_notice_page_extracts_title_event_date_agenda_and_files():
    url = "https://www.utah.gov/pmn/sitemap/notice/1112241.html"
    notice = pmn.parse_notice_page(read("pmn_notice.html"), url)
    assert notice["title"] == "Salt Lake City Council Work Session Agenda"
    assert notice["date"] == "2026-10-06"
    assert "237 & 239 S 1000 East" in notice["text"]
    assert "var x" not in notice["text"] and "Definitions" not in notice["text"]
    assert notice["files"] == ["https://www.utah.gov/pmn/files/1495669.pdf"]


def test_pmn_skips_a_body_whose_page_is_not_the_expected_body(monkeypatch):
    class FakeHttp:
        def text(self, url):
            return read("pmn_body.html") if "publicbody" in url else read("pmn_notice.html")

        def content(self, url):
            return b""

    from civic_scout.config import Config
    monkeypatch.setattr(pmn, "DEFAULT_BODIES", [("1360", "Council", "Salt Lake City Council", "Salt Lake City"),
                                                 ("731", "DABS", "Alcoholic Beverage Services Commission", "")])
    monkeypatch.setattr(pmn, "pdf_text", lambda data: "AGENDA PDF TEXT")
    items = pmn.PublicNotices().fetch(Config(), FakeHttp(), seen={"1110609"})
    assert [i["id"] for i in items] == ["1112241", "1112213"]  # seen notice skipped; DABS body rejected
    assert items[0]["org"] == "Council" and "AGENDA PDF TEXT" in items[0]["text"]


def test_pmn_marks_notices_from_regional_bodies(monkeypatch):
    class FakeHttp:
        def text(self, url):
            return read("pmn_body.html") if "publicbody" in url else read("pmn_notice.html")

        def content(self, url):
            return b""

    from civic_scout.config import Config
    monkeypatch.setattr(pmn, "DEFAULT_BODIES", [])
    monkeypatch.setattr(pmn, "REGIONAL_BODIES", [("1360", "Council", "Salt Lake City Council", "Salt Lake City")])
    monkeypatch.setattr(pmn, "REGIONAL_IDS", {"1360"})
    monkeypatch.setattr(pmn, "pdf_text", lambda data: "")
    items = pmn.PublicNotices().fetch(Config(), FakeHttp(), seen={"1110609", "1112213"})
    assert [(i["id"], i.get("regional")) for i in items] == [("1112241", True)]


def test_pmn_regional_bodies_are_separate_from_the_core_list():
    core = {body_id for body_id, *_ in pmn.DEFAULT_BODIES}
    assert not core & pmn.REGIONAL_IDS
    assert pmn.REGIONAL_IDS == {body_id for body_id, *_ in pmn.REGIONAL_BODIES}


def test_warn_table_parses_rows_and_skips_blank_ones():
    items = warn.parse(read("warn.html"))
    assert len(items) == 5
    assert [i["date"] for i in items[2:]] == ["2022-08-31", "2022-01-15", "2022-01-01"]
    tyson = items[0]
    assert tyson["org"] == "Tyson Fresh Meats" and tyson["workers"] == 723
    assert tyson["date"] == "2026-08-13" and tyson["place"] == "Eagle Mountain"
    # Ids are stable across runs.
    assert warn.parse(read("warn.html"))[0]["id"] == tyson["id"]


def test_license_page_finds_only_license_lists():
    files = slc_licenses.list_files(read("licenses.html"))
    assert [u.rsplit("/", 1)[1] for u, _ in files] == [
        "september-2026-new-business-licenses.xlsx", "august-2026-new-business-licenses.pdf"]
    assert iso(files[0][1]) == "2026-09-01"


def test_license_rows_become_items():
    rows = [{"License Number": "BL-1", "Business Name": "Postino", "Address": "615 W 100 S",
             "Business Type": "Restaurant", "Issue Date": "09/14/2026"}]
    [item] = slc_licenses.rows_to_items(rows, "https://x/file.xlsx", "2026-09-01")
    assert item["id"] == "BL-1" and item["org"] == "Postino" and item["place"] == "615 W 100 S"
    assert item["date"] == "2026-09-14"


def test_slcbuilding_record_converts_with_its_rating():
    rec = {"record_number": "PLNPCM2026-00852", "address": "16 W 800 S", "date": "09/30/2026",
           "scope": "Master plan amendment for an IHC skybridge.", "importance": "high",
           "why_it_matters": "Goes to the Planning Commission.", "detail_url": "https://aca/x",
           "job_value": 1500000.0, "business": "Intermountain Health"}
    item = slc_permits.convert(rec)
    assert item["prerated"] and item["importance"] == "high"
    assert item["date"] == "2026-09-30" and item["places"] == ["16 W 800 S"]
    assert "$1,500,000" in item["details"]


def test_dates():
    assert iso("Event Date & Time\nOctober 7, 2026 7:00 PM") == "2026-10-07"
    assert iso("Sept. 3, 2026") == "2026-09-03"
    assert iso("10/2/26") == "2026-10-02"
    assert iso("2026/10/06 07:00 PM") == "2026-10-06"
    assert iso("August 2026 New Business Licenses") == "2026-08-01"
    assert iso("nothing here") is None


def test_health_closures_table_parses_rows_and_inspection_buttons():
    from civic_scout.sources import restaurants
    rows = restaurants.parse_closures(read("closures.html"))
    assert [(r["name"], r["address"], r["closed"], r["reopened"]) for r in rows] == [
        ("SOUTH RIDGE TOWNHOMES - W/P", "10668 S MONICA RIDGE WY", "2026-06-11", None),
        ("VIETOPIA BISTRO", "1407 W 9000 S", "2026-09-28", "2026-09-30"),
    ]
    assert rows[1]["button"] == "ctl00$PageContent$VW_EST_PUBLIC4TableControlRepeater$ctl02$InspButton$_Button"


def test_pool_closures_are_told_apart_from_restaurants():
    from civic_scout.sources import restaurants
    for name in ("SOUTH RIDGE TOWNHOMES - W/P", "PARKGATE APTS - POOL", "21LUX APTS - OUTDOOR POOL",
                 "ARTESIAN SPRINGS APTS - NORTH W/P #2", "SOME HOTEL - SPA"):
        assert restaurants.is_pool(name), name
    for name in ("VIETOPIA BISTRO", "LOS PANCHITOS #US95AG", "SPA CAFE", "POOLSIDE GRILL"):
        assert not restaurants.is_pool(name), name


def test_health_closure_reason_comes_from_the_closing_inspection():
    from civic_scout.sources import restaurants
    history = restaurants.parse_history(read("inspection_history.html"))
    assert [h["date"] for h in history[:4]] == ["2026-10-02", "2026-10-01", "2026-09-29", "2026-09-28"]
    # Follow-ups after the closure describe the cleanup; the closing-date inspection gives the reason.
    picks = restaurants.closing_inspections(history, "2026-09-28")
    assert [(p["type"], p["button"]) for p in picks] == [
        ("07 - Critical Item", "ctl00$PageContent$INSPECTIONTableControlRepeater$ctl03$ViolButton$_Button")]
    assert restaurants.closing_inspections(history, "2026-09-30")[0]["date"] == "2026-09-29"
    assert restaurants.closing_inspections(history, "2020-01-01") == []

    text = restaurants.report_text(read("inspection_results.html"))
    assert text.startswith("Establishment Information") and "VIETOPIA BISTRO" in text
    assert "There are live cockroaches in the establishment." in text
    assert "open sewer pipe" in text
    assert "Red Text" not in text and "Copyright" not in text and "__doPostBack" not in text


def test_health_closures_fetch_presses_through_to_the_closing_results():
    from types import SimpleNamespace
    from civic_scout.sources import restaurants
    pages = {"Closedbut": "closures.html", "InspButton": "inspection_history.html",
             "ViolButton": "inspection_results.html"}
    pressed = []

    class FakeHttp:
        def get(self, url):
            return SimpleNamespace(url=url, text="<form></form>")

        def post(self, url, data):
            pressed.append(data["__EVENTTARGET"])
            page = next(v for k, v in pages.items() if k in data["__EVENTTARGET"])
            return SimpleNamespace(url=url, text=read(page))

    items = restaurants.RestaurantClosures().fetch(None, FakeHttp(), seen=set())
    vietopia = next(i for i in items if i["org"] == "Vietopia Bistro")
    assert "live cockroaches" in vietopia["text"]
    assert vietopia["url"] == restaurants.SITE
    assert "max_importance" not in vietopia
    pool = next(i for i in items if i["org"] == "South Ridge Townhomes - W/P")
    assert pool["max_importance"] == "medium"
    assert "ctl00$PageContent$INSPECTIONTableControlRepeater$ctl03$ViolButton$_Button" in pressed


def test_council_recap_feed_gives_posts_with_their_dates():
    from civic_scout.sources import slc_council
    recaps = slc_council.parse_recap_feed(read("council_recaps.xml"))
    assert len(recaps) == 10
    assert recaps[1]["id"] == "recap-17949" and recaps[1]["date"] == "2026-10-07"
    assert recaps[1]["url"] == "https://www.slc.gov/council/october-6-2026-meeting-recap/"


def test_council_recap_page_text_is_just_the_recap():
    from civic_scout.sources import slc_council
    text = slc_council.recap_text(read("council_recap.html"), "October 6, 2026 Meeting Recap")
    assert text.startswith("October 6, 2026 Meeting Recap\nCouncil Approves $50,000")
    assert "Impact Fee" in text and "City Directory" not in text and "Tags:" not in text


def test_council_minutes_are_dated_when_posted_not_when_held():
    import json
    from civic_scout.sources import slc_council
    items = slc_council.parse_meetings(json.loads(read("primegov_meetings.json")))
    assert [i["id"] for i in items] == ["minutes-41833", "minutes-45593", "minutes-45590"]  # no-minutes meeting skipped
    assert items[1]["date"] == "2026-10-14" and items[1]["meeting_date"] == "2026-08-18"
    assert items[1]["url"].endswith("compiledMeetingDocumentFileId=45593")
    text = slc_council.minutes_text(read("primegov_minutes.html"))
    assert text.startswith("Oath of Office Ceremony") and "Subscribe" not in text


def test_council_fetch_reads_text_only_for_new_records(monkeypatch):
    from datetime import date
    from civic_scout.config import Config
    from civic_scout.sources import slc_council

    fetched = []

    class FakeHttp:
        def text(self, url):
            fetched.append(url)
            if url == slc_council.RECAP_FEED:
                return read("council_recaps.xml")
            if "ListArchivedMeetings" in url:
                return read("primegov_meetings.json") if url.endswith("2026") else "[]"
            return read("council_recap.html") if "slc.gov" in url else read("primegov_minutes.html")

    monkeypatch.setattr(slc_council, "today", lambda: date(2026, 10, 15))
    items = slc_council.SlcCouncil().fetch(Config(), FakeHttp(), seen={"recap-17967"})
    with_text = {i["id"] for i in items if i.get("text")}
    # Within 14 days and unseen: the Oct. 6 recap and the two minutes posted Oct. 14.
    assert with_text == {"recap-17949", "minutes-45593", "minutes-45590"}
    assert len(items) == 13  # older and seen records still come back, so they're recorded
    assert not any("41833" in u for u in fetched)


def test_campaign_finance_makes_one_record_per_filed_report():
    from civic_scout.sources import campaign_finance as cf
    rows = cf.parse_csv(read("campaign_finance_pic.csv"))
    assert len(rows) == 21 and rows[0]["FILER"] == "Defend Utahs Ballot"
    reports = {i["id"]: i for i in cf.reports(rows, "PIC", 2026)}
    # The General report isn't filed yet (FILED blank), so it isn't a record yet.
    assert sorted(reports) == ["2026:PIC:Defend Utahs Ballot:September 30th",
                               "2026:PIC:Protect Utah Workers:September 30th",
                               "2026:PIC:Utahns for Worker Freedom:Primary"]
    ballot = reports["2026:PIC:Defend Utahs Ballot:September 30th"]
    assert ballot["date"] == "2026-09-23" and ballot["org"] == "Defend Utahs Ballot"
    assert ballot["spent"] == 145397.26 and "spent $145,397 in 9 expenditure(s)" in ballot["details"]
    assert ballot["text"].splitlines()[6] == "2026-09-23  $100,000  Utahns Protecting Our Constitution  Other"
    assert not ballot.get("prerated")
    # Under $5,000 moved: recorded as routine without asking Claude.
    small = reports["2026:PIC:Protect Utah Workers:September 30th"]
    assert small["prerated"] and small["importance"] == "low"


def test_campaign_finance_fetch_skips_types_without_filings_and_seen_reports(monkeypatch):
    from datetime import date
    from types import SimpleNamespace
    from civic_scout.sources import campaign_finance as cf
    monkeypatch.setattr(cf, "today", lambda: date(2026, 10, 21))
    csv_bytes = (FIX / "campaign_finance_pic.csv").read_bytes()

    class FakeHttp:
        def get(self, url):
            if url.endswith("EntityType=PIC"):
                return SimpleNamespace(headers={"content-type": "application/csv; charset=utf-8"}, content=csv_bytes)
            return SimpleNamespace(headers={"content-type": "text/html"}, content=b"<html></html>")

    seen = {"2026:PIC:Utahns for Worker Freedom:Primary"}
    items = cf.CampaignFinance().fetch(None, FakeHttp(), seen)
    assert {i["org"] for i in items} == {"Defend Utahs Ballot", "Protect Utah Workers"}
    # In January last year's year-end report is still being filed, so both years are read.
    monkeypatch.setattr(cf, "today", lambda: date(2027, 1, 12))
    ids = {i["id"] for i in cf.CampaignFinance().fetch(None, FakeHttp(), set())}
    assert "2026:PIC:Defend Utahs Ballot:September 30th" in ids


def test_slc_permits_lowers_repeat_trade_permits_and_test_records():
    class FakeHttp:
        def text(self, url):
            return read("slcbuilding_permits.jsonl")

    from civic_scout.config import Config
    items = slc_permits.SlcPermits().fetch(Config(), FakeHttp(), seen=set())
    rated = {i["id"]: i["importance"] for i in items}
    assert rated["BLD2026-08571"] == "high"  # first record about Suan Wasatch Thai stays
    assert rated["26TMP-023331"] == rated["BLD2026-08631"] == rated["BLD2026-08645"] == "low"
    assert rated["BLD2026-08654"] == "low"  # void test record at the City & County Building
    assert rated["BLD2026-08056"] == "high"  # first record at its address

    # A place already seen in an earlier run counts too.
    items = slc_permits.SlcPermits().fetch(Config(), FakeHttp(), seen={"BLD2026-08571"})
    assert {i["id"]: i["importance"] for i in items}["BLD2026-08645"] == "low"


def test_slc_permits_keeps_big_trade_jobs():
    import json
    rec = json.loads(read("slcbuilding_permits.jsonl").splitlines()[-1])
    again = {**rec, "record_number": "BLD-X", "date": "12/01/2026"}

    class FakeHttp:
        def text(self, url):
            return "\n".join(json.dumps(r) for r in (rec, again))

    from civic_scout.config import Config
    items = slc_permits.SlcPermits().fetch(Config(), FakeHttp(), seen={rec["record_number"]})
    assert [(i["id"], i["importance"]) for i in items] == [("BLD-X", rec["importance"])]
