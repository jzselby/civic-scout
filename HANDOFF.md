# Civic Scout handoff (as of Oct 5, 2026)

Start a new session with the `jzselby/civic-scout` repository selected, then ask Claude to
read this file. `CLAUDE.md` (loaded automatically) has the working rules; `README.md` has
the full description.

## What it is

A daily GitHub Actions job that checks Utah public-records sources for new records, has
Claude rate each one for news value, links records across sources that share an address or
name, and writes a cross-source briefing. Output goes to a Google Sheet and to
`reports/YYYY-MM-DD.md`. It's meant for every beat in the newsroom.

- Repo: `jzselby/civic-scout` (private). Sibling, **read-only**: `jzselby/slcbuilding`.
- Sheet: [Civic Scout: Utah public records briefing](https://docs.google.com/spreadsheets/d/107ajJ93b3iGa6wKsbTwqi7jk0tNj_y_82Ymt0_me2Ag/edit)
  (id `107ajJ93b3iGa6wKsbTwqi7jk0tNj_y_82Ymt0_me2Ag`), owned by the owner, shared
  with the same service account slcbuilding uses.
- Schedule: `daily.yml` at 14:13, 14:47 and 15:31 UTC (about 8–9:30am Mountain); later
  attempts skip once the day's report exists. GitHub's scheduler runs late (Oct 4's run
  started at 18:06 UTC).
- Cost: roughly under $1/day in Claude usage; the first backlog run was about $0.85.

## How a run works

```
fetch each source → drop records already seen (data/<source>.jsonl)
  → records older than DAYS_BACK (14) are stored with reported=false, not reported
rate        Claude Opus 5.5: headline, importance, category, why it matters, names, places
editor      Claude Sonnet 5.5: skeptical second pass; changes carry an Editor's note
            (screener's rating kept as first_importance). It can hide a record (Low)
            only for routine_permit / duplicate / placeholder / procedural; otherwise
            code keeps it at Medium.
connect     normalized address/name matching across sources (link.py); a public body's
            meeting place (an address on 3+ of its notices) is ignored
brief       Claude Opus: one briefing per day covering all of the day's records
publish     sheet (idempotent by Key) + the day's one report file (rewritten by later
            runs that day) + data/ committed by the workflow
```

## The sheet

- **Briefings**: one row per day (later runs rewrite the day's row); rich text with links.
- **Top stories**: what reporters use. High (30 days) + Medium (7 days), newest first.
  Rebuilt each run.
- **All records**: archive, sorted newest first, with the Editor's note column. Link and
  Key columns are hidden.
- Formatting is applied once per `FORMAT_VERSION` (now "2") in `sheets.py`.

## Sources (all working)

| Key | Source | Notes |
|---|---|---|
| `pmn` | Utah Public Notice Website agendas | 15 bodies, ids in `sources/pmn.py` `DEFAULT_BODIES`; Claude reads attached agenda PDFs |
| `warn` | DWS WARN layoff notices | year headings fill missing years in dates |
| `slc_licenses` | SLC monthly new business license list | PDF only; newest month per run |
| `restaurants` | SL County health closures (cdpehs.com) | ASP.NET postback ("Closures" button); covers food, pools, lodging |
| `slc_permits` | slcbuilding's committed permit data | already rated there (`prerated`) |

Public Notice body ids: SLC Council 1360, SLC Planning Commission 1274, SLC Board of
Education 1067, Inland Port board 6413, SL County Council 709, SLC Historic Landmark
Commission 1266, liquor commission (DABS) 13, Granite 767, Jordan 738, Canyons 1281,
Murray 1094, State Board of Education 1499, Board of Higher Education 83, U of U
trustees 1376, SLCC trustees 1383, State Charter School Board 1495, SLC CRA 9033,
Fairpark district 8711, Point of the Mountain authority 6439, SL County Mountainous
Planning District 5341, Housing Connect 6223, Air Quality Board 38, Water Quality Board
40, Waste Management and Radiation Control Board 5281, Great Salt Lake Advisory Council
7937, Metropolitan Water District of SL & Sandy 885, SL County Board of Health 1498.

Regional bodies (`REGIONAL_BODIES` in `sources/pmn.py`; records carry `regional: true`
and reach Top stories and the briefing only when High, see `store.is_notable`): Utah
County Commission 2731, Provo council 1600, Orem council 734, Alpine SD 762, Davis
County Commission 1335, Layton council 315, Davis SD 736, Weber County Commission 2167,
Ogden council 6587, Weber SD 1144, Tooele County Council 7189, Summit County Council
1330, Utah Lake Authority 7775. The Wasatch Front Regional Council 2262 and the
Transportation Commission 60 are regular bodies, since they decide Salt Lake projects.

## Workflows

`daily.yml` (Actions → Civic Scout daily briefing → Run workflow), inputs:
- `sources` (blank = all), `days_back`, `dry_run` (print only, write nothing)
- `review_existing`: redo the editor's pass over the last 30 days from the screener's
  ratings, update the sheet and rewrite today's briefing (honors `dry_run`)
- `format_sheet`: re-apply formatting only; `refresh_connections`: recompute the
  Connections column only

`probe.yml` (Probe sources), inputs: `sources`, `pmn_bodies` (check body ids),
`pmn_notices` (look up the body id behind notice ids). Prints raw pages and parser output
to the job log.

Local: `pip install -r requirements.txt`, `python -m pytest` (32 tests), `python -m
civic_scout run --dry-run --no-summary`, `python -m civic_scout sources`.

## Open items

1. **UTA board not watched.** Body 940 is UTA's deactivated board; its current notices
   don't show under a new body yet. Find a recent UTA notice id and run the probe with
   `pmn_notices`.
2. **Editor calibration.** It now errs toward visibility (the owner's priority). Ratings
   vary a bit between runs (LLM nondeterminism). If Top stories grows too long on normal
   days, tune `REVIEW_PROMPT` rather than loosening `HIDE_REASONS`. Watch a week of
   normal (non-backlog) runs before changing anything.
3. **Business license list** is one item per month (PDF). Per-business items would
   improve address matching; the PDF's layout hasn't been inspected yet (probe it).

## Next sources the owner approved in principle

In this order:
- State Auditor and Legislative Auditor General audit reports (simple list pages).
- Campaign finance (disclosures.utah.gov), timely before the Nov. 3 election.
- Restaurant inspections with many critical violations (same cdpehs.com site).

Later: Legislature bill filings (glen.le.utah.gov, needs a token), DEQ permit notices,
water-rights applications, County recorder sales.

To add a source: a class in `civic_scout/sources/` with `name`, `label`, `guidance`,
`default_enabled`, `fetch()` and `probe()`; register it in `sources/__init__.py`; add a
fixture test. Use `org`, `place`, `names`, `places` so connections work.

## History worth knowing

- Oct 4 a scheduled run ran before the Anthropic key existed and marked everything seen
  unrated. That data was reset, and runs now refuse to record without a key
  (`--require-claude`).
- Two queued runs once collided (both checked out the queued commit). Now the job checks
  out the branch head at start, rebases before push, and publishing is idempotent.
- Council agendas listed City Hall as an address and "connected" to every City Hall
  permit. Meeting places are now ignored for matching.
