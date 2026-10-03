from pathlib import Path

from civic_scout.sources import pmn, slc_licenses, slc_permits, warn
from civic_scout.sources.text_utils import iso

FIX = Path(__file__).parent / "fixtures"


def read(name):
    return (FIX / name).read_text()


def test_pmn_body_page_lists_notices_newest_first_without_duplicates():
    title, notices = pmn.parse_body_page(read("pmn_body.html"))
    assert "Salt Lake City Council" in title
    assert [n for n, _ in notices] == ["1077049", "1075705", "1064119"]


def test_pmn_notice_page_extracts_event_date_text_and_files():
    url = "https://www.utah.gov/pmn/sitemap/notice/1077049.html"
    notice = pmn.parse_notice_page(read("pmn_notice.html"), url)
    assert notice["title"] == "REVISED Salt Lake City Formal Meeting Agenda"
    assert notice["date"] == "2026-10-07"
    assert "730 W 900 S" in notice["text"]
    assert "var x" not in notice["text"] and "Home | Search" not in notice["text"]
    assert notice["files"] == ["https://www.utah.gov/pmn/files/1453567.pdf"]


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
    assert iso(files[0][1]) is None  # month names alone aren't dates; month comes from the file


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
    assert iso("nothing here") is None
