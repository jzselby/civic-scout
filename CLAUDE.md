# Civic Scout: notes for Claude

Daily watcher of Salt Lake City / Salt Lake County / Utah public records for a newsroom.
Read `README.md` for how it works and `HANDOFF.md` for current state, open items and next
steps. This file is the working rules.

## Rules

- **Never modify `jzselby/slcbuilding`.** It's a separate project (the SLC permit digest).
  Civic Scout only reads its public `data/permits.jsonl` (source `slc_permits`).
- **Built for every beat, not one reporter.** What counts as High is written per source in
  each source's `guidance` and in the editor prompt (`analyze.REVIEW_PROMPT`); keep it
  general-newsroom.
- **Recall over precision.** The owner's priorities: (1) never miss anything important,
  (2) reporters work only from the **Top stories** tab (High 30 days + Medium 7 days).
  The editor may hide a record (Low) only for a routine reason; code enforces this in
  `analyze.apply_review` (`HIDE_REASONS`). Don't loosen that guard.
- Runs must stay **safe to repeat**: publishing is idempotent by `Key` (`source:id`), one
  Briefings row per day, records that Claude failed to rate are held, not marked seen.
- Write code that matches the surrounding code; add a test with a saved page in
  `tests/fixtures/` for any parser change. `python -m pytest` must pass before pushing.
- Commit messages: imperative subject, a short body on why.

## Environment gotchas (cloud sessions)

- **The sandbox can't reach Utah government sites** (utah.gov, slc.gov, jobs.utah.gov,
  public.cdpehs.com are blocked by the egress policy). Only GitHub is reachable. To see a
  live page, run the **Probe sources** workflow and read its job log with the GitHub MCP
  `get_job_logs` (artifact downloads are blocked too). Large logs are saved to a file;
  extract with Python.
- The GitHub MCP can't create repositories; the owner creates them.
- Secrets (`ANTHROPIC_API_KEY`, `GOOGLE_SHEET_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON`) exist
  only in GitHub Actions, so real runs (Claude, the sheet) happen there, triggered with
  `actions_run_trigger` on `daily.yml` / `probe.yml`.
- Don't queue two `daily.yml` runs that both write data at once without need; the workflow
  checks out the branch head at job start and rebases before pushing, but each run costs
  Claude credits.
