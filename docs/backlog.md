# Backlog

_Prioritised, top = next. Maintained by Claude (Code or chat) at the end of every session. Last updated: 2026-10-03_

**Rules**
- One row per item. `Rank` is the order within a section. Re-rank rather than adding "urgent" labels.
- `Why` links each item to a playbook recommendation (R-xx, `docs/playbook-tracker.md`), an idea (I-xx, `docs/ideas.md`)
  or a milestone (spec §8). An item with no *why* doesn't belong here.
- Status: `Todo` · `Doing` · `Done` · `Blocked` · `Dropped`. Done items move to the Done section with the date and a result.
- Size: S (≤2 h) · M (≤1 session ~4 h) · L (several sessions).
- Model (Christian, 2026-10-03): the Claude model that should do the work, and where it runs. **Haiku 4.5 · PC** for mechanical work
  (refreshes, small edits, data pulls); **Opus 5.5 · PC** for development (included in Christian's subscription; drop to Sonnet if usage limits bite); **Fable 5.1 · cloud** (frontier)
  only where getting the design right is worth the most. Frontier items run as cloud threads on the free credits (until they run out or
  expire 2026-11-05); then revisit. Label every new item.

## Now: M1 baseline bot live
| Rank | ID | Item | Why | Owner | Size | Status | Model |
|---|---|---|---|---|---|---|---|
| 1 | B-01 | Waiting for the Metaculus LLM credits / grant ($100–500 expected; application sent 2026-09-29). When it arrives: raise the OpenRouter limit and revisit the roster (B-16). AskNews access done 2026-10-01 | R-01, R-10: credits decide what we can afford | Christian | S | Blocked | Haiku 4.5 · PC |
| 2 | B-04 | Template bot live on Fall 2026 AIB + MiniBench: 3 cheap models from different vendors, median (scenario B). Live since 2026-10-01 (cron-job.org trigger since 2026-10-02). No FutureEval/MiniBench questions released yet (next MiniBench round 2026-10-05); wide mode published 18 Cup/main-site forecasts 2026-10-03. Close once it has forecast real tournament questions | M1; R-04, R-15 | Claude Code | M | Doing | Haiku 4.5 · PC |

## Next: reliability → evaluation harness (M2) → experiments
_Evaluation-first: nothing below rank 10 changes the live bot until the harness (ranks 4–10) passes its validation. See `docs/evaluation-protocol.md`._

| Rank | ID | Item | Why | Owner | Size | Status | Model |
|---|---|---|---|---|---|---|---|
| 1 | B-08 | Manual review of the first ~20 forecasts (units, dates, open-vs-resolved confusion); then a weekly 30-min review | R-14, R-17, R-18 | Christian | S | Todo | Opus 5.5 · PC |
| 2 | B-09 | Guard against treating open questions as resolved, and against the wrong "today" date | R-18 | Claude Code | S | Todo | Opus 5.5 · PC |
| 3 | B-10 | Re-run the cost model with measured tokens; confirm the $/question for scenario B | research/03 | Claude | S | Todo | Haiku 4.5 · PC |
| 4 | B-39 | Metaculus Bot Benchmarking Access Tier (~250 resolved + ~250 open questions with outcomes and CP): Data Needs Form submitted 2026-10-03; waiting for Metaculus | I-14; research/05 | Christian | S | Blocked | Haiku 4.5 · PC |
| 5 | B-27 | Evaluation models + release dates; question-supply census; frozen DEV/HOLDOUT manifests with eligibility filter. Pick older eval models to widen the eligible pool (I-14) | Protocol §3, T1, T6; R-25; I-14 | Claude Code | M | Todo | Opus 5.5 · PC |
| 6 | B-38 | Live records as leakage-free backtest data: collect resolutions for archived live questions, and a runner path that replays reasoning variants on frozen research bundles | I-14 (2); Protocol §7 research cache | Claude Code | M | Todo | Opus 5.5 · PC |
| 7 | B-28 | AskNews archive search is covered by the Pro plan (granted 2026-10-03; ~600 credits/month, archive = 5, overage capped by a $5 wallet). As-of retrieval layer (AskNews historical, date guard), content-addressed research cache, LLM leakage screen, AskNews monthly call-budget guard (grant: 1k calls/month, archive = 5) | Protocol T2–T5, T7; R-22 | Claude Code | L | Todo | Fable 5.1 · cloud |
| 8 | B-29 | Scorer package: log/Brier/Metaculus baseline, MC, numeric (log + CRPS), calibration; golden + property tests | Protocol §4, T10 | Claude Code | M | Todo | Opus 5.5 · PC |
| 9 | B-30 | Experiment runner: immutable run records, config hashing, clean-git check, cost guard, holdout guard, repeats k | Protocol §7, T8, T11 | Claude Code | M | Todo | Opus 5.5 · PC |
| 10 | B-31 | Report generator: paired cluster-bootstrap CIs, MDE, calibration plot, per-type breakdown, cost → `docs/experiments/` | Protocol §5, T9, T12 | Claude Code | M | Todo | Fable 5.1 · cloud |
| 11 | B-32 | Harness validation: reproducibility, A/A test, leakage-injection test, trivial baselines | Protocol §7, T13 | Claude Code | M | Todo | Fable 5.1 · cloud |
| 12 | B-33 | EXP-001: single-call baseline vs. M1 config on DEV. Measure the real SD → MDE; set the PoC gate margin | Protocol §5; I-08 | Claude Code + Christian | M | Todo | Fable 5.1 · cloud |
| 13 | B-11 | Experiment: prediction capping (e.g. clip binary to [0.02, 0.98]) | R-07 | Claude Code | S | Todo | Opus 5.5 · PC |
| 14 | B-12 | Experiment: explicit base-rate step + similar resolved questions in the prompt (no "Bayesian" wording) | R-08, R-09, R-21 | Claude Code | M | Todo | Opus 5.5 · PC |
| 15 | B-13 | Experiment: second research source (as-of prediction-market prices) | R-06, research/04 | Claude Code | M | Todo | Opus 5.5 · PC |
| 16 | B-15 | Experiment: numeric-question pipeline vs. template default | R-19 | Claude Code | M | Todo | Opus 5.5 · PC |

## Later: M3 better bot (after the gate)
| Rank | ID | Item | Why | Owner | Size | Status | Model |
|---|---|---|---|---|---|---|---|
| 1 | B-16 | Upgrade forecasters to frontier reasoning models (when credits allow / gate passed) | R-01, R-10, R-11 | Claude Code | S | Todo | Opus 5.5 · PC |
| 2 | B-17 | Agentic, iterative research loop | R-03, R-02 | Claude Code | L | Todo | Fable 5.1 · cloud |
| 3 | B-18 | Add a decorrelating ensemble member (e.g. Grok) and test 3 vs. 4–5 members | R-04, R-13 | Claude Code | S | Todo | Opus 5.5 · PC |
| 4 | B-19 | Platt-scaling experiment on backtest + live data (contested) | R-05 | Claude Code | M | Todo | Opus 5.5 · PC |
| 5 | B-20 | Structured forecast-record schema (claims → evidence → source + date, base rate, cruxes), graph-loadable | I-12 | Claude Code | M | Todo | Opus 5.5 · PC |
| 6 | B-21 | Local 4090 open-weight model as a free backtest baseline / ensemble member | I-04, R-12 | Claude Code | M | Todo | Opus 5.5 · PC |
| 7 | B-22 | Jev as a cheap news-relevance filter (A/B in the backtest) | I-03 | Claude Code | M | Todo | Opus 5.5 · PC |
| 8 | B-34 | Backtest ↔ live agreement check per config era (≥ 50 resolved live Qs) | Protocol §9 | Claude Code | S | Todo | Opus 5.5 · PC |

## Side branch: trading (paper only, no real money)
_Gated on the M2 harness (B-29, B-31, B-38). Real stakes only after the B-42 gate, the B-44 check and Christian's explicit go-ahead. See `docs/research/06-trading-branch.md`._

| Rank | ID | Item | Why | Owner | Size | Status | Model |
|---|---|---|---|---|---|---|---|
| 1 | B-41 | Market question feed + price-snapshot logger: liquid binary markets (Polymarket, Manifold, Kalshi read-only APIs) as a question source for the core; record mid, bid/ask and volume at forecast time; price kept out of the prompt | I-15; research/06 | Claude Code | M | Todo | Opus 5.5 · PC |
| 2 | B-42 | Paper-trading evaluator: edge vs. market price, fractional Kelly sizing, fees and spread, bootstrap CI (reuses B-31); defines the go/no-go gate | I-15; research/06 | Claude Code | M | Todo | Opus 5.5 · PC |
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
| B-00 | Project setup, research 00/02/03/04, cost model, M1 prompt | 2026-09-28 | See `docs/log/2026-09-28.md` |
| B-02 | OpenRouter account + API key (in `.env`) | 2026-09-29 | Key "FutureEvalKey" in `.env`, $20 total spend limit confirmed. Temporary: raise it when the Metaculus grant ($100–500 expected) arrives |
| B-05 | Cost + provenance logging per forecast, `CONFIG_VERSION`, persist research bundle + rationales | 2026-09-30 | `forecast_engine/records.py`: per-run JSONL + one JSON per question, uploaded as a workflow artifact |
| B-06 | Redundant scheduling | 2026-10-02 | GitHub cron delivered ~8% of firings, so cron-job.org now triggers `workflow_dispatch` every 10 min (token expires 2027-10-01). GitHub cron entries kept as backup |
| B-36 | Working research source for live | 2026-09-29 | Christian enabled billing on the Google project; Gemini grounded search works. Grounding fee not yet known: check the billing console |
| B-07 | Public GitHub repo + secrets | 2026-10-01 | https://github.com/ChrRaaby/forecast_engine; history scanned for the .env values before push; METACULUS_TOKEN, OPENROUTER_API_KEY, GEMINI_API_KEY set from .env; Tests CI green; Test Bot run published 5 bot-testing-area forecasts ($0.033), records uploaded as artifact |
| B-37 | Bot monitor: forecasts, per-model answers and reasoning, research, cost, OpenRouter key usage | 2026-10-01 | `tools/build_monitor.py` + `tools/monitor_template.html`; published at https://claude.ai/artifact/JPhLSguYoWVJzs3AHNJDLS; refreshed on request |
| B-40 | Widen live coverage to unlock outcomes | 2026-10-03 | ADR-0007: `--mode wide` 5×/day in the tournament workflow: all new Metaculus Cup questions + 2 main-site questions per run (~10/day, resolving within 90 days). Dry run: 17 Cup + 2 main-site eligible, $0.039 for 4 |
| B-35 | Spec §7 Architecture + ADRs 0001–0005 (template, hosting, OpenRouter, $0.50/q ceiling, core/adapter split) | 2026-09-29 | All accepted (ADR-0005 by Christian 2026-09-29). M0 done; M1 unblocked |
| B-03 | Regenerate the Metaculus token | 2026-09-29 | Dropped by Christian: keeping the current token |
