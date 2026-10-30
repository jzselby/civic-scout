# Civic Scout

A daily watcher for public records from Salt Lake City, Salt Lake County and the state
of Utah. Each morning it checks a set of public sources for new records, has Claude
rate each one for news value, links records from different sources that share an
address or a name, and writes a short cross-source briefing for the newsroom. The
results go to a Google Sheet and a Markdown report in `reports/`.

It's meant for every beat. Nothing is tuned to one reporter; what counts as "high
importance" is written down per source, in plain editor's language, in each
source's `guidance` (see `civic_scout/sources/`).

## Sources

| Source | Key | What it is | Status |
|---|---|---|---|
| Utah Public Notice Website | `pmn` | Agendas and notices from public bodies: SLC Council, SLC Planning Commission, SLC Historic Landmark Commission, Inland Port Authority, the state liquor commission (DABS), Salt Lake County Council; school boards (SLC, Granite, Jordan, Canyons, Murray), the State Board of Education, the State Charter School Board, the Board of Higher Education, and U of U and SLCC trustees; development and housing bodies (SLC Community Reinvestment Agency, Fairpark district, Point of the Mountain authority, SL County Mountainous Planning District, Housing Connect); environment and health boards (state Air Quality, Water Quality, and Waste Management and Radiation Control boards, Great Salt Lake Advisory Council, Metropolitan Water District of SL & Sandy, SL County Board of Health); transportation (Wasatch Front Regional Council, state Transportation Commission, UTA Transit Commission); and DEQ's Division of Water Quality public notices (draft permits, water quality certifications, settlements). Regional bodies (Utah, Davis, Weber, Tooele and Summit county commissions/councils; Provo, Orem, Layton and Ogden councils; Alpine, Davis and Weber school boards; Utah Lake Authority) are rated too, but only their High records reach Top stories and the briefing. Claude reads the attached agenda PDFs. | Working |
| WARN layoff notices | `warn` | Employers' advance notice of mass layoffs and closures (Dept. of Workforce Services) | Working |
| SLC new business licenses | `slc_licenses` | The city's monthly list of businesses that applied for a license (PDF only; Claude picks out notable names) | Working |
| SL County health closures | `restaurants` | Health Department closures for imminent health hazards (restaurants, food trucks, pools, lodging), with each place's inspection report for the reason | Working |
| SLC permits & planning | `slc_permits` | Commercial building permits and Planning applications, imported **read-only** from the [slcbuilding](https://github.com/jzselby/slcbuilding) project's data, already rated there | Working |
| SLC Council actions | `slc_council` | What the SLC Council (and CRA board) decided: council staff's meeting recap, posted the day after each meeting (slc.gov), and the official minutes from the city's PrimeGov portal, which post about 100 days later and are rated only for what's still news (split votes, changes from the agenda) | Working |
| Campaign finance reports | `campaign_finance` | Reports filed with the Lieutenant Governor (disclosures.utah.gov) by state candidates, PACs, ballot-issue committees, parties, corporations and independent spenders: one record per filed report, with totals and the largest contributions and expenditures. Reports moving under $5,000 are recorded as routine without Claude | Working |
| Sugar House Community Council | `sugar_house` | The neighborhood council's monthly meeting agendas, its AI-drafted meeting summaries and news posts, and its Land Use and Zoning committee's reports and petition posts (sugarhousecouncil.org category RSS feeds, full text). Developers and the city often present Sugar House projects here before the Planning Commission sees them | Working |

`python -m civic_scout sources` lists them. Next sources, in order: State Auditor and
Legislative Auditor General reports, Utah appellate opinions, Public Service Commission
dockets, the Legislature's bill filings; see `HANDOFF.md` for the full scouting notes.

## How a run works

```
for each source:      fetch the current list → drop records already seen
                      → records dated before the look-back window (default 14 days)
                        are remembered but not reported, so a first run isn't a flood
                      → routine records come in as Low: apartment and hotel pool
                        closures, and trade permits (under $1M) at a place that
                        already has a record; the first record about a place keeps
                        its rating
rate (Claude Opus)    headline, importance (high/medium/low), category, why it
                      matters, plus the names and addresses each record mentions,
                      and any new restaurant, bar or cafe it shows is coming
                      (pre-rated permits get a short Claude Sonnet pass for this)
editor (Claude Sonnet) a skeptical second pass over every rating, as an assignment
                      editor: "high" means a reporter should start calls today, so
                      routine trade permits, repeat items about one project and past
                      meetings get lowered; each change carries an Editor's note.
                      It can only hide a record (lower it to Low, off Top stories)
                      for a routine reason: a trade permit for a known project, a
                      duplicate, a placeholder, or procedural paperwork. Otherwise
                      the code keeps the record at Medium, where reporters see it.
                      The first record of a new restaurant isn't a routine permit.
connect               normalize addresses ("1124 East 100 South, Unit 3" → "1124 E 100 S")
                      and names ("Postino, LLC" → "POSTINO"); match records across
                      sources, over the past year
brief (Claude)        Top stories (up to 8, none on a slow day) · New restaurants &
                      bars (written by code: one line per Salt Lake County place in
                      today's records, SLC first, with when it first turned up) ·
                      Connections ·
                      Coming up (hearings and comment deadlines first) · Also notable ·
                      Results to check (high/medium agenda items from meetings held
                      since the last weekday, so a reporter checks what was decided).
                      Claude also sees the high/medium records from the last 60 days
                      of briefings: an item that continues one gets an "Earlier:" note
                      linking it, and a repeat (new minutes of a meeting already
                      reported) becomes a one-line "Update" under Also notable. The
                      report's "Connections across records" lists address matches
                      only; the sheet's Connections column keeps name matches too.
publish               Google Sheet + reports/YYYY-MM-DD.md + data/<source>.jsonl
```

There's one report per day: a second run (or an editor re-review) the same day rewrites
`reports/YYYY-MM-DD.md` to cover all of the day's records, like the sheet's Briefings row.

A source that fails (site down, layout changed) is marked FAILED in the report's
source table; the others still run. If the sheet write fails, nothing is marked as
seen, so the next run retries.

```
civic_scout/
  sources/      one module per source (+ text_utils for dates, PDFs, spreadsheets)
  pipeline.py   collect → rate → connect → brief
  analyze.py    the two Claude calls and their prompts
  link.py       address/name normalization and cross-source matching
  openings.py   new restaurants and bars: grouping records by place, the briefing section
  report.py     the Markdown report
  sheets.py     the Google Sheet
  store.py      data/<source>.jsonl: every record already seen
```

## The sheet

- **Briefings**: one row per day, newest first, with the cross-source briefing. If more
  than one run happens in a day, the day's row is rewritten to cover all of them. Headings
  are bold and every source in it is a clickable link.
- **Top stories**: the tab reporters work from: High records from the last 30 days and
  Medium ones from the last 7, newest first and High before Medium. Rebuilt every run,
  so don't type notes here.
- **Openings**: new restaurants, bars and cafes from the last 90 days, one row per
  place, latest first, with each step found so far (building permit, business license,
  liquor license) and the Area (Salt Lake City, Salt Lake County, elsewhere). Places
  outside Salt Lake County appear only here, not in the briefing. Rebuilt every run.
- **All records**: every new record, sorted newest first. When the editor pass changed
  a rating, the **Editor's note** column says why. Add your own columns (Notes,
  Assigned to); runs write by column heading and leave other columns alone. Don't
  rename the built-in headings.

How it's styled, for scanning:

- Rows from the **latest run** are shaded light blue.
- **High** importance is marked in red on the Importance cell, **Medium** in amber;
  **Low** (routine) rows are gray.
- Each **headline links** to the original record. The Link and Key columns are hidden
  (Data > unhide to see them).
- Every column has a filter, and the header row stays in view. For a personal view
  that doesn't change anyone else's, use **Data > Filter views**.

The formatting is applied once per version (`FORMAT_VERSION` in `sheets.py`), so
formatting your team adds by hand afterwards stays. To apply it immediately, run the
daily workflow with **format_sheet** ticked.

## Setup

1. **Google Sheet.** Create a blank sheet and share it (Editor) with the Google Cloud
   service account's email. The slcbuilding service account works fine. The sheet id
   is the long string in its URL.
2. **Secrets** (Settings → Secrets and variables → Actions):

   | Secret | Value |
   |---|---|
   | `ANTHROPIC_API_KEY` | for ratings and the briefing (without it, records are still collected) |
   | `GOOGLE_SHEET_ID` | the new sheet's id |
   | `GOOGLE_SERVICE_ACCOUNT_JSON` | the service account key file's contents |

   Optional **variable** `PMN_BODIES` replaces the default list of public bodies:
   comma-separated ids from utah.gov/pmn body pages, e.g. `1360,1274=SLC Planning Commission`.
3. **Probe first.** Actions → *Probe sources* → Run workflow. It saves raw pages from
   every source and logs what each parser makes of them.
4. **Test run.** Actions → *Civic Scout daily briefing* → Run workflow with *dry run*
   ticked, then once for real. After that a Claude Routine starts it every morning at
   7:21am Mountain (see `HANDOFF.md`), after slcbuilding's update (moved to about 7:00am so its permits
   are in). The workflow's own schedule is only a backup: GitHub fires it hours late.

## Run it locally

```bash
pip install -r requirements.txt
python -m civic_scout run --dry-run --no-summary        # no Claude, no writes
python -m civic_scout run --sources pmn,warn --dry-run  # just some sources
python -m civic_scout probe --out probe                 # save raw pages
python -m civic_scout review-existing --dry-run         # what the editor would change
python -m pytest
```

Settings (environment variables): `SOURCES`, `DAYS_BACK`, `SUMMARY_MODEL` (default
`claude-opus-5-5`), `REVIEW_MODEL` (the editor pass; default `claude-sonnet-5-5`, empty
turns it off), `PMN_BODIES`, `SLCBUILDING_URL`, `HTTP_TIMEOUT`.

## Adding a source

Write a class in `civic_scout/sources/` with `name`, `label`, `guidance`,
`default_enabled`, `fetch()` and `probe()` (see `sources/base.py` for the item
fields), add it to `sources/__init__.py`, and add a parser test with a saved page in
`tests/fixtures/`. Use `org`, `place`, `names` and `places` wherever the record has
them; that's what cross-source connections match on.

Be a polite scraper: the shared HTTP client identifies itself, retries gently and
waits a second between requests to the same site.
