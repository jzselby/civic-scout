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


def test_warn_table_parses_rows_and_skips_blank_ones():
    items = warn.parse(read("warn.html"))
    assert len(items) == 2
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
