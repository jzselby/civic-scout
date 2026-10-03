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
