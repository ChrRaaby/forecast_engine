# Backlog

_Prioritised, top = next. Maintained by Claude (Code or chat) at the end of every session. Last updated: 2026-10-06_

**Rules**
- One row per item. `Rank` is the order within a section. Re-rank rather than adding "urgent" labels.
- `Why` links each item to a playbook recommendation (R-xx, `docs/playbook-tracker.md`), an idea (I-xx, `docs/ideas.md`)
  or a milestone (spec §8). An item with no *why* doesn't belong here.
- Status: `Todo` · `Doing` · `Done` · `Blocked` · `Dropped`. Done items move to the Done section with the date and a result.
- Follow-ups & reminders: dated or waiting actions that aren't development (renewals, checks, decisions) live in their own section
  below, so they don't hide in session logs.
- Size: S (≤2 h) · M (≤1 session ~4 h) · L (several sessions).
- Model (Christian, 2026-10-03; revised 2026-10-05 to use smaller models for simpler work): the Claude model that should do the work,
  and where it runs. **Haiku 4.5 · PC**: mechanical work (refreshes, small edits, data pulls, follow-up checks). **Sonnet 5.5 · PC**:
  routine development and experiments with clear specs. **Opus 5.5 · PC**: design-heavy development where the approach isn't settled.
  **Opus 5.5 · cloud**: frontier items where getting the design right is worth the most, as a standalone cloud session started by
  Christian on the free $100 credit until 2026-11-05. Pick the smallest model that can do the task well; label every new item.

## Now: M1 baseline bot live
| Rank | ID | Item | Why | Owner | Size | Status | Model |
|---|---|---|---|---|---|---|---|
| 1 | B-04 | Template bot live on Fall 2026 AIB + MiniBench: 3 cheap models from different vendors, median (scenario B). Live since 2026-10-01 (cron-job.org trigger since 2026-10-02). No FutureEval/MiniBench questions released yet (next MiniBench round 2026-10-05); wide mode published 18 Cup/main-site forecasts 2026-10-03. Close once it has forecast real tournament questions | M1; R-04, R-15 | Claude Code | M | Doing | Haiku 4.5 · PC |

## Next: reliability → evaluation harness (M2) → experiments
_Evaluation-first: no experiment changes the live bot until the harness (B-27, B-29…B-32) passes its validation. See `docs/evaluation-protocol.md`._

| Rank | ID | Item | Why | Owner | Size | Status | Model |
|---|---|---|---|---|---|---|---|
| 1 | B-08b | Weekly 30-min review of new forecasts (same checks as `docs/research/09-first-forecast-review.md`; add the polarity-guard and research-date flags once B-51 lands) | R-14, R-17, R-18 | Christian | S | Todo | Sonnet 5.5 · PC |
| 2 | B-51 | Research date sanity check: flag or drop research items with a weekday/date mismatch, a date after `as_of`, or a stale marketing/fiscal year (found in 7/82 bundles, all AskNews); store Gemini grounding URLs so single-figure claims are auditable. Proposed in research/09, needs Christian's OK | R-14, R-17; research/09 | Claude Code | S | Todo | Sonnet 5.5 · PC |
| 3 | B-52 | Choices ledger: one row per setting that shapes a forecast (models, aggregation, clip, prompt, research sources, reasoning effort, guards, caps) with today's value, where it is decided (ADR / log) and evidence strength (Tested / Literature / Inherited / Engineering) plus the experiment that would test it. Generated from `config.py` + a small evidence file (e.g. `docs/choices.yaml`); a hygiene test fails when a forecast-affecting config field has no row. Shown as a section in the dashboard | I-17 | Claude Code | S | Todo | Sonnet 5.5 · PC |
| 4 | B-53 | Weekly brief: one page each Monday, written for Christian: every CONFIG_VERSION change in plain words with before/after, new ADRs, experiment results, incidents, spend, the X-ray of the week (B-46) and the scorecard (B-55) once filled. Built from git log, config diffs and monitor data; republished to one link by a weekly routine. Convention that feeds it: every CONFIG_VERSION bump adds a one-line "what you'd notice" entry (e.g. `docs/config-changes.md`) | I-17 | Claude Code | M | Todo | Sonnet 5.5 · PC |
| 5 | B-09b | Experiment EXP-002: question close/resolution dates in the prompt (card written; runs on the M2 harness) | R-18; B-09 | Claude Code | S | Todo | Sonnet 5.5 · PC |
| 6 | B-10 | Re-run the cost model with measured tokens; confirm the $/question for scenario B | research/03 | Claude | S | Todo | Haiku 4.5 · PC |
| 7 | B-39 | Metaculus Bot Benchmarking Access Tier (~250 resolved + ~250 open questions with outcomes and CP): Data Needs Form submitted 2026-10-03; waiting for Metaculus | I-14; research/05 | Christian | S | Blocked | Haiku 4.5 · PC |
| 8 | B-27 | Evaluation models + release dates; question-supply census; frozen DEV/HOLDOUT manifests with eligibility filter. Pick older eval models to widen the eligible pool (I-14) | Protocol §3, T1, T6; R-25; I-14 | Claude Code | M | Todo | Sonnet 5.5 · PC |
| 9 | B-46 | Reasoning graph for inspection: after each forecast a cheap model (~$0.001/q) extracts each member's graph (base rate → drivers/cruxes → evidence with source/date/direction/strength → scenarios → probability), stored with the record (extends B-20), shown side by side in the monitor. Includes the **Question X-ray** view (I-17): each week the 1–2 questions with the widest model spread laid out end to end (research, each model's number and key quote, guard verdicts, final), and on request for any question ("show me question 45925"). Doesn't change forecasts; design confirmed with Christian before coding | I-16, I-12, R-14; research/08 | Claude Code | M | Todo | Opus 5.5 · PC |
| 10 | B-54 | Living pipeline map: architecture diagram generated from `config.py` and `core.py`'s step order, current values printed in each box and evidence colour from the ledger (B-52); a changed box is highlighted in the weekly brief. Section in the dashboard or monitor | I-17 | Claude Code | S | Todo | Sonnet 5.5 · PC |
| 12 | B-55 | Scorecard + ensemble health tab in the bot monitor, on B-38's outcomes: resolved N, Brier/log score vs community with CIs, calibration curve once N ≥ 30, each model alone, frontier vs cheap shadow (ADR-0009); plus the per-question model spread chart (who sits far from the median, mean level per model). Uses B-29's scorer; shares metrics with B-47 | I-17; B-38 | Claude Code | M | Todo | Sonnet 5.5 · PC |
| 13 | B-30 | Experiment runner: immutable run records, config hashing, clean-git check, cost guard, holdout guard, repeats k | Protocol §7, T8, T11 | Claude Code | M | Todo | Sonnet 5.5 · PC |
| 14 | B-31 | Report generator: paired cluster-bootstrap CIs, MDE, calibration plot, per-type breakdown, cost → `docs/experiments/` | Protocol §5, T9, T12 | Claude Code | M | Todo | Opus 5.5 · cloud |
| 15 | B-47 | Diversity metrics in every experiment report: member error correlation, member spread, diversity bonus (ensemble vs mean member); on top of B-31 | I-16; research/08 | Claude Code | S | Todo | Sonnet 5.5 · PC |
| 16 | B-32 | Harness validation: reproducibility, A/A test, leakage-injection test, trivial baselines | Protocol §7, T13 | Claude Code | M | Todo | Opus 5.5 · cloud |
| 17 | B-33 | EXP-001: single-call baseline vs. M1 config on DEV. Measure the real SD → MDE; set the PoC gate margin | Protocol §5; I-08 | Claude Code + Christian | M | Todo | Opus 5.5 · cloud |
| 18 | B-48 | EXP-003: extremize / cap / Platt sweep on resolved binary records (post-processing, $0; covers the B-11 and B-19 evaluations). Needs N ≥ ~150 resolved binary, B-38, B-31 | I-16, R-05, R-07; research/08 | Claude Code | S | Todo | Sonnet 5.5 · PC |
| 19 | B-49 | EXP-004: diversity of perspective, 4 arms (baseline / structured method / lens per member / research per member) on frozen research. Needs B-38, B-31, B-47 | I-16, R-04, R-20; research/08 | Claude Code | M | Todo | Opus 5.5 · PC |
| 20 | B-50 | EXP-005: graph-first reasoning vs free text (after EXP-004) | I-16, I-12, R-21; research/08 | Claude Code | M | Todo | Opus 5.5 · PC |
| 21 | B-11 | Experiment: prediction capping (e.g. clip binary to [0.02, 0.98]) | R-07 | Claude Code | S | Todo | Sonnet 5.5 · PC |
| 22 | B-12 | Experiment: explicit base-rate step + similar resolved questions in the prompt (no "Bayesian" wording) | R-08, R-09, R-21 | Claude Code | M | Todo | Sonnet 5.5 · PC |
| 23 | B-13 | Experiment: second research source (as-of prediction-market prices) | R-06, research/04 | Claude Code | M | Todo | Sonnet 5.5 · PC |
| 24 | B-15 | Experiment: numeric-question pipeline vs. template default | R-19 | Claude Code | M | Todo | Sonnet 5.5 · PC |

## Later: M3 better bot (after the gate)
| Rank | ID | Item | Why | Owner | Size | Status | Model |
|---|---|---|---|---|---|---|---|
| 1 | B-16 | Frontier ensemble for tournament questions (ADR-0009): pick the roster (diverse vendors incl. Grok and a Chinese model; candidates in the ADR), season spend cap $300 in code with fallback to the cheap config, cheap config as unpublished shadow on every tournament question, shadow vs live on the monitor. Switch after B-08 + ~1 week of clean MiniBench runs | R-01, R-10, R-11; ADR-0009 | Claude Code | M | Todo | Sonnet 5.5 · PC |
| 2 | B-17 | Agentic, iterative research loop | R-03, R-02 | Claude Code | L | Todo | Opus 5.5 · cloud |
| 3 | B-18 | Add a decorrelating ensemble member (e.g. Grok) and test 3 vs. 4–5 members | R-04, R-13 | Claude Code | S | Todo | Sonnet 5.5 · PC |
| 4 | B-19 | Platt-scaling experiment on backtest + live data (contested) | R-05 | Claude Code | M | Todo | Sonnet 5.5 · PC |
| 5 | B-20 | Structured forecast-record schema (claims → evidence → source + date, base rate, cruxes), graph-loadable | I-12 | Claude Code | M | Todo | Sonnet 5.5 · PC |
| 6 | B-21 | Local 4090 open-weight model as a free backtest baseline / ensemble member | I-04, R-12 | Claude Code | M | Todo | Sonnet 5.5 · PC |
| 7 | B-22 | Jev as a cheap news-relevance filter (A/B in the backtest) | I-03 | Claude Code | M | Todo | Sonnet 5.5 · PC |
| 8 | B-34 | Backtest ↔ live agreement check per config era (≥ 50 resolved live Qs) | Protocol §9 | Claude Code | S | Todo | Sonnet 5.5 · PC |

## Follow-ups & reminders
_Dated or waiting items that aren't development work. Checked at the start of every session; done ones move to Done._

| ID | Item | Why | Date | Owner | Status | Model |
|---|---|---|---|---|---|---|
| F-02 | Check the Google billing console for the per-search grounding fee (not in our cost records) | B-10; ADR-0006 | after a few days live | Christian | Todo | Haiku 4.5 · PC |
| F-03 | Check the workflows still run after GitHub moves `ubuntu-latest` to Ubuntu 26 (and Node 20 actions are forced to Node 24) | Ops | 2026-10-19 | Claude | Todo | Haiku 4.5 · PC |
| F-04 | Use the free $100 cloud credit for the Opus 5.5 · cloud items before it expires | Model rule | by 2026-11-05 | Christian | Todo | Haiku 4.5 · PC |
| F-05 | AskNews Pro promo (100% off for 4 months) ends: decide whether to pay $7.99/month or fall back | research/03 | ~2027-01 (check the billing page) | Christian | Todo | Haiku 4.5 · PC |
| F-06 | Renew the cron-job.org GitHub token and paste it into the job | B-06 | before 2027-10-01 | Christian | Todo | Haiku 4.5 · PC |
| F-07 | Update the cron-job.org `X-GitHub-Api-Version` header (2022-11-28 is deprecated) | B-06 | before 2028-03-10 | Christian | Todo | Haiku 4.5 · PC |
| F-09 | Fresh confirmation sample (~20 new questions, 100 credits) for the dual leakage screen, required before the first large archive batch, since its settings were chosen after seeing the B-45 confirmation set | B-45; research/07 | after 2026-10-30 (AskNews reset) | Claude | Todo | Sonnet 5.5 · PC |
| F-10 | Raise the OpenRouter key limit before it runs out (no Metaculus credits). $4.29 of $20 used on 2026-10-05; ~$0.30/day with MiniBench live (incl. ~$3 of one-off B-45 QA), so roughly early-to-mid November. Stay within the $100 PoC budget | B-01; spec §6 | before ~2026-11-10 | Christian | Todo | Haiku 4.5 · PC |
| F-11 | Confirm the polarity guard on live records: after ~10 binary records under `m1-baseline-2026-10-05`, check the `polarity` verdicts and the exclusion rate (review found Gemini slipping on ~5% of binary answers) | B-08; research/09 | after ~2026-10-08 | Claude | Todo | Haiku 4.5 · PC |
| F-08 | Keep `ARCHIVE_KEY` safe outside `.env` too (e.g. a password manager): unsynced artifacts are unreadable without it | ADR-0002 | now | Christian | Todo | Haiku 4.5 · PC |

## Side branch: trading (paper only, no real money)
_Gated on the M2 harness (B-29, B-31, B-38). Real stakes only after the B-42 gate, the B-44 check and Christian's explicit go-ahead. See `docs/research/06-trading-branch.md`._

| Rank | ID | Item | Why | Owner | Size | Status | Model |
|---|---|---|---|---|---|---|---|
| 1 | B-41 | Market question feed + price-snapshot logger: liquid binary markets (Polymarket, Manifold, Kalshi read-only APIs) as a question source for the core; record mid, bid/ask and volume at forecast time; price kept out of the prompt | I-15; research/06 | Claude Code | M | Todo | Sonnet 5.5 · PC |
| 2 | B-42 | Paper-trading evaluator: edge vs. market price, fractional Kelly sizing, fees and spread, bootstrap CI (reuses B-31); defines the go/no-go gate | I-15; research/06 | Claude Code | M | Todo | Sonnet 5.5 · PC |
| 3 | B-43 | Feasibility check: can Polymarket/Manifold price history give leakage-free paired forecast/price backtests? | I-15; research/06 | Claude | S | Todo | Haiku 4.5 · PC |
| 4 | B-44 | Denmark legality and tax check for prediction-market trading (gather sources; Christian decides) | I-15; spec §9 | Christian | S | Todo | Haiku 4.5 · PC |

## Parked
| ID | Item | Why parked | Revisit when |
|---|---|---|---|
| B-23 | Graph DB for analysing past forecasts, then as a reasoning aid | Needs B-20 data first | After M2 |
| B-24 | Fine-tune an open-weight model on resolved questions (4090) | Promising (R-12) but a large effort | After M3, if gap to frontier remains |
| B-25 | Prediction-market trading with real money | Non-goal for v1; legal/tax check needed. Paper-trading side branch: B-41…B-44 | After the B-42 gate and B-44 check |
| B-26 | `docs/research/01-silver.md`: Silver notes | Low urgency | When the book is finished |

## Done
| ID | Item | Date | Result |
|---|---|---|---|
| B-29 | Scorer package (`evals/scoring.py`, `tests/test_scoring.py`) | 2026-10-06 | Pure functions: binary log/Brier/baseline, MC log/Brier/baseline, numeric baseline (Metaculus's 202-bucket pmf rule), numeric log score and exact CRPS on the 201-point CDF, 10-bin calibration, ECE, sharpness. 23 tests (golden, propriety, edge cases) pass offline. Formulas verified against Metaculus's public scoring code; the FAQ's own worked examples were unreachable (403), so goldens are hand-derived, not copied. Not yet wired to records; B-31 and the experiments depend on it |
| B-08 | First review of the real forecasts | 2026-10-05 | 82 records reviewed (`docs/research/09-first-forecast-review.md`). Units, dates and open-vs-resolved: **clean**. Found: Gemini polarity slips (2 of 37 Gemini binary answers, guard live but unseen on a record), a Gemini misreading (46082), wrong weekdays or stale content in 7/82 AskNews bundles, one unsourced figure steering 45524, one parse failure. Gate for B-16 passes with two conditions (see the note); follow-ups B-51, F-11 |
| B-00 | Project setup, research 00/02/03/04, cost model, M1 prompt | 2026-09-28 | See `docs/log/2026-09-28.md` |
| B-02 | OpenRouter account + API key (in `.env`) | 2026-09-29 | Key "FutureEvalKey" in `.env`, $20 total spend limit confirmed. Temporary: raise it when the Metaculus grant ($100–500 expected) arrives |
| B-05 | Cost + provenance logging per forecast, `CONFIG_VERSION`, persist research bundle + rationales | 2026-09-30 | `forecast_engine/records.py`: per-run JSONL + one JSON per question, uploaded as a workflow artifact |
| B-06 | Redundant scheduling | 2026-10-02 | GitHub cron delivered ~8% of firings, so cron-job.org now triggers `workflow_dispatch` every 10 min (token expires 2027-10-01). GitHub cron entries kept as backup |
| B-36 | Working research source for live | 2026-09-29 | Christian enabled billing on the Google project; Gemini grounded search works. Grounding fee not yet known: check the billing console |
| B-07 | Public GitHub repo + secrets | 2026-10-01 | https://github.com/ChrRaaby/forecast_engine; history scanned for the .env values before push; METACULUS_TOKEN, OPENROUTER_API_KEY, GEMINI_API_KEY set from .env; Tests CI green; Test Bot run published 5 bot-testing-area forecasts ($0.033), records uploaded as artifact |
| B-37 | Bot monitor: forecasts, per-model answers and reasoning, research, cost, OpenRouter key usage | 2026-10-01 | `tools/build_monitor.py` + `tools/monitor_template.html`; published at https://claude.ai/artifact/JPhLSguYoWVJzs3AHNJDLS; refreshed on request |
| B-40 | Widen live coverage to unlock outcomes | 2026-10-03 | ADR-0007: `--mode wide` 5×/day in the tournament workflow: all new Metaculus Cup questions + 2 main-site questions per run (~10/day, resolving within 90 days). Dry run: 17 Cup + 2 main-site eligible, $0.039 for 4 |
| B-09 | Reliability guards | 2026-10-03 | `forecast_engine/guards.py`: skip non-open/closed/resolved questions and re-check before publishing; abort if the clock is >1 day off Metaculus server time; numeric flags (median far outside range, >10x member disagreement) recorded and shown on the monitor; MC option names must match. 26 offline tests. Config `m1-baseline-2026-10-03b` |
| B-28 | As-of research layer for backtests (AskNews archive, date guard, write-once cache, leakage screen, credit guard) | 2026-10-04 | PR #2 merged; ADR-0008 accepted. Live check passes (server honours end_timestamp, 30-day window, all dated). Screen changed to two models that must agree (research/07) |
| B-45 | QA gate for B-28 | 2026-10-04 | Code review: 10 issues fixed with tests (79 pass). Validation on 50 real bundles + 100 planted leaks: date bound 50/50; dual screen missed 0/100 plants, excluded 7% / 0% of clean bundles (dev / confirmation). Fresh confirmation pending (F-09) |
| F-01 | Accept ADR-0008 and merge PR #2 | 2026-10-04 | Accepted by Christian with the dual screen; merged |
| B-01 | Metaculus LLM credits | 2026-10-05 | **Declined** for this season (automated email 2026-10-04); prize money still open to us. The bot runs on our own budget: $100 PoC (spec §6) |
| B-38 | Live records as leakage-free backtest data | 2026-10-05 | `evals/outcomes.py` collects resolutions of every forecast question (bot token; daily via the archive task) into data/outcomes/; `evals/replay.py` reruns forecast() on a record's frozen research as of its own time with any config. All 74 live records reproduce their prompts exactly; first outcome collected (Lula margin, -1.87) |
| B-35 | Spec §7 Architecture + ADRs 0001–0005 (template, hosting, OpenRouter, $0.50/q ceiling, core/adapter split) | 2026-09-29 | All accepted (ADR-0005 by Christian 2026-09-29). M0 done; M1 unblocked |
| B-03 | Regenerate the Metaculus token | 2026-09-29 | Dropped by Christian: keeping the current token |
