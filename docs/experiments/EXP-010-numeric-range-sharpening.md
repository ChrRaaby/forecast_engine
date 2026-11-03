# EXP-010: Numeric range sharpening

> **v3: first scored run done 2026-11-03: arm A killed, arm B inconclusive; live unchanged (see Results).** Built 2026-11-02. Christian's decisions 1 and 2 (changed kill rule, arm B) are built and this
> card is up to date; tests and `tools/exp010.py --validate` pass. The two v3 points ("Open points (v3)") were decided by
> Christian on 2026-11-03 as proposed; neither changes the code path of a run. The scored run
> waits for N ≥ 40 (`--count`) and Christian's OK to run it.

_Card written BEFORE running. Results section filled by the report generator (`tools/exp010.py --card`). First run: 2026-11-03._

- **Date / author:** v1 2026-10-19, Claude (B-68; prompted by the first scored evaluation, B-67). v2 2026-11-01, Claude (B-68 build:
  brought up to protocol v0.1 as amended 2026-10-19 and the F-12 rules; tool `evals/exp010.py` + `tools/exp010.py`). v3 2026-11-02,
  Claude (Christian's decisions 1 and 2 built: no kill on a mean Δ ≤ 0; arm B; m = 2, 97.5% CIs; generator v1.1.0).
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
  Arm B (decision 2) adds one step between 2 and 3: after sharpening, P1 and P99 are added where a normal would put them on the
  question's own axis (log axis for a log-scaled question). On each side the normal goes through the median m and that side's
  sharpened P10 (or P90): P1 = m + 1.815·(P10 − m), P99 = m + 1.815·(P90 − m) (1.815 = z(0.01)/z(0.1)). For a symmetric forecast
  that is the one normal through P10 and P90; a skewed forecast keeps its skew. Nothing is clamped by the experiment: a tail
  beyond a bound goes to the live builder as is, and `main.to_prediction` treats it as it treats any percentile beyond a bound
  (clamped to a closed bound, so no mass beyond it; clamped at 1.9× the range beyond an open bound; the open-bound mass comes from
  the standardisation). **At s = 1.0 arm B still adds the tails**, so arm B at s = 1.0 is not today's forecast.
  Baseline for both arms = arm A at s = 1.0 (today's forecast, bit for bit). Code: `sharpen`, `with_normal_tails`, `arm_points` and
  `submitted_cdf` in `evals/exp010.py`; the live builder in `tools/exp010.py` (ADR-0005 exception, decision 4).
- **Question set:** live records `data/runs/**/q*.json` (B-38), numeric + discrete, published and shadow, bot-testing-area runs
  excluded; one forecast per (question, source), the earliest usable one; source = live or a named shadow. Resolved (Metaculus's
  actual resolve time, `evals.outcomes.resolved_date`), not annulled or ambiguous, with the question's Metaculus axis stored
  (`--fetch-scaling`: range, `zero_point`, bucket count; the records don't hold the last two). Every dropped record is counted with
  its reason in the report. Clusters: Metaculus posts (sub-questions of a group share one; live and shadow forecasts of a question
  share its cluster). DEV/HOLDOUT: n/a (live records; nothing here touches a frozen set). Tagged by era from the roster (`frontier` =
  TOURNAMENT_CONFIG, `cheap` = DEFAULT_CONFIG, `other`), type and tournament. Both arms are scored on the same forecasts: a forecast
  whose live CDF fails to build, or breaks the limits, for either arm at any s is dropped from both and counted (arm B's own drop
  reasons say "arm B only"). `--count` (via `tools/exp010.py`) builds the CDFs, no scores, and shows how many would drop, before
  any scored run. Known case: a forecast whose P10 already sits at a closed bound (a count with P10 = 0) gets a P1 clamped to the
  bound, and the CDF step there overshoots the live builder's 0.19 cap by ≈1.5·10⁻⁶; the existing guard (unchanged) drops it.
  Today's forecasts that are very tall trip the same guard.
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
  which is the same choice. Each arm chooses its own s this way, on the same folds and seed. Each arm's out-of-fold Δs are compared
  with today's forecast (arm A at s = 1.0) by `evals.report.paired_compare`: cluster bootstrap, 10,000 reps, seed 1010, **97.5% CI**
  (m = 2, Bonferroni, protocol §5; `report.family_level(2)`). The s that would go live is the one the arm chooses on all the data. Why nested: s is picked on questions it is
  then not scored on, so the reported gain isn't inflated by having picked the best of six on the same data.
- **Expected effect & MDE:** synthetic power check (`tools/exp010.py --power`; made-up numeric, log-scaled and discrete questions,
  2 per cluster, 60 trials per row, both arms at the card's 97.5% CI; Δ in points against today's forecast; v3 run 2026-11-02):

  | made-up truth | N | arm | gain found (CI > 0) | CI < 0 | mean Δ | SD(Δ) | cluster MDE | A or B found |
  |---|---|---|---|---|---|---|---|---|
  | calibrated (outcome drawn from today's CDF) | 40 / 80 | A | 3% / 2% | 2% / 0% | −1.8 / −0.8 | 13 / 8 | 5.8 / 2.4 | 3% / 2% |
  | | 40 / 80 | B | 0% / 0% | 47% / 62% | −11.5 / −10.0 | 39 / 36 | 16.9 / 11.0 | |
  | today's CDF 0.7× as wide, tails as the live builder makes them | 40 / 80 | A | 37% / 58% | 0% / 0% | +5.0 / +5.7 | 23 / 20 | 9.7 / 6.1 | 37% / 58% |
  | | 40 / 80 | B | 2% / 0% | 17% / 30% | −7.4 / −7.5 | 44 / 43 | 18.9 / 13.1 | |
  | same, 0.6× | 40 / 80 | A | 62% / 77% | 0% / 0% | +9.7 / +9.6 | 24 / 25 | 10.2 / 7.6 | 62% / 77% |
  | | 40 / 80 | B | 3% / 2% | 15% / 12% | −4.2 / −4.2 | 46 / 47 | 19.4 / 14.4 | |
  | normal as wide as our percentiles say (middle right, live tails too fat) | 40 / 80 | A | 7% / 0% | 0% / 0% | −0.6 / −0.8 | 10 / 7 | 4.3 / 2.1 | 40% / 48% |
  | | 40 / 80 | B | 35% / 48% | 0% / 0% | +6.7 / +6.6 | 31 / 31 | 13.7 / 9.6 | |
  | normal 0.7× as wide (narrower everywhere) | 40 / 80 | A | 8% / 7% | 0% / 0% | −1.3 / −0.7 | 23 / 19 | 9.7 / 5.9 | 35% / 62% |
  | | 40 / 80 | B | 28% / 60% | 0% / 0% | +6.4 / +6.8 | 28 / 26 | 11.8 / 7.8 | |
  | same, 0.6× (≈3% outside our 10–90%: what 0 of 19 suggests) | 40 / 80 | A | 10% / 10% | 0% / 0% | −0.8 / +0.4 | 29 / 27 | 12.9 / 8.1 | 55% / 80% |
  | | 40 / 80 | B | 48% / 80% | 0% / 0% | +8.5 / +9.2 | 26 / 26 | 11.0 / 8.0 | |

  So: arm A is detectable only if real outcomes keep the live CDF's fat linear tails (then ≈+5 to +10 points); arm B is detectable
  if outcomes are normal-shaped, whether or not they are also narrower (≈+6 to +9 points), and found about half the time at N = 40
  in the 0.6× world that 0 of 19 suggests. The two arms point in opposite directions: in the worlds where A gains, B loses
  (−4 to −8 points), and in a calibrated world B loses badly (−10 to −12, CI < 0 in about half the trials), because thinner tails
  lose a lot on a surprise. At N = 40 the cluster-aware MDE is ≈10 points for A and ≈11–19 for B (B's SD(Δ) is ≈26–47 points, about
  twice A's). False alarms in the calibrated world: 3% / 2% for either arm. Synthetic numbers depend on the made-up truth; they say
  what the tool can see, not what our data will show. Real surprises (outcomes far outside a normal) are not in these truths, and
  they are what arm B risks.
- **Arms (decision 2):** two candidates, m = 2, so every CI is at 97.5% (protocol §5), for the adopt condition and the kill rule alike;
  calls/q 0 for all; no compute-matched control needed (no extra calls).
  - **A, sharpen:** s from the grid, chosen by nested CV.
  - **B, sharpen + normal-shaped P1/P99 tails:** the same grid, chosen by its own nested CV (same folds, seed 1010); at every s,
    s = 1.0 included, P1 and P99 are added after sharpening (see "Change vs. baseline"). B's lever is the tails, so B may choose
    s = 1.0 and still differ from today.
  - **Baseline for both: arm A at s = 1.0, today's forecast, unchanged.** Arm B at s = 1.0 is compared with that, not with
    itself: it adds the tails at s = 1.0 too.
  - Each arm gets its own verdict. Both arms use the same questions (a forecast that fails for either arm is dropped from both).
- **Decision rule (code: `RULE`, `decide` and `which_goes_live` in `evals/exp010.py`; uses `evals.report.apply_decision_rule`),
  applied to each arm on its own, every CI at 97.5%:**
  - **adopt** if all hold: out-of-fold mean Δ > 0; 97.5% CI lower bound > 0; stable across folds; the frontier era's out-of-fold mean
    Δ > 0 (the live config gains; no frontier forecasts → not met). Stable: arm A, every outer fold chose s < 1; arm B, every
    outer fold chose an s, 1.0 allowed, because B's lever is the tails [Christian] (see "Open points (v3)").
  - **kill** if: the 97.5% CI upper bound < 0.75 points (the kill threshold below). **A mean Δ ≤ 0 is no longer a kill**
    (decision 1, `kill_if_point_at_most=None`): with no other kill it is inconclusive and goes to the one rerun.
  - adopt and kill both hold (real but smaller than the threshold) → **kill** (as F-12 (3)). Neither → **inconclusive**.
  - A narrow win (adopt, but the 97.5% CI lower bound < 0.75) counts as a screen: keep s = 1.0 and do the one rerun (§6.3). Each
    arm's `next:` line in the report says which step follows (`evals.report.next_step_words`).
  - **Which arm goes live:** only a clean win (adopt, not narrow). If only one arm wins, that arm goes live at the s it chooses on
    all the data. **If both win, the one with the larger out-of-fold mean Δ goes live** [Christian]; an exact tie goes to arm A
    (no tails: the smaller change). The other arm is not run live. The report prints this as "Which arm goes live".
- **Kill threshold [Christian]:** 0.75 Metaculus baseline points per forecast, written before any run. It is F-12's 0.015 nats in
  numeric units: numeric baseline points = 50 × nats of numeric log score, so 0.015 nats = 0.75 points (on a binary question the same
  0.015 nats is ≈2.2 baseline points, because there 1 nat = 100/ln 2 ≈ 144 points). The same smallest effect in information terms
  across experiments. Alternative: 1.0 point (0.02 nats) if Christian wants a free post-processing step to clear a slightly higher bar
  for the extra moving part.
- **Per era (cheap vs frontier), type and tournament:** reported for every run and for each arm, marked descriptive. If eras disagree: the frontier
  era decides adoption for live (adopt needs its out-of-fold mean Δ > 0, per arm); a disagreeing cheap era alone doesn't block (the
  cheap shadow stays at today's forecast, arm A at s = 1.0, either way, as the fixed yardstick, B-69). If the frontier era shows a loss while the pooled result wins,
  the verdict is inconclusive and the rerun decides.
- **Tier (protocol §6.2):** 1: cheap, simple, undoable post-processing, no extra calls, no prompt change. On adopt:
  - the arm that goes live (see "Which arm goes live") does so at once with a new CONFIG_VERSION, in a separate PR that touches
    `main.py` / `forecast_engine/` with Christian's OK (nothing in this PR does). Requirement for that PR: the record keeps the
    **untransformed** aggregate plus the arm and s applied, so today's forecast (arm A at s = 1.0) stays computable from every live
    record: that is "today's forecast kept as a shadow" (no extra calls);
  - **confirmation rule (written now; it confirms the arm that went live):** one look, once ≥ 30 numeric/discrete questions
    forecast with the new config have resolved after the go-live date: `tools/exp010.py --confirm-arm <A or B> --confirm-s
    <adopted s> --resolved-after <go-live date>`. Confirmed if the one-sided 95% lower bound of Δ (that arm at its fixed s vs
    today's forecast, arm A at s = 1.0; the lower end of the two-sided 90% cluster-bootstrap CI) is above 0. One arm is live, so
    m = 1 here. Otherwise live goes back to today's forecast (new CONFIG_VERSION). Same N gate in code (30).
- **Rerun (protocol §6.3):** at most one per arm, only for an arm whose first run is inconclusive or a narrow win, and only if no
  arm went live: `tools/exp010.py --run-kind rerun --resolved-after <date of this run> --arms <the arms being rerun>`, only questions
  that resolved strictly after that date, same grid, seed, folds and rule; it waits for ≥ 40 such questions. The CI level follows
  the number of arms in the rerun (1 − 0.05/m: 97.5% for both, 95% for one). Still inconclusive → dropped. A killed arm is not
  rerun. (If one arm went live and the other is inconclusive, the other's rerun needs a new card: the baseline would no longer be
  what live submits.)
- **Repeats k:** n/a (deterministic transform of stored forecasts).
- **Cost estimate:** $0 (no model calls; one free Metaculus GET per question for its axis, `--fetch-scaling`, like `evals.outcomes`).
- **Commands (PC):** `poetry run python tools/exp010.py --count` now (counts, plus the live CDF build check once scaling is stored;
  no scores); once N ≥ 40 and Christian has OK'd the run: `poetry run python tools/exp010.py --fetch-scaling`,
  `poetry run python tools/exp010.py --count`, `poetry run python tools/exp010.py --validate`, then
  `poetry run python tools/exp010.py --card docs/experiments/EXP-010-numeric-range-sharpening.md`.

### Open choices for Christian (before the first scored run): answered 2026-11-02, see "Decisions"
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

### Decisions (Christian, 2026-11-02; walk-through doc "EXP-010 card choices")
1. **Kill threshold 0.75 points** (= 0.015 nats). **The "mean Δ ≤ 0 → kill" rule is switched off for this experiment:** a
   non-positive mean counts as inconclusive and goes to the one rerun. The kill on "CI upper bound < 0.75 points" stays, and so does
   "adopt and kill both hold → kill". Reason: SD(Δ) ≈ 20–30 points, so at N = 40 a real +3-point gain shows a negative mean about one
   time in four; the change is free and undoable. To build: `kill_if_point_at_most=None` in `RULE` (`evals/exp010.py`) and the
   decision-rule text above, with a test.
2. **Option (b): add arm B, "sharpen + normal-shaped P1/P99 tails"**, m = 2, so 97.5% CIs (Bonferroni, protocol §5). Not built yet;
   both arms run on the same questions, $0.
3. **N counts distinct questions** (a question with live + shadow forecasts counts once), as written.
4. **ADR-0005:** keep `tools/exp010.py` as the one file that imports the live CDF step; the exception is noted in ADR-0005.
5. **Eras:** pooled primary result, with "frontier era must gain" as an adopt condition, as written.
6. **Scaling data:** `--fetch-scaling` by hand before each scored run. Storing `zero_point`, bucket count and the submitted CDF in
   live records is a separate live-code item (B-71).
7. **Folds and seed:** as written (5 outer folds by Metaculus post, seed 1010, ties → s closest to 1.0). Purpose: s is chosen on
   questions it is then not scored on, so picking the best of six values can't inflate the gain.
- **Extra:** the one cheap-roster forecast made in wide mode (Gemini-only research) counts as cheap; the report lists it.

### Open points (v3, arm B build): both decided by Christian 2026-11-03, as proposed
1. **If both arms win, which goes live:** **decided 2026-11-03:** the one with the larger out-of-fold mean Δ (tie → arm A). Alternative: always
   arm A unless B's lower CI bound beats A's, which prefers the smaller change. Mine: the larger mean Δ, because both are free and
   undoable and the Tier 1 confirmation checks the one that went live.
2. **Arm B's stability condition:** **decided 2026-11-03:** every outer fold chose an s (1.0 allowed). Honest note: with ≥ 40 questions every fold
   has training data, so this condition is always met; it is a formality, and B's real tests are the 97.5% CI and the frontier
   era. A stricter alternative, the exact analogue of arm A's: every fold's training part has arm B at its chosen s scoring above
   today's forecast. The report shows that per fold ("beats today on its training part") either way, so you can switch to it
   later without new code in the report.
- Not a decision, a watch item: the CDF guard compares each step with forecasting-tools' 0.19 cap. That cap is 95% of
  Metaculus's 0.2, so the guard drops some CDFs that Metaculus would accept (see "Question set"). The guard is unchanged here.
  If `--count` shows more than a couple of such drops, decide before the scored run whether to compare with 0.2 instead. That
  would be a guard change, so only with your OK.

## Results (generated)
<!-- report:begin (generated by evals/report.py; do not edit) -->
### EXP-010 numeric range sharpening: first run

_Generated by `python -m evals.exp010`; do not edit by hand. Post-processing of stored forecasts: no model calls, $0._

#### Provenance

- commit: 5b63cbc51ae35931ed91fadd0ffa8d5733fe9465
- generator: evals/exp010.py v1.1.0, sha256 d66d6391f0cdaf87350d408f747be2878b326775c2f42996c8e88230086f686c
- scoring: evals/scoring.py sha256 5411a76e23fe9fe99990557c4e02cfa784bbdf727470797bff52c18197ab9e16
- report: evals/report.py v1.3.1
- live_cdf: main.to_prediction + forecasting-tools 0.3.1 (via tools/exp010.py)
- records: 267 numeric/discrete record files, combined sha256 c96315899a5e154b343001e126cb8aaee10ceae61de922981438db53336f296a
- outcomes: sha256 71929280eafcc81fc2273af23c70f0c608870fe2088a352a96bbbe1df0e0aaee
- scaling: sha256 07064cc307050362048a22178128df681ba289416c74a6d3bfac08c2f0a35aea
- seed 1010 (pre-registered); bootstrap 10,000 reps over clusters; outer folds 5; grid 0.5, 0.6, 0.7, 0.8, 0.9, 1
- arms: A = sharpen, B = sharpen + normal-shaped P1/P99 tails; m = 2, so each arm's CI is at 97.5% (1 − 0.05/2, protocol §5), for the adopt condition and the kill rule alike
- baseline for every arm: arm A at s = 1.0 (today's forecast, unchanged); arm B adds its tails at every s, s = 1.0 included
- resolved after: no cut-off (first run)
- N: 59 questions, 91 forecasts (live + shadow), 59 clusters (Metaculus posts)

##### Dropped records

| reason | records |
|---|---|
| not resolved yet (or no outcome row) | 150 |
| not a submitted or shadow forecast (status failed / refused) | 20 |
| bot-testing-area run (not a live record) | 4 |
| the live CDF at some s breaks Metaculus's limits (it would have been refused) | 2 |

#### Primary result

Per arm: s chosen on the other folds and applied to each held-out fold (nested cross-validation, folds by cluster; the same folds and seed for both arms). Δ = Metaculus numeric baseline score of the arm at that s minus today's forecast (arm A at s = 1.0), per forecast, in points; positive = the arm helps.

##### Arm A: sharpen

| outer fold | train forecasts | test forecasts | test clusters | chosen s | beats today on its training part |
|---|---|---|---|---|---|
| 1 | 72 | 19 | 12 | 1 | no |
| 2 | 70 | 21 | 12 | 1 | no |
| 3 | 73 | 18 | 12 | 1 | no |
| 4 | 74 | 17 | 12 | 0.9 | yes |
| 5 | 75 | 16 | 11 | 0.9 | yes |

s chosen on all the data (what would go live on adoption): **1**

| comparison | N | clusters | mean Δ | CI | median Δ | win rate (W/T/L) | SD(Δ) | MDE | cluster MDE |
|---|---|---|---|---|---|---|---|---|---|
| A (sharpen), s chosen out of fold vs s = 1.0 | 91 | 59 | -2.43 | [-6.09, 0.30] (97.5%) | 0.00 | 53% (19/58/14) | 14.30 | 4.20 | 4.10 |

##### Arm B: sharpen + normal-shaped P1/P99 tails

| outer fold | train forecasts | test forecasts | test clusters | chosen s | beats today on its training part |
|---|---|---|---|---|---|
| 1 | 72 | 19 | 12 | 1 | yes |
| 2 | 70 | 21 | 12 | 1 | yes |
| 3 | 73 | 18 | 12 | 1 | yes |
| 4 | 74 | 17 | 12 | 0.9 | yes |
| 5 | 75 | 16 | 11 | 0.9 | yes |

s chosen on all the data (what would go live on adoption): **0.9**

| comparison | N | clusters | mean Δ | CI | median Δ | win rate (W/T/L) | SD(Δ) | MDE | cluster MDE |
|---|---|---|---|---|---|---|---|---|---|
| B (sharpen + normal-shaped P1/P99 tails), s chosen out of fold vs s = 1.0 | 91 | 59 | 0.65 | [-2.88, 4.06] (97.5%) | 0.00 | 58% (26/54/11) | 12.68 | 3.72 | 4.25 |

##### Decision

```
Decision for arm A (sharpen), s chosen out of fold under 'EXP-010 decision rule (card v3: no kill on mean Δ ≤ 0; 97.5% CIs with two arms)': KILL
  [ ] adopt mean improvement > 0: point -2.4264
  [ ] adopt 97.5% CI excludes 0 (lower bound > 0): CI [-6.0947, 0.3049]
  [ ] adopt stable across folds: every outer fold chose s < 1: chosen s per fold: 1, 1, 1, 0.9, 0.9
  [ ] adopt frontier era (live config) gains out of fold (point > 0): N 32, point -3.07
  [x] kill  CI upper < 0.75 (smallest effect worth having): upper 0.3049
  next: Dropped: it hit the kill threshold (§5); not rerun.
Decision for arm B (sharpen + normal-shaped P1/P99 tails), s chosen out of fold under 'EXP-010 decision rule (card v3: no kill on mean Δ ≤ 0; 97.5% CIs with two arms)': INCONCLUSIVE
  [x] adopt mean improvement > 0: point 0.6524
  [ ] adopt 97.5% CI excludes 0 (lower bound > 0): CI [-2.8771, 4.0571]
  [x] adopt stable across folds: every outer fold chose an s (1.0 allowed: arm B's lever is the tails): chosen s per fold: 1, 1, 1, 0.9, 0.9
  [x] adopt frontier era (live config) gains out of fold (point > 0): N 32, point 1.70
  [ ] kill  CI upper < 0.75 (smallest effect worth having): upper 4.0571
  next: Inconclusive: keep the simpler/cheaper config and schedule the one rerun on questions resolved after this run (§6.3); this run counts as a screen.
```

**Which arm goes live:** No arm goes live: live keeps s = 1.0 (today's forecast). Each arm's own `next:` line says what follows.

#### Secondary metrics (descriptive)

| metric | s = 1.0 (today) | arm A, out-of-fold s | arm B, out-of-fold s |
|---|---|---|---|
| numeric log score (nats, higher better) | -4.1007 | -4.1492 | -4.0876 |
| CRPS (fraction of range, lower better) | 0.1084 | 0.1083 | 0.1079 |
| share of outcomes outside the 10-90% range (≈20% if calibrated) | 13.2% | 14.3% | 17.6% |

##### Arm A: every s on all the data (in-sample, descriptive: not the test)

| s | baseline score | numeric log score | CRPS | share of outcomes outside the 10-90% range |
|---|---|---|---|---|
| 0.5 | 10.04 | -4.3100 | 0.1082 | 31.9% |
| 0.6 | 13.39 | -4.2429 | 0.1076 | 26.4% |
| 0.7 | 16.05 | -4.1898 | 0.1071 | 23.1% |
| 0.8 | 17.48 | -4.1613 | 0.1071 | 20.9% |
| 0.9 | 19.83 | -4.1142 | 0.1075 | 16.5% |
| 1 | 20.51 | -4.1007 | 0.1084 | 13.2% |

##### Arm B: every s on all the data (in-sample, descriptive: not the test)

| s | baseline score | numeric log score | CRPS | share of outcomes outside the 10-90% range |
|---|---|---|---|---|
| 0.5 | 8.38 | -4.3432 | 0.1088 | 34.1% |
| 0.6 | 13.79 | -4.2351 | 0.1075 | 28.6% |
| 0.7 | 19.18 | -4.1271 | 0.1066 | 23.1% |
| 0.8 | 21.26 | -4.0856 | 0.1065 | 22.0% |
| 0.9 | 23.13 | -4.0481 | 0.1070 | 19.8% |
| 1 | 22.97 | -4.0514 | 0.1079 | 13.2% |

#### Breakdowns (descriptive, not decisions)

Per arm: the same out-of-fold choice of s as the primary result, restricted to a subset, against today's forecast; the in-sample best s is that subset's own best on all its forecasts. Small groups have wide intervals.

##### By era (arm A)

| era | N | clusters | mean Δ | CI | median Δ | win rate (W/T/L) | questions | in-sample best s | outside 10-90% at s = 1.0 | at chosen s |
|---|---|---|---|---|---|---|---|---|---|---|
| cheap | 59 | 59 | -2.08 | [-6.79, 0.93] (97.5%) | 0.00 | 56% (15/36/8) | 59 | 1 | 10% | 12% |
| frontier | 32 | 32 | -3.07 | [-10.84, 0.50] (97.5%) | 0.00 | 47% (4/22/6) | 32 | 1 | 19% | 19% |

##### By type (arm A)

| type | N | clusters | mean Δ | CI | median Δ | win rate (W/T/L) | questions | in-sample best s | outside 10-90% at s = 1.0 | at chosen s |
|---|---|---|---|---|---|---|---|---|---|---|
| discrete | 39 | 25 | -2.85 | [-10.05, 0.99] (97.5%) | 0.00 | 54% (8/26/5) | 25 | 1 | 13% | 15% |
| numeric | 52 | 34 | -2.11 | [-6.59, 0.90] (97.5%) | 0.00 | 52% (11/32/9) | 34 | 0.9 | 13% | 13% |

##### By tournament (arm A)

| tournament | N | clusters | mean Δ | CI | median Δ | win rate (W/T/L) | questions | in-sample best s | outside 10-90% at s = 1.0 | at chosen s |
|---|---|---|---|---|---|---|---|---|---|---|
| fall-futureeval-2026 | 8 | 5 | 1.31 | [0.00, 3.49] (97.5%) | 0.00 | 62% (2/6/0) | 5 | 0.9 | 25% | 25% |
| learning | 2 | 2 | -43.28 | [-91.81, 5.25] (97.5%) | -43.28 | 50% (1/0/1) | 2 | 1 | 0% | 0% |
| metaculus-cup-fall-2026 | 2 | 2 | 2.61 | [0.00, 5.22] (97.5%) | 2.61 | 75% (1/1/0) | 2 | 0.5 | 50% | 50% |
| minibench | 78 | 49 | -1.92 | [-5.25, 0.33] (97.5%) | 0.00 | 51% (15/50/13) | 49 | 0.9 | 12% | 13% |
| none | 1 | 1 | 0.00 | [0.00, 0.00] (97.5%) | 0.00 | 50% (0/1/0) | 1 | 0.9 | 0% | 0% |

##### By source (arm A)

| source | N | clusters | mean Δ | CI | median Δ | win rate (W/T/L) | questions | in-sample best s | outside 10-90% at s = 1.0 | at chosen s |
|---|---|---|---|---|---|---|---|---|---|---|
| live | 59 | 59 | -3.95 | [-9.58, 0.23] (97.5%) | 0.00 | 51% (12/36/11) | 59 | 1 | 12% | 14% |
| shadow-cheap | 32 | 32 | 0.38 | [-0.84, 1.54] (97.5%) | 0.00 | 56% (7/22/3) | 32 | 0.9 | 16% | 16% |

##### By era (arm B)

| era | N | clusters | mean Δ | CI | median Δ | win rate (W/T/L) | questions | in-sample best s | outside 10-90% at s = 1.0 | at chosen s |
|---|---|---|---|---|---|---|---|---|---|---|
| cheap | 59 | 59 | 0.08 | [-3.20, 2.93] (97.5%) | 0.00 | 61% (19/34/6) | 59 | 0.8 | 10% | 15% |
| frontier | 32 | 32 | 1.70 | [-4.55, 8.37] (97.5%) | 0.00 | 53% (7/20/5) | 32 | 1 | 19% | 22% |

##### By type (arm B)

| type | N | clusters | mean Δ | CI | median Δ | win rate (W/T/L) | questions | in-sample best s | outside 10-90% at s = 1.0 | at chosen s |
|---|---|---|---|---|---|---|---|---|---|---|
| discrete | 39 | 25 | 0.13 | [-4.66, 5.06] (97.5%) | 0.00 | 55% (10/23/6) | 25 | 1 | 13% | 21% |
| numeric | 52 | 34 | 1.05 | [-4.09, 5.81] (97.5%) | 0.00 | 61% (16/31/5) | 34 | 0.9 | 13% | 15% |

##### By tournament (arm B)

| tournament | N | clusters | mean Δ | CI | median Δ | win rate (W/T/L) | questions | in-sample best s | outside 10-90% at s = 1.0 | at chosen s |
|---|---|---|---|---|---|---|---|---|---|---|
| fall-futureeval-2026 | 8 | 5 | 1.31 | [0.00, 3.49] (97.5%) | 0.00 | 62% (2/6/0) | 5 | 0.9 | 25% | 25% |
| learning | 2 | 2 | -18.34 | [-41.92, 5.25] (97.5%) | -18.34 | 50% (1/0/1) | 2 | 0.6 | 0% | 50% |
| metaculus-cup-fall-2026 | 2 | 2 | 15.75 | [5.22, 26.28] (97.5%) | 15.75 | 100% (2/0/0) | 2 | 1 | 50% | 50% |
| minibench | 78 | 49 | 0.69 | [-3.08, 4.36] (97.5%) | 0.00 | 57% (21/47/10) | 49 | 0.9 | 12% | 15% |
| none | 1 | 1 | 0.00 | [0.00, 0.00] (97.5%) | 0.00 | 50% (0/1/0) | 1 | 0.9 | 0% | 0% |

##### By source (arm B)

| source | N | clusters | mean Δ | CI | median Δ | win rate (W/T/L) | questions | in-sample best s | outside 10-90% at s = 1.0 | at chosen s |
|---|---|---|---|---|---|---|---|---|---|---|
| live | 59 | 59 | 0.26 | [-4.17, 4.60] (97.5%) | 0.00 | 57% (17/33/9) | 59 | 1 | 12% | 19% |
| shadow-cheap | 32 | 32 | 1.38 | [-1.34, 4.16] (97.5%) | 0.00 | 61% (9/21/2) | 32 | 0.8 | 16% | 16% |

#### Notes

- The CI resamples clusters of forecasts with the out-of-fold choices held fixed; it does not include the extra spread from which s each fold happened to pick. The fold tables show that spread.
- Live and shadow forecasts of the same question share its outcome; they sit in one cluster, so they enter and leave the bootstrap together.
- Both arms are scored on the same forecasts: a forecast whose live CDF fails for either arm at any s is dropped from both (see Dropped records).
- N gate: the verdict is printed only at ≥ 40 resolved questions.
<!-- report:end -->
## Decision
