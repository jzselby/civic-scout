from datetime import date

from civic_scout.link import connections, normalize_address, normalize_name


def test_addresses_normalize_to_one_form():
    assert normalize_address("1124 East 100 South, Unit 3, Salt Lake City UT 84102") == "1124 E 100 S"
    assert normalize_address("1124 E 100 S") == "1124 E 100 S"
    assert normalize_address("707 W Genesee Avenue Salt Lake City UT 84104-1234") == "707 W GENESEE AVE"
    assert normalize_address("257 E 200 S Suite 810") == "257 E 200 S"
    assert normalize_address("Eagle Mountain") is None
    assert normalize_address(None) is None


def test_names_drop_legal_suffixes_and_generic_names():
    assert normalize_name("Postino, LLC") == "POSTINO"
    assert normalize_name("The Postino Inc.") == "POSTINO"
    assert normalize_name("Salt Lake City") is None


def test_connections_link_items_across_sources_by_address_and_name():
    today = date(2026, 10, 3)
    permit = {"source": "slc_permits", "id": "BLD1", "title": "Restaurant build-out",
              "place": "615 W 100 S, Salt Lake City UT", "date": "2026-08-01"}
    agenda = {"source": "pmn", "id": "9", "title": "DABS commission", "org": "Alcoholic Beverage Services Commission",
              "places": ["615 West 100 South"], "names": ["Postino LLC"], "date": "2026-10-01"}
    license_ = {"source": "slc_licenses", "id": "BL-1", "title": "New license: Postino", "org": "Postino",
                "date": "2026-09-14"}
    unrelated = {"source": "warn", "id": "w", "title": "Layoffs", "org": "Other Co", "place": "Ogden",
                 "date": "2026-09-01"}
    out = connections([agenda], [permit, license_, unrelated], today)
    matched = {m["key"]: m["via"] for m in out["pmn:9"]}
    assert matched == {"slc_permits:BLD1": ["address 615 W 100 S"], "slc_licenses:BL-1": ["name POSTINO"]}


def test_public_body_names_and_old_items_do_not_connect():
    today = date(2026, 10, 3)
    a = {"source": "pmn", "id": "1", "title": "Agenda", "org": "Salt Lake City Planning Commission",
         "date": "2026-10-01"}
    b = {"source": "pmn", "id": "2", "title": "Minutes", "org": "Salt Lake City Planning Commission",
         "date": "2026-09-01"}
    old = {"source": "slc_permits", "id": "X", "title": "Old", "place": "1 E Main St", "date": "2020-01-01"}
    c = {"source": "warn", "id": "3", "title": "Layoff", "place": "1 E Main St", "date": "2026-10-01"}
    assert connections([a], [b], today) == {}
    assert connections([c], [old], today) == {}


def test_matches_within_one_source_are_not_connections():
    today = date(2026, 10, 3)
    a = {"source": "slc_permits", "id": "1", "title": "Electrical", "place": "1116 S Richards St", "date": "2026-09-29"}
    b = {"source": "slc_permits", "id": "2", "title": "Plumbing", "place": "1116 S Richards St", "date": "2026-09-23"}
    assert connections([a], [b], today) == {}
