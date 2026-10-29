# Civic Scout handoff (as of Oct 21, 2026)

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
- Schedule: the Routine "Civic Scout daily briefing" (claude.ai, the owner's account) starts
  `daily.yml` (workflow_dispatch) at 7:21am America/Denver, so the briefing is ready by
  about 7:30. It is bound to Claude Code session `session_01FN8jTXZ59MQ5xf1m7mYxPX`,
  because that session has GitHub access and Routine-created sessions don't; don't
  archive that session, or the Routine has nowhere to run (then recreate it from a session
  with the repo attached, or from the claude.ai Routines page). It skips if the day's
  report exists or a run is going, and sends a push notification if it can't start the run.
  Until Oct 21 a task in the owner's other Claude account started it at about 8:18; stop
  that one once the Routine has run. slcbuilding's own Claude task ("SLC permit digest daily
  update", was 7:41am) moves to about 7:00am so its permits land (~5 minutes) first; if
  they're late they come in the next morning. `daily.yml`'s own schedule (14:47, 15:31,
  16:13 UTC) is only a backup: GitHub fires it 3–6 hours late for this repo, and each
  attempt skips once the day's report exists. A manual or Routine-started run never skips,
  so a second one the same day re-rates new records and rewrites the day's briefing.
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
            (sees the last 60 days of briefed high/medium records: "Earlier:" notes, repeats
            become one-line updates)
            (+ a "New restaurants & bars" section built by code from records' `openings`)
publish     sheet (idempotent by Key) + the day's one report file (rewritten by later
            runs that day) + data/ committed by the workflow
```

## The sheet

- **Briefings**: one row per day (later runs rewrite the day's row); rich text with links.
- **Top stories**: what reporters use. High (30 days) + Medium (7 days), newest first.
  Rebuilt each run.
- **Openings**: new restaurants, bars and cafes (90 days), one row per place with its
  steps; built from the `openings` Claude lists on each record (`openings.py`).
- **All records**: archive, sorted newest first, with the Editor's note column. Link and
  Key columns are hidden.
- Formatting is applied once per `FORMAT_VERSION` (now "2") in `sheets.py`.

## Sources (all working)

| Key | Source | Notes |
|---|---|---|
| `pmn` | Utah Public Notice Website agendas | about 45 bodies, ids in `sources/pmn.py` `DEFAULT_BODIES` and `REGIONAL_BODIES`; Claude reads attached agenda PDFs |
| `warn` | DWS WARN layoff notices | year headings fill missing years in dates |
| `slc_licenses` | SLC monthly new business license list | PDF only; newest month per run |
| `restaurants` | SL County health closures (cdpehs.com) | ASP.NET postback ("Closures" button); covers food, pools, lodging |
| `slc_permits` | slcbuilding's committed permit data | already rated there (`prerated`) |
| `slc_council` | SLC Council recaps (slc.gov RSS) and minutes (slc.primegov.com JSON) | minutes are dated by publish date, so the backlog is recorded as old, not rated |
| `campaign_finance` | disclosures.utah.gov CSV per year and filer type (candidates, PACs, issue committees, parties, corporations, independent expenditures) | one record per filed report, dated by its latest transaction; reports under $5,000 prerated low; amendments and transactions added after a report was first seen are not re-reported |

Public Notice body ids: SLC Council 1360, SLC Planning Commission 1274, SLC Board of
Education 1067, Inland Port board 6413, SL County Council 709, SLC Historic Landmark
Commission 1266, liquor commission (DABS) 13, Granite 767, Jordan 738, Canyons 1281,
Murray 1094, State Board of Education 1499, Board of Higher Education 83, U of U
trustees 1376, SLCC trustees 1383, State Charter School Board 1495, SLC CRA 9033,
Fairpark district 8711, Point of the Mountain authority 6439, SL County Mountainous
Planning District 5341, Housing Connect 6223, Air Quality Board 38, Water Quality Board
40, Waste Management and Radiation Control Board 5281, Great Salt Lake Advisory Council
7937, Metropolitan Water District of SL & Sandy 885, SL County Board of Health 1498, DEQ Division
of Water Quality 6493 (permit and settlement notices), UTA Transit Commission 9629 (replaced
the deactivated Board of Trustees 940 on July 1, 2026).

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

Local: `pip install -r requirements.txt`, `python -m pytest` (50 tests), `python -m
civic_scout run --dry-run --no-summary`, `python -m civic_scout sources`.

## Open items

1. **Editor calibration.** It now errs toward visibility (the owner's priority). Ratings
   vary a bit between runs (LLM nondeterminism). If Top stories grows too long on normal
   days, tune `REVIEW_PROMPT` rather than loosening `HIDE_REASONS`. Watch a week of
   normal (non-backlog) runs before changing anything.
2. **Business license list** is one item per month (PDF). Per-business items would
   improve address matching; the PDF's layout hasn't been inspected yet (probe it).
3. **Campaign finance first deadline.** The pre-general reports (Sept. 26–Oct. 22) are due
   Oct. 27, 2026: expect a few hundred reports on Oct. 27–28, most rated by Claude. Check
   that day's run cost and Top stories length. The CSV has no office or race; Claude
   names it only when it's clear. A filer's later amendment keeps the same id and isn't
   re-reported.

## Next sources (scouted Oct 21, 2026)

Checked live through the probe workflow on Oct 21 unless marked otherwise. In order:
1. **State Auditor reports** (`auditor.utah.gov/audit-reports/recent-audit-reports/`,
   server-rendered list; its wp-json API returns 403) and **Legislative Auditor General**
   audits (`olag.utleg.gov/audits_all.jsp`).
2. **Utah appellate opinions**: RSS at `legacy.utcourts.gov/rss/opinions/index.xml`
   (5–10 a week; PDF per opinion).
3. **Public Service Commission dockets**: `psc.utah.gov/wp-json/wp/v2/posts` lists each new
   docket with its date (Rocky Mountain Power is the 26-035 series; Enbridge gas 057).
4. Small list pages, one module: Judicial Conduct Commission dispositions
   (`jcc.utah.gov/public-dispositions/`), governor's executive orders
   (`governor.utah.gov/executive-orders/`), initiatives and referenda
   (`vote.utah.gov/initiatives-and-referenda`), admin rules (`rules.utah.gov/feed/`), SL
   County DA officer-involved incidents (`saltlakecounty.gov/district-attorney/oici---media/`,
   list not parsed yet), Government Records Office (GRAMA appeal) decisions
   (`archives.utah.gov/transparency-services/government-records-office/`).
5. **Legislature bills** for the 2027 session (opens Jan. 19; bills numbered from
   December). Needs a developer token (owner: request at le.utah.gov/data/developer.htm).
   The interim calendar JSON (`le.utah.gov/CalServ/CalServ?month=<0-11>&year=<y>`) works
   without one.
6. Later: U3P/Bonfire bids (`utah.bonfirehub.com/PublicPortal/getOpenPublicOpportunitiesSectionData`,
   JSON after a GET of `/portal/?tab=openOpportunities`; 208 open from 52 agencies incl. SLC,
   UTA, school districts; needs filtering), water-rights change applications
   (`waterrights.utah.gov/applicationsrecords/AppTracker.asp`, HTML table), DAQ permit
   applications (`daqpermitting.utah.gov`), new federal cases in D. Utah (CourtListener
   search API, no key), restaurant inspections with many critical violations (cdpehs.com).

**Transparent Utah** (State Auditor) has no public API: bulk transactions are in BigQuery
(`ut-sao-transparency-prod.transaction.transaction`) for "Super Users" the auditor's office
approves (owner: ask, and whether a service account can be granted). Fits a weekly job:
large payments by Salt Lake County entities, new vendors. Its compliance report
(`old.transparent.utah.gov/compliance.php`: entities delinquent / on hold) is filled in by
JavaScript; find its data endpoint (`static/js/compliance.js`) before building on it.

Skip: opendata.utah.gov (decommissioned), maps.slc.gov services (token required), the
County recorder (paid), court calendars, jail bookings, UDOT, SEC EDGAR (blocks the
probe's User-Agent; fixable if wanted), Federal Register (noisy).

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
