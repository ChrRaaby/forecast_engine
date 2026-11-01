# EXP-010: Numeric range sharpening

> **DRAFT v2: needs Christian's OK before the first scored run.** Open choices are listed at the end. Until he approves, the tool
> may only be run with `--count`, `--fetch-scaling`, `--validate`, `--demo` and `--power` (no scores of real forecasts).

_Card written BEFORE running. Results section filled by the report generator (`tools/exp010.py --card`). Not run yet: waits for
N ≥ 40 resolved numeric/discrete questions._

- **Date / author:** v1 2026-10-19, Claude (B-68; prompted by the first scored evaluation, B-67). v2 2026-11-01, Claude (B-68 build:
  brought up to protocol v0.1 as amended 2026-10-19 and the F-12 rules; tool `evals/exp010.py` + `tools/exp010.py`).
- **Hypothesis:** our numeric and discrete distributions are too wide, so narrowing them around the median raises the score.
  Evidence (ours, weak: one MiniBench batch forecast 5–7 Oct by the cheap roster): 0 of 19 real answers fell outside our 10–90%
  range where ≈4 were expected (chance ≈1.4% if the ranges were right), and the numeric peer score averaged −6.3 over 11 questions.
  A wider range is safer, so the prior is that a modest narrowing helps and a strong one hurts through the tails. Relates to R-19 /
  B-15. **New since v1 (synthetic, see "Expected effect"):** the live CDF builder keeps 10% of the mass beyond P10 and beyond P90 and
  spreads it linearly to the bounds whatever s is, so this transform narrows the middle of the distribution, not its tails. If real
  outcomes are narrower everywhere (which 0 of 19 outside suggests), the made-up data says the gain in the middle is cancelled by
  losses in the thin tails.
- **Change vs. baseline:** post-processing only, no API calls. For each stored aggregate (the 10/20/40/60/80/90% values):
  1. median m = the 50% point interpolated between the declared percentiles (ours: the midpoint of P40 and P60), on the question's
     own axis: plain values for a linear question, the log axis for a log-scaled one (its `zero_point`);
  2. each value v becomes m + s·(v − m) on that axis; the percentile levels stay; s = 1.0 returns the values unchanged, bit for bit;
  3. the live submission step builds the CDF, unchanged and imported, not copied: `main.to_prediction` (fit to the range Metaculus
     accepts, break ties, forecasting-tools `NumericDistribution.from_question`) and `get_cdf()`. Bounds, open-bound mass (the
     standardisation), the 201-point CDF of a continuous question and a discrete question's own bucket count (N + 1 points) all
     come from that step, exactly as live.
  Baseline = s = 1.0 (today's forecast). Code: `sharpen` and `submitted_cdf` in `evals/exp010.py`; the live builder in
  `tools/exp010.py` (ADR-0005 keeps the adapter out of `evals/`, see open choice 4).
- **Question set:** live records `data/runs/**/q*.json` (B-38), numeric + discrete, published and shadow, bot-testing-area runs
  excluded; one forecast per (question, source), the earliest usable one; source = live or a named shadow. Resolved (Metaculus's
  actual resolve time, `evals.outcomes.resolved_date`), not annulled or ambiguous, with the question's Metaculus axis stored
  (`--fetch-scaling`: range, `zero_point`, bucket count; the records don't hold the last two). Every dropped record is counted with
  its reason in the report. Clusters: Metaculus posts (sub-questions of a group share one; live and shadow forecasts of a question
  share its cluster). DEV/HOLDOUT: n/a (live records; nothing here touches a frozen set). Tagged by era from the roster (`frontier` =
  TOURNAMENT_CONFIG, `cheap` = DEFAULT_CONFIG, `other`), type and tournament.
- **N it waits for:** ≥ 40 distinct resolved numeric/discrete questions after drops (a question forecast by live and shadow counts
  once). The code refuses to print a verdict below it, prints `not enough data: N = …` and computes no scores (no peeking).
  `tools/exp010.py --count` shows progress per type and era.
- **Evaluation model(s) + release dates:** none (transforms stored forecasts).
- **Primary metric:** Metaculus numeric baseline score per forecast, in points: 100·ln(pmf[outcome bucket] / baseline) / 2, with
  baseline 0.05 for an open-bound bucket and (1 − 0.05·open bounds) / N for an inner one (N = 200 continuous, the question's bucket
  count for discrete; Metaculus `score_math.py`, checked 2026-11-01). Δ = score at the chosen s − score at s = 1.0; positive =
  sharpening helps. On the same outcome 1 nat of numeric log score = 50 points. Secondary, descriptive only: numeric log score
  (nats), CRPS (fraction of the range), share of outcomes outside the forecast's 10–90% range (≈20% if calibrated; outcome position =
  midpoint of the cumulative mass of its bucket).
- **Grid:** s ∈ {0.5, 0.6, 0.7, 0.8, 0.9, 1.0}.
- **Nested cross-validation (seed 1010):** 5 outer folds; whole clusters are assigned to folds in a seeded random order, so a cluster
  never straddles folds. For each outer fold, s is chosen on the other four and applied to this one; ties go to the s closest to 1.0.
  Inner folds: nothing is fitted per fold (the transform is fixed and each forecast's score at each s doesn't depend on the training
  data), so the inner cross-validated mean of each s equals its plain mean over the training part; the code chooses on that mean,
  which is the same choice. The out-of-fold Δs are compared with s = 1.0 by `evals.report.paired_compare`: cluster bootstrap, 10,000
  reps, seed 1010, 95% CI. The s that would go live is the one chosen on all the data. Why nested: s is picked on questions it is
  then not scored on, so the reported gain isn't inflated by having picked the best of six on the same data.
- **Expected effect & MDE:** synthetic power check (`tools/exp010.py --power`; made-up numeric, log-scaled and discrete questions,
  2 per cluster, 60 trials per row; Δ in points):

  | made-up truth | N | gain found (CI > 0) | mean Δ | SD(Δ) | cluster MDE |
  |---|---|---|---|---|---|
  | calibrated (outcome drawn from our own CDF) | 40 / 80 | 3% / 2% | −1.8 / −0.8 | 13 / 8 | 5.7 / 2.4 |
  | our CDF 0.7× as wide, tails as the live builder makes them | 40 / 80 | 40% / 68% | +5.0 / +5.7 | 23 / 20 | 9.6 / 6.1 |
  | same, 0.6× | 40 / 80 | 65% / 85% | +9.7 / +9.6 | 24 / 25 | 10.2 / 7.6 |
  | normal 0.7× as wide as our percentiles say (narrower everywhere) | 40 / 80 | 10% / 8% | −1.3 / −0.7 | 23 / 19 | 9.7 / 5.9 |
  | same, 0.6× (≈3% outside our 10–90%: what 0 of 19 suggests) | 40 / 80 | 10% / 15% | −0.8 / +0.4 | 29 / 27 | 12.9 / 8.1 |

  So: at N = 40 the cluster-aware MDE is ≈10 points; the transform is detectable only if real outcomes keep the live CDF's fat linear
  tails, and then ≈+5 to +10 points; if they are narrower everywhere, expect ≈0. What-if (not an arm): adding normal-shaped P1/P99
  values before the live builder gained ≈+8 points at s = 1.0 under the "normal" truths, with or without sharpening (see open choice
  2). Synthetic numbers depend on the made-up truth; they say what the tool can see, not what our data will show.
- **Arms:** one candidate (sharpening with s chosen by nested CV), m = 1, so the 95% CI; calls/q 0 for all; no compute-matched
  control needed (no extra calls).
- **Decision rule (code: `RULE` and `decide` in `evals/exp010.py`; uses `evals.report.apply_decision_rule`):**
  - **adopt** if all hold: out-of-fold mean Δ > 0; 95% CI lower bound > 0; every outer fold chose s < 1 (stable); the frontier era's
    out-of-fold mean Δ > 0 (the live config gains; no frontier forecasts → not met).
  - **kill** if any holds: mean Δ ≤ 0; CI upper bound < 0.75 points (the kill threshold below).
  - adopt and kill both hold (real but smaller than the threshold) → **kill** (as F-12 (3)). Neither → **inconclusive**.
  - A narrow win (adopt, but the CI lower bound < 0.75) counts as a screen: keep s = 1.0 and do the one rerun (§6.3). The report's
    `next:` line says which step follows (`evals.report.next_step_words`).
- **Kill threshold [Christian]:** 0.75 Metaculus baseline points per forecast, written before any run. It is F-12's 0.015 nats in
  numeric units: numeric baseline points = 50 × nats of numeric log score, so 0.015 nats = 0.75 points (on a binary question the same
  0.015 nats is ≈2.2 baseline points, because there 1 nat = 100/ln 2 ≈ 144 points). The same smallest effect in information terms
  across experiments. Alternative: 1.0 point (0.02 nats) if Christian wants a free post-processing step to clear a slightly higher bar
  for the extra moving part.
- **Per era (cheap vs frontier), type and tournament:** reported for every run, marked descriptive. If eras disagree: the frontier
  era decides adoption for live (adopt needs its out-of-fold mean Δ > 0); a disagreeing cheap era alone doesn't block (the cheap
  shadow stays at s = 1.0 either way, as the fixed yardstick, B-69). If the frontier era shows a loss while the pooled result wins,
  the verdict is inconclusive and the rerun decides.
- **Tier (protocol §6.2):** 1: cheap, simple, undoable post-processing, no extra calls, no prompt change. On adopt:
  - it goes live at once with a new CONFIG_VERSION, in a separate PR that touches `main.py` / `forecast_engine/` with Christian's OK
    (nothing in this PR does). Requirement for that PR: the record keeps the **unsharpened** aggregate plus the s applied, so the
    s = 1.0 forecast stays computable from every live record: that is the "s = 1.0 kept as a shadow" (no extra calls);
  - **confirmation rule (written now):** one look, once ≥ 30 numeric/discrete questions forecast with the new config have resolved
    after the go-live date: `tools/exp010.py --confirm-s <adopted s> --resolved-after <go-live date>`. Confirmed if the one-sided
    95% lower bound of Δ (fixed s vs s = 1.0; the lower end of the two-sided 90% cluster-bootstrap CI) is above 0. Otherwise live
    goes back to s = 1.0 (new CONFIG_VERSION). Same N gate in code (30).
- **Rerun (protocol §6.3):** at most one, only if this run is inconclusive or a narrow win: `tools/exp010.py --run-kind rerun
  --resolved-after <date of this run>`, only questions that resolved strictly after that date, same grid, seed, folds and rule; it
  waits for ≥ 40 such questions. Still inconclusive → dropped. A kill is not rerun.
- **Repeats k:** n/a (deterministic transform of stored forecasts).
- **Cost estimate:** $0 (no model calls; one free Metaculus GET per question for its axis, `--fetch-scaling`, like `evals.outcomes`).
- **Commands (PC):** `poetry run python tools/exp010.py --count` now; once N ≥ 40 and this card is approved:
  `poetry run python tools/exp010.py --fetch-scaling`, `poetry run python tools/exp010.py --validate`, then
  `poetry run python tools/exp010.py --card docs/experiments/EXP-010-numeric-range-sharpening.md`.

### Open choices for Christian (before the first scored run)
1. **Approve the card**, including the kill threshold (0.75 points = 0.015 nats, or 1.0) and the report's default kill on a
   mean Δ ≤ 0 (as EXP-005). Note that SD(Δ) is large (≈20–30 points on the made-up data), so at N = 40 a real +3-point effect
   can easily show a negative mean and be dropped without a rerun; switching that kill off would send such a run to the one
   rerun instead.
2. **Arm or lever.** The synthetic check says this transform likely finds ≈0 if our outcomes are narrower everywhere, and the
   normal-shaped P1/P99 what-if gained ≈+8 points in the same made-up world. Options: (a) run as written (m = 1; $0; a clean null
   is still informative); (b) add an arm B "sharpen + normal-shaped P1/P99" (m = 2, 97.5% CIs, MDE ≈ +12%; not built, ~1–2 h);
   (c) leave sharpening to B-15 and test the tail shape there. My recommendation: (b). Both arms are free and use the same 40
   questions, and B tests the lever the made-up data points at. Caveat: thinner tails lose badly on real surprises, which the
   normal truth doesn't contain; only real outcomes can settle that.
3. **N definition:** 40 distinct questions (a question with live + shadow forecasts counts once) rather than 40 forecasts.
4. **ADR-0005.** The live CDF step is in the adapter (`main.to_prediction` + forecasting-tools) and `evals/` may not import it
   (tests/test_hygiene.py, unchanged). `tools/exp010.py` is the one file that imports it. Keep that (and note it in ADR-0005), or
   later move the numeric submission step into `forecast_engine/` (a live-code PR; `tests/test_exp010.py`'s golden CDF test checks
   the CDFs stay identical) so `evals/` can import it from the core.
5. **Eras:** pooled primary result + "frontier must gain" as an adopt condition (above), or frontier-only primary (cleaner, fewer
   questions: N would count frontier questions only).
6. **Scaling data:** `--fetch-scaling` writes `data/outcomes/scaling.jsonl` (private data repo). Run it by hand before a scored run,
   or add it to the daily archive task. Related: live records don't store the question's `zero_point`, bucket count or the
   submitted CDF; storing them would make this kind of check exact without a fetch (a live-code change, its own item).
7. **Folds and seed:** 5 outer folds by Metaculus post, seed 1010, ties → the s closest to 1.0.

## Results (generated)
## Decision
