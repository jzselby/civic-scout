"""Command line.

    python -m civic_scout run      fetch, rate, brief, write the report and the sheet
    python -m civic_scout probe    save raw pages from each source, to check parsers
    python -m civic_scout sources  list the sources
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from . import analyze, link, pipeline, sheets
from .config import Config
from .http import Http
from .sources import ALL, selected
from .store import Store

log = logging.getLogger("civic_scout")


def cmd_run(cfg: Config, args) -> int:
    if args.require_claude and not args.no_summary and not analyze.available():
        # Otherwise every record would be marked seen without a rating, and never rated.
        log.error("ANTHROPIC_API_KEY is not set; stopping before anything is recorded")
        return 1
    store = Store(cfg.data_dir)
    http = Http(cfg.http_timeout)
    day = pipeline.today()
    result = pipeline.collect(cfg, store, http, day)
    pipeline.enrich(cfg, store, result, use_claude=not args.no_summary)
    print(result.report)

    if args.dry_run:
        log.info("Dry run: nothing written")
        return 0

    if cfg.google_sheet_id and cfg.google_service_account_json and not args.no_sheet:
        # If the sheet write fails, nothing is recorded as seen, so the next run retries.
        sheets.publish(cfg.google_sheet_id, cfg.google_service_account_json, day, result.items,
                       result.labels, result.briefing, result.connections, store.everything())
    elif not args.no_sheet:
        log.info("GOOGLE_SHEET_ID / GOOGLE_SERVICE_ACCOUNT_JSON not set; skipping the sheet")

    cfg.reports_dir.mkdir(parents=True, exist_ok=True)
    (cfg.reports_dir / f"{day.isoformat()}.md").write_text(result.report, encoding="utf-8")
    (cfg.reports_dir / "latest.md").write_text(result.report, encoding="utf-8")
    for source in selected(cfg.sources):
        mine = [i for i in result.items + result.old if i["source"] == source.name]
        store.source(source.name).add(mine)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(result.report)
    failed = [n for n, s in result.health.items() if s.startswith("FAILED")]
    if failed:
        log.error("Source(s) failed: %s", ", ".join(failed))
        return 2 if len(failed) == len(result.health) else 0
    return 0


def cmd_refresh_connections(cfg: Config, args) -> int:
    if not (cfg.google_sheet_id and cfg.google_service_account_json):
        log.error("GOOGLE_SHEET_ID / GOOGLE_SERVICE_ACCOUNT_JSON not set")
        return 1
    items = Store(cfg.data_dir).everything()
    conns = link.connections(items, items, pipeline.today())
    sheets.refresh_connections(cfg.google_sheet_id, cfg.google_service_account_json, conns,
                               {s.name: s.label for s in ALL})
    return 0


def cmd_probe(cfg: Config, args) -> int:
    http = Http(cfg.http_timeout)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for source in selected(cfg.sources or tuple(s.name for s in ALL)):
        try:
            files = source.probe(cfg, http)
        except Exception as exc:
            files = {"error.txt": f"{type(exc).__name__}: {exc}".encode()}
        for name, data in files.items():
            path = out_dir / source.name / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            print(f"{path} ({len(data)} bytes)")
        # Also run the parser, so the log shows what it would produce.
        try:
            items = source.fetch(cfg, http, set())
            print(f"== {source.name}: parser produced {len(items)} items")
            for item in items[:5]:
                print("  ", {k: (v[:150] if isinstance(v, str) else v) for k, v in item.items() if k != "text"})
        except Exception as exc:
            print(f"== {source.name}: parser FAILED: {type(exc).__name__}: {exc}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="civic_scout", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="fetch, rate, brief and publish")
    run.add_argument("--sources", help="comma-separated source names (default: all enabled)")
    run.add_argument("--days-back", type=int, help="report items dated within this many days (default 14)")
    run.add_argument("--dry-run", action="store_true", help="print the report; write nothing, mark nothing seen")
    run.add_argument("--no-sheet", action="store_true", help="skip the Google Sheet")
    run.add_argument("--no-summary", action="store_true", help="skip Claude (no ratings or briefing)")
    run.add_argument("--require-claude", action="store_true",
                     help="fail, recording nothing, if no Anthropic API key is set")
    probe = sub.add_parser("probe", help="save raw pages from each source")
    probe.add_argument("--sources", help="comma-separated source names (default: all)")
    probe.add_argument("--out", default="probe")
    sub.add_parser("sources", help="list sources")
    sub.add_parser("refresh-connections", help="recompute the sheet's Connections column from stored records")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = Config.from_env()
    if getattr(args, "sources", None):
        cfg.sources = tuple(s.strip() for s in args.sources.split(",") if s.strip())
    if getattr(args, "days_back", None):
        cfg.days_back = args.days_back

    if args.command == "sources":
        for s in ALL:
            print(f"{s.name:14} {'on ' if s.default_enabled else 'off'}  {s.label}")
        return 0
    if args.command == "probe":
        return cmd_probe(cfg, args)
    if args.command == "refresh-connections":
        return cmd_refresh_connections(cfg, args)
    return cmd_run(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
