# EXP-005: Structured reasoning (base rate → scenarios → drivers → reconcile), on frontier models

_Pre-registered card. Rewritten 2026-10-07 (was "graph-first reasoning", 2026-10-05); absorbs EXP-006 (outside view first). Card,
prompts and analysis script are committed **before any scored run**; anything changed after a scored run counts as a new variant.
Results section filled by the report generator._

- **Date / author:** 2026-10-07, Claude, design agreed with Christian (I-16, I-18, I-12; research/08, research/10, research/11).
- **Hypothesis:** forecasters that work through an explicit protocol, with a serious base rate, scenarios with probabilities, and
  drivers as stated odds adjustments, all arithmetic in code, are more accurate than today's free-text prompt. This is Christian's
  reading of *Superforecasting*.
- **Prior (stated before running): expected effect zero or negative.** Two reports found prompts framed as "Bayesian updating"
  underperformed (R-21); rigid structure can crowd out a strong model's judgement; and an LLM asked for a base rate may invent one.
  The bar is set accordingly (decision rule below).
- **Why frontier models:** method effects may not transfer between model strengths, and the tournament bot moves to frontier models
  (ADR-0009 and its 2026-10-07 amendment). All arms run on frontier models.

## Arms (protocol `structured-v3`, `forecast_engine/structured.py`)
| Arm | What each member does | Calls/member |
|---|---|---|
| **A** baseline | today's live binary prompt (`forecast_engine/prompts.py`, template-2026-09-26), unchanged | 1 |
| **B** protocol | one call, JSON: (1) base rate from 2-3 reference classes, each with fit/misfit, a frequency (a count of comparable cases, k of n, which code shrinks to (k+1)/(n+2) and flags when n < 5; or an event rate that code converts to the question window, 1 − exp(−rate·t)) and a source (research item, question, or "memory"), weights → p0 in code; (2) 3-5 exclusive, exhaustive scenarios with P(s) and P(YES\|s), written before the drivers → p_scen = Σ P(s)·P(YES\|s) in code; (3) drivers: direction, evidence ref, odds multiplier from ×1.25/×1.5/×2/×3 (or ÷), independent of the reference classes and of each other → p_drv = odds(p0)·Πm in code, product capped at ×10 either way; (4) reconcile: model names the route it trusts and gives a final number | 1 |
| **C** blind base rate | as B, but step 1 is a separate call that sees the question, its background, resolution criteria, fine print and dates, but no research. The protocol call gets that p0 and may not change it (EXP-006 arm C) | 2 |

- **The arm's forecast (pre-registered):** per member, the code number: if p_drv and p_scen agree (their odds within a factor of 1.5)
  their log-odds mean, otherwise the model's own final number. Clipped to [0.01, 0.99] like live. Per question: median of members, as live.
  The model's own final number is stored on every answer and compared as a pre-registered secondary.
- **No "Bayesian" wording** in any prompt (R-21; a test checks it).
- EXP-006 arm D (retrieved anchor from similar resolved questions, B-56) is deferred until the library is big enough; it would be a new card.

## Data and split (fixed now)
- **Questions:** our own resolved binary live records (tournament, Cup, main site) with frozen research and as_of (B-38 outcomes),
  in order of resolution. Live-replay: same research, same "today" (as_of), so A, B and C see identical inputs.
- **Leakage control:** a model re-forecasts a record only if released ≥ 1 day before its as_of (`evals/model_release.py`; T1). Every
  refused pair is counted. Research bundles are the live ones (no new search).
- **Development set:** the first ~50 resolved eligible records. Used for the screen, for debugging prompts and parsers, and for
  measuring SD(diff) and the MDE. Every prompt or parser change made after a scored dev run is a counted variant.
- **Confirmation set:** the next **100+** resolved eligible records, which nobody looks at before the confirmation run. The decision
  is taken **once**, on this set. HOLDOUT (protocol §3) is not touched.

## Procedure
1. **Pilot (unscored, done 2026-10-07):** 10 disputed records (research/11 set), arms B and C, one frontier + one cheap model; for
   inspecting the protocol's behaviour, not its accuracy. `docs/research/13-structured-protocol-pilot.md`. Ran `structured-v1`.

## Variants (counted; protocol §2 T8)
| # | Protocol | Decided | Change | Why | Before any scored run? |
|---|---|---|---|---|---|
| 1 | `structured-v1` | 2026-10-07 | the card's original design | | yes |
| 2 | `structured-v2` | 2026-10-07, Christian, after the unscored pilot | (a) proportions as k of n, shrunk in code to (k+1)/(n+2), n < 5 flagged; (b) the blind base-rate call (arm C) also sees the question background, still no research | pilot: 10 of 91 reference classes sat at exactly 0% or 100% from tiny remembered sets; the blind call missed the setup on q46076 (1% base rate) | **yes**: no scored run had been made |

| 3 | `structured-v3` | 2026-10-08, Christian | routes agree only if their odds are within 1.5× (v2: within 10 pp **or** 1.5×) | near 0% or 100%, the 10 pp test let a tenfold odds gap count as agreement (1% vs 10% → averaged to ~3%), hiding a real conflict | **yes**: no scored run had been made |

v3 is what the screen runs. Any later change counts as variant 4 and is reported with the results.
2. **Screen (dev set, k=1):** arms A, B, C with **2 frontier members**. Picks which of B or C goes to confirmation (the one with the
   better mean log score vs A; ties → C, the stricter base rate). Only one arm is confirmed, so there is one confirmatory comparison.
3. **Freeze:** protocol version, prompts, parser and analysis script (B-31 report generator) committed and hashed; the card's
   Results section records the hashes and the number of variants tried in development.
4. **Confirmation (confirmation set, k=3):** A vs the chosen arm with the **full frontier roster** (B-16). Per-question score = mean
   over the 3 repeats.

## Metrics
- **Primary:** binary log score of the aggregate, paired per question (chosen arm − A). 95% CI by cluster bootstrap (question series
  as clusters, 10,000 reps), protocol §5.
- **Calibration:** Brier reliability term (Murphy decomposition) and a calibration plot per arm.
- **Secondary (pre-registered, reported regardless of outcome):** Brier; code number vs the model's own number; each frontier model
  alone (per-model table: log score, Brier, parse failures); arm C vs arm B; parse/validation failure rate; tokens and cost per question.

## Risks, each measured
| Risk | Measured by |
|---|---|
| **Invented base rate** | share of reference classes sourced "memory" vs research/question; spread of p0 across members on the same question; \|p0(B) − p0(C)\| (does seeing the news move the "outside view"?); a dev-set audit of 20 random reference classes (is the frequency checkable and roughly right?) |
| **Double-counted drivers** | drivers per answer; total odds factor distribution and share hitting the ×10 cap; drivers citing the same research item; sharpness (mean \|p − 0.5\|) vs A together with the reliability term: more extreme *and* less reliable = overconfidence |
| **Structure crowding out judgement** | how often the two routes disagree and the model overrides; code number vs model number scores; score difference vs A by question type and by how much research there was; failure rate |

## Expected effect, MDE, cost
- **MDE:** stated after the screen from the measured SD(diff) at N = 100+ (protocol §5: ≈ 2.8·SD/√N; for Brier with SD 0.06,
  N = 100 → 0.017).
- **Cost (from the pilot's measured cost; budget $180, ledger `docs/experiments/budget-ledger.md`):** the screen ≈ 50 records ×
  2 members × 4 calls; confirmation ≈ 100 records × roster × 2-3 calls × 3 repeats. The confirmation roster size is fixed before
  the run so the total fits the $180; at least 3 members. Each run has a hard cap in code.

## Decision rule (confirmation set only)
- **Adopt** (then shadow run → Christian's OK → CONFIG_VERSION bump, see "Path to live") only if: mean Δ log score > 0 **and** its
  95% CI excludes 0, **and** the reliability term is not worse than A's by more than 0.005, **and** parse failures are not higher
  than A's by more than 2 percentage points.
- **Kill:** point estimate ≤ 0, **or** CI upper bound < MDE/2 → structured reasoning is dropped for the season. No re-tuning on the
  confirmation set.
- **Otherwise inconclusive:** keep A. A later retry needs a new card, new unseen questions and counts as a new variant.
- Report per-model results either way; a single model's win does not override the aggregate rule.

## Path to live
1. Win on the confirmation set under the rule above.
2. Shadow run: the winning arm runs unpublished next to the live bot on live questions (B-62); compare paired scores as outcomes
   arrive, plus failures, cost and latency against the live run's limits.
3. Only then a CONFIG_VERSION bump that switches the live bot, and only with Christian's OK.

## Relation to B-46
The inspection graphs (B-46) describe free-text answers after the fact; research/11 found they're mostly empty of base rates. This
experiment makes the model build the structure itself. Inspection graphs remain a diagnostic (e.g. the YES/NO mismatch check).

## Results (generated)
## Decision
