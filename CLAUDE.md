# CLAUDE.md: forecast_engine

## Project context
- Read `docs/spec.md` before starting any task; it is the source of truth for scope and design.
- Current phase: **M1 baseline bot** (M0 done 2026-09-29: spec §7 + ADRs 0001–0005). Build per
  `docs/session-prompts/M1-baseline-bot.md`, following ADR-0005 (own core behind a thin template adapter).

## Governing rule
- **Evaluation-first:** no change reaches the live bot without evidence under `docs/evaluation-protocol.md`. Write the
  experiment card (`docs/experiments/TEMPLATE.md`) before running anything. Never touch HOLDOUT without logging it. Never
  weaken a leakage control or delete its test to make something pass; stop and ask instead.

## Working style (agreed with Christian)
- Be critical. Challenge ideas that are weak, half-baked or not worth the effort, and say so plainly with
  reasons and a better alternative. Don't agree just to be agreeable. (I-02)
- Separate facts (with sources) from judgement.

## Inbox
- Christian dumps raw input in `docs/inbox.md`. On "triage the inbox", crystallise each new entry into
  `docs/ideas.md` with an honest assessment, carry settled items into the spec, and mark entries ✅.

## Trackers (keep current; they're the project's status)
- `docs/backlog.md`: prioritised backlog. At the end of every session, update statuses, move finished items to Done with a
  result, and re-rank if priorities changed. New work must link to R-xx / I-xx / a milestone.
- `docs/playbook-tracker.md`: recommendations → what we built → status → honest assessment + measured results.
  Update whenever the bot changes or results come in. Add results to its Results log.
- `docs/log/YYYY-MM-DD.md`: short session log (done, decisions, next actions).
- Dashboard (read-only view of the two trackers): `python tools/build_dashboard.py` writes `dashboard/index.html`.
  The published copy is the Claude artifact https://claude.ai/artifact/KcQC4LVaCvJ4YezyNwkRJE. To refresh it, rebuild,
  then republish to that URL from a Claude session that has the Artifact tool (pass the URL so it updates in place).
  Christian comments on items in the dashboard. On "process the dashboard input" (a Claude session with artifact tools): read
  the comment threads, carry each one into the repo (backlog / tracker / ideas / inbox), reply in the thread saying what changed,
  resolve it, then rebuild and republish. Claude can only reply to and resolve threads that were sent to Claude
  ("Send to Claude" or @claude in the thread); plain comments are read and acted on, but stay open for Christian to resolve.

- Scheduling: GitHub cron is unreliable (~8% delivered), so a cron-job.org job (Christian's account) triggers the tournament workflow
  every 10 min via `workflow_dispatch`, with a fine-grained token that expires **2027-10-01**.
- Run records are uploaded as artifacts **encrypted** with `ARCHIVE_KEY` (in `.env` and GitHub secrets; never upload plaintext:
  they contain AskNews text and the code repo is public). Losing the key makes not-yet-synced artifacts unreadable.
- Bot monitor (forecasts, model answers, research, costs per run): `poetry run python tools/build_monitor.py` downloads new
  GitHub Actions run artifacts into `data/runs/` (gitignored, kept forever; artifacts on GitHub expire after 90 days) and writes
  `dashboard/monitor.html`. Published copy: https://claude.ai/artifact/JPhLSguYoWVJzs3AHNJDLS. On "refresh the monitor": run it, then republish to that URL. A Windows scheduled
  task "forecast_engine archive runs" on Christian's PC runs `--sync-only` daily at 09:00 and at logon (also saves each run's log);
  it appends to `data/runs/_sync.log`. `data/` is its own git repo, pushed after each sync to the
  **private** repo ChrRaaby/forecast_engine_data (never make it public: it holds licensed news text).

- Model per task (Christian, 2026-10-03): every backlog item carries a `Model` label: Haiku 4.5 · PC (mechanical), Opus 5.5 · PC
  (development; in Christian's subscription), Opus 5.5 · cloud (frontier items: a standalone cloud session started by Christian, paid
  by the free $100 cloud credit until 2026-11-05; the credit covers neither project threads nor Fable). Label new items when adding
  them; revisit the split when the credit ends.

## How to work
- For any non-trivial task: propose a plan first, wait for approval, then implement.
- Work one milestone at a time; keep changes small and reviewable.
- When a decision is made (library, data source, model approach, interface), write an ADR in
  `docs/decisions/` using `0000-template.md`, and update the spec if it changes scope or design.
- Keep "Open questions" in the spec current: add new ones, and remove answered ones with a pointer to where they were answered.
- Research findings go in `docs/research/<topic>.md` with sources linked.

## Conventions
<!-- Fill in once decided: language/version, package manager, formatter/linter, test framework,
     folder layout, commands for build/test/run. -->
- Stack: Python 3.11+, Poetry, Metaculus template + `forecasting-tools`, OpenRouter (ADRs 0001, 0003)
- Commands (Poetry is at `~/.local/bin/poetry` on Christian's PC; `.venv/` is in-project):
  - install: `poetry install --no-root`
  - test (offline, network blocked): `poetry run pytest`
  - check model IDs against the live lists: `poetry run python tools/check_models.py`
  - dry run: `poetry run python main.py --mode test_questions --max-questions 3`
  - live (what GitHub Actions runs): `poetry run python main.py --mode tournament --publish`, plus 5×/day
    `--mode wide --publish --main-site-max 2` (Metaculus Cup + main-site questions, ADR-0007)

## Don'ts
- Don't commit secrets, credentials, or real/sensitive data. Use synthetic or public sample data.
- Don't add dependencies without noting why (ADR for anything significant).
