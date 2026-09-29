# Backlog

_Prioritised, top = next. Maintained by Claude (Code or chat) at the end of every session. Last updated: 2026-09-30_

**Rules**
- One row per item. `Rank` is the order within a section. Re-rank rather than adding "urgent" labels.
- `Why` links each item to a playbook recommendation (R-xx, `docs/playbook-tracker.md`), an idea (I-xx, `docs/ideas.md`)
  or a milestone (spec §8). An item with no *why* doesn't belong here.
- Status: `Todo` · `Doing` · `Done` · `Blocked` · `Dropped`. Done items move to the Done section with the date and a result.
- Size: S (≤2 h) · M (≤1 session ~4 h) · L (several sessions).

## Now: M1 baseline bot live
| Rank | ID | Item | Why | Owner | Size | Status |
|---|---|---|---|---|---|---|
| 1 | B-01 | Fill in the Metaculus participation form (includes the LLM credits application) ✓ done 2026-09-29. AskNews bot-access request sent 2026-09-29 (asked about archive use for backtests); waiting for reply. Don't buy a plan meanwhile (research/03 addendum) | R-01, R-10: credits decide what we can afford | Christian | S | Blocked |
| 2 | B-04 | Template bot live on Fall 2026 AIB + MiniBench: 3 cheap models from different vendors, median (scenario B). Built and dry-run 2026-09-30; not live: needs research (B-36) and the repo (B-07) | M1; R-04, R-15 | Claude Code | M | Doing |
| 3 | B-36 | Working research source for live: the free Gemini key has no grounded-search quota (429). Options: enable Google billing, OpenRouter web search (~$0.02/q), or the AskNews grant | M1; R-06 | Christian | S | Blocked |
| 4 | B-07 | Public GitHub repo, secrets check, connect repo to the Claude Project. Local git + first commit done 2026-09-30 | Spec §6 | Christian + Claude Code | S | Todo |

## Next: reliability → evaluation harness (M2) → experiments
_Evaluation-first: nothing below rank 10 changes the live bot until the harness (ranks 4–10) passes its validation. See `docs/evaluation-protocol.md`._

| Rank | ID | Item | Why | Owner | Size | Status |
|---|---|---|---|---|---|---|
| 1 | B-08 | Manual review of the first ~20 forecasts (units, dates, open-vs-resolved confusion); then a weekly 30-min review | R-14, R-17, R-18 | Christian | S | Todo |
| 2 | B-09 | Guard against treating open questions as resolved, and against the wrong "today" date | R-18 | Claude Code | S | Todo |
| 3 | B-10 | Re-run the cost model with measured tokens; confirm the $/question for scenario B | research/03 | Claude | S | Todo |
| 4 | B-27 | Evaluation models + release dates; question-supply census; frozen DEV/HOLDOUT manifests with eligibility filter | Protocol §3, T1, T6; R-25 | Claude Code | M | Todo |
| 5 | B-28 | As-of retrieval layer (AskNews historical, date guard), content-addressed research cache, LLM leakage screen, AskNews monthly call-budget guard (grant: 1k calls/month, archive = 5) | Protocol T2–T5, T7; R-22 | Claude Code | L | Todo |
| 6 | B-29 | Scorer package: log/Brier/Metaculus baseline, MC, numeric (log + CRPS), calibration; golden + property tests | Protocol §4, T10 | Claude Code | M | Todo |
| 7 | B-30 | Experiment runner: immutable run records, config hashing, clean-git check, cost guard, holdout guard, repeats k | Protocol §7, T8, T11 | Claude Code | M | Todo |
| 8 | B-31 | Report generator: paired cluster-bootstrap CIs, MDE, calibration plot, per-type breakdown, cost → `docs/experiments/` | Protocol §5, T9, T12 | Claude Code | M | Todo |
| 9 | B-32 | Harness validation: reproducibility, A/A test, leakage-injection test, trivial baselines | Protocol §7, T13 | Claude Code | M | Todo |
| 10 | B-33 | EXP-001: single-call baseline vs. M1 config on DEV. Measure the real SD → MDE; set the PoC gate margin | Protocol §5; I-08 | Claude Code + Christian | M | Todo |
| 11 | B-11 | Experiment: prediction capping (e.g. clip binary to [0.02, 0.98]) | R-07 | Claude Code | S | Todo |
| 12 | B-12 | Experiment: explicit base-rate step + similar resolved questions in the prompt (no "Bayesian" wording) | R-08, R-09, R-21 | Claude Code | M | Todo |
| 13 | B-13 | Experiment: second research source (as-of prediction-market prices) | R-06, research/04 | Claude Code | M | Todo |
| 14 | B-15 | Experiment: numeric-question pipeline vs. template default | R-19 | Claude Code | M | Todo |

## Later: M3 better bot (after the gate)
| Rank | ID | Item | Why | Owner | Size | Status |
|---|---|---|---|---|---|---|
| 1 | B-16 | Upgrade forecasters to frontier reasoning models (when credits allow / gate passed) | R-01, R-10, R-11 | Claude Code | S | Todo |
| 2 | B-17 | Agentic, iterative research loop | R-03, R-02 | Claude Code | L | Todo |
| 3 | B-18 | Add a decorrelating ensemble member (e.g. Grok) and test 3 vs. 4–5 members | R-04, R-13 | Claude Code | S | Todo |
| 4 | B-19 | Platt-scaling experiment on backtest + live data (contested) | R-05 | Claude Code | M | Todo |
| 5 | B-20 | Structured forecast-record schema (claims → evidence → source + date, base rate, cruxes), graph-loadable | I-12 | Claude Code | M | Todo |
| 6 | B-21 | Local 4090 open-weight model as a free backtest baseline / ensemble member | I-04, R-12 | Claude Code | M | Todo |
| 7 | B-22 | Jev as a cheap news-relevance filter (A/B in the backtest) | I-03 | Claude Code | M | Todo |
| 8 | B-34 | Backtest ↔ live agreement check per config era (≥ 50 resolved live Qs) | Protocol §9 | Claude Code | S | Todo |

## Parked
| ID | Item | Why parked | Revisit when |
|---|---|---|---|
| B-23 | Graph DB for analysing past forecasts, then as a reasoning aid | Needs B-20 data first | After M2 |
| B-24 | Fine-tune an open-weight model on resolved questions (4090) | Promising (R-12) but a large effort | After M3, if gap to frontier remains |
| B-25 | Prediction-market trading | Non-goal for v1; legal/tax check needed | After proven calibration |
| B-26 | `docs/research/01-silver.md`: Silver notes | Low urgency | When the book is finished |

## Done
| ID | Item | Date | Result |
|---|---|---|---|
| B-00 | Project setup, research 00/02/03/04, cost model, M1 prompt | 2026-09-28 | See `docs/log/2026-09-28.md` |
| B-02 | OpenRouter account + API key (in `.env`) | 2026-09-29 | Key "FutureEvalKey" in `.env`, $20 total spend limit confirmed. Temporary: raise it when the Metaculus grant ($100–500 expected) arrives |
| B-05 | Cost + provenance logging per forecast, `CONFIG_VERSION`, persist research bundle + rationales | 2026-09-30 | `forecast_engine/records.py`: per-run JSONL + one JSON per question, uploaded as a workflow artifact |
| B-06 | Redundant scheduling | 2026-09-30 | Two cron entries (7/27/47 and 17/37/57), gated by repo variable `LIVE_ENABLED`. External dispatcher not set up (see research/04) |
| B-35 | Spec §7 Architecture + ADRs 0001–0005 (template, hosting, OpenRouter, $0.50/q ceiling, core/adapter split) | 2026-09-29 | All accepted (ADR-0005 by Christian 2026-09-29). M0 done; M1 unblocked |
| B-03 | Regenerate the Metaculus token | 2026-09-29 | Dropped by Christian: keeping the current token |
