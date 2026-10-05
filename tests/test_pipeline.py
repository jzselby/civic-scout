from datetime import date

from civic_scout import pipeline
from civic_scout.config import Config
from civic_scout.store import Store


class FakeSource:
    name = "fake"
    label = "Fake source"
    guidance = "Source fake."
    default_enabled = True

    def __init__(self, items):
        self.items = items

    def fetch(self, cfg, http, seen):
        return [dict(i) for i in self.items]


class Broken(FakeSource):
    name = "broken"
    label = "Broken source"

    def fetch(self, cfg, http, seen):
        raise RuntimeError("site down")


def run(tmp_path, monkeypatch, sources):
    monkeypatch.setattr(pipeline, "selected", lambda names: sources)
    cfg = Config(data_dir=tmp_path / "data", reports_dir=tmp_path / "reports", days_back=14)
    store = Store(cfg.data_dir)
    result = pipeline.collect(cfg, store, None, date(2026, 10, 3))
    pipeline.enrich(cfg, store, result, use_claude=False)
    return cfg, store, result


def test_new_old_and_seen_items_are_split(tmp_path, monkeypatch):
    src = FakeSource([
        {"id": "a", "title": "Recent", "date": "2026-10-01", "text": "big raw text"},
        {"id": "b", "title": "Old", "date": "2026-01-01"},
        {"id": "c", "title": "Undated"},
    ])
    cfg, store, result = run(tmp_path, monkeypatch, [src])
    assert [i["id"] for i in result.items] == ["a", "c"]
    assert [i["id"] for i in result.old] == ["b"]
    assert "Recent" in result.report and "Old" not in result.report

    store.source("fake").add(result.items + result.old)
    stored = (cfg.data_dir / "fake.jsonl").read_text()
    assert "big raw text" not in stored  # raw text is never stored

    _, _, again = run(tmp_path, monkeypatch, [src])
    assert again.items == [] and again.old == []


def test_a_failing_source_is_reported_and_others_still_run(tmp_path, monkeypatch):
    ok = FakeSource([{"id": "a", "title": "Fine", "date": "2026-10-02"}])
    _, _, result = run(tmp_path, monkeypatch, [Broken([]), ok])
    assert result.health["broken"].startswith("FAILED: RuntimeError: site down")
    assert [i["id"] for i in result.items] == ["a"]
    assert "FAILED" in result.report


def test_big_warn_notice_is_high_without_claude():
    item = {"source": "warn", "id": "x", "workers": 120}
    pipeline.apply_rules(item)
    assert item["importance"] == "high"


def test_was_reported_flags_old_backlog_items():
    from civic_scout.store import was_reported
    assert was_reported({"reported": False}) is False
    assert was_reported({"first_seen": "2026-10-05T15:00:00+00:00", "date": "2026-09-30"})
    assert not was_reported({"first_seen": "2026-10-05T15:00:00+00:00", "date": "2026-08-01"})
    assert was_reported({"first_seen": "2026-10-05T15:00:00+00:00"})


def test_a_second_run_the_same_day_rewrites_one_report_covering_both(tmp_path, monkeypatch):
    from civic_scout.__main__ import write_report
    first = FakeSource([{"id": "a", "title": "Morning record", "date": "2026-10-02"}])
    cfg, store, result = run(tmp_path, monkeypatch, [first])
    for i in result.items:
        i["first_seen"] = "2026-10-03T15:00:00+00:00"
    store.source("fake").add(result.items)
    write_report(cfg, result.day, result.report)

    second = FakeSource([{"id": "a", "title": "Morning record", "date": "2026-10-02"},
                         {"id": "b", "title": "Afternoon record", "date": "2026-10-03"}])
    _, _, again = run(tmp_path, monkeypatch, [second])
    assert [i["id"] for i in again.items] == ["b"]
    assert "Morning record" in again.report and "Afternoon record" in again.report
    assert "**2 new record(s)" in again.report
    write_report(cfg, again.day, again.report)

    assert sorted(p.name for p in cfg.reports_dir.iterdir()) == ["2026-10-03.md", "latest.md"]
    assert (cfg.reports_dir / "2026-10-03.md").read_text() == again.report


def test_report_lists_sources_found_by_an_earlier_run():
    from civic_scout import report
    items = [{"id": "1", "source": "warn", "title": "Layoff"}, {"id": "2", "source": "pmn", "title": "Agenda"}]
    text = report.build(date(2026, 10, 3), items, {"pmn": "Notices", "warn": "WARN"}, {"pmn": "ok (5 fetched)"},
                        None, {})
    assert "| Notices | 1 | ok (5 fetched) |" in text
    assert "| WARN | 1 | earlier run today |" in text
