# EXP-014: Pooled ensemble, frontier 5 + cheap shadow 3 (B-92)

_Card written BEFORE running: pre-registered 2026-11-12, before any pooled forecast was computed or scored. Christian approved
running this check (recommendation 3 of the non-determinism report, 2026-11-12). Results section filled by the analysis script._

## In plain words

Every live tournament question is answered by the five frontier models (published: the median of their five answers) and, in
the same run on the same research, by the three cheap shadow models (not published). The idea: take the median of all eight
answers instead of five. More answers damp each model's random wobble, and the cheap models add some diversity. It costs
nothing extra, because the cheap shadow already runs.

Example: frontier answers 20, 25, 30, 32, 40% → live median 30%. Cheap shadow answers 15, 22, 35%. The pooled median of eight
(15, 20, 22, 25, 30, 32, 35, 40) is the average of the middle two, 27.5%.

Both arms come from the same answers made at the same moment, so run-to-run noise cancels (protocol §5, "prefer designs that
share model answers").

## Card

- **Date / author:** 2026-11-12, Claude (Opus), for Christian.
- **Hypothesis:** the pooled median of 8 (5 frontier + 3 cheap shadow) scores better than the live median of 5, because more
  answers average away sampling noise (Metaculus 2025/2026 bot surveys: teams that aggregate more forecasts score better;
  medium evidence, observational, other teams). Against it: the cheap models are weaker (frontier vs cheap head-to-head on 65
  paired questions, 1 Nov: a draw, low certainty, ours), and our own four-run reanalysis found no gain from averaging 2–3 runs
  of the same cheap model (n = 84, low to moderate certainty, ours). Prior: small or no effect.
- **Change vs. baseline:** post-processing only. Arm A = the live record's aggregate, recomputed from its usable members with
  the live code (`parsing.median_*`) and checked equal to the stored one. Arm B = the same median function over the union of
  the live record's usable members and the same run's `shadow-cheap` record's usable members (polarity-excluded and failed
  members are left out, as live does). Numeric/discrete CDFs are built from the aggregate percentiles with the live CDF builder
  (`tools/exp010.py` `LiveCdf`, s = 1.0), exactly as for EXP-010's baseline.
- **Question set:** Fall 2026 FutureEval + MiniBench tournament questions with a published frontier record (status `ok`) and a
  `shadow-cheap` record from the same run (the cheap roster with the live prompt; the old-prompt and EXP-008 shadows are not
  used). First record per question. Resolved, not annulled. Numeric/discrete need stored scaling (`data/outcomes/scaling.jsonl`),
  otherwise dropped and counted. Clusters = post id.
- **Data today (counted 2026-11-12, counts only, no scores):** 88 resolved paired questions: 38 binary, 23 numeric,
  22 discrete, 5 multiple choice (both prompt eras: `frontier-2026-10-08` and `frontier-2026-11-07`).
- **Primary metric:** Metaculus baseline score per question across all question types (protocol §4 default since B-81),
  paired, B − A, cluster bootstrap 95% CI (10,000 reps, seed 1014). Secondary, descriptive: binary log score, per-type means,
  per prompt era, win rate.
- **Expected effect & MDE:** expected 0 to +1 baseline point per question (a guess). The SD of the per-question difference is
  unknown before the run; it is small because B differs from A only where the cheap answers move the median. The MDE is
  reported with the result.
- **Smallest effect of interest:** **0.75 baseline points per question** (same value as EXP-010's kill threshold, = 0.005 nats
  binary log score; Claude's proposal, Christian may change it before the decisive read).
- **Arms:** A baseline, B candidate; m = 1, CI level 95%. B adds no forecasting calls (the shadow already runs), so no
  compute-matched control is needed. Going live would make the published forecast wait for the three cheap answers (they run
  in parallel; this conflicts with B-86's "publish before shadows" and would have to be weighed then).
- **Tier (protocol §6.2):** 1 (aggregation tweak, no extra calls, easy to undo).
- **Rerun:** this is the first run, a **screen** at N ≈ 88. The **deciding read** is one rerun on questions that resolve after
  2026-11-12 only (`resolved_at` > 2026-11-12), at ≥ 80 such questions (expected around mid-December at ~20–25 resolved
  questions a week, estimate).
- **Repeats k:** 1 (live records; nothing is re-run).
- **Cost estimate:** $0.
- **Decision rule (screen):** no change to live from the screen. If the screen's CI excludes 0 in B's favour, or the mean is
  ≥ 0.75, the rerun goes ahead as written. If the CI's upper end is below 0.75, B is **dropped** (pooling can't be worth the
  complexity) and no rerun is run. Otherwise (inconclusive) the rerun goes ahead.
- **Decision rule (rerun):** adopt pooling for live (Tier 1: goes live behind a flag with a new `CONFIG_VERSION`, the 5-member
  median stays as a shadow) only if the rerun's 95% CI excludes 0 in B's favour **and** its mean is ≥ 0.75. If the CI excludes 0
  but the mean is below 0.75: dropped (real but too small). Otherwise dropped.

## Results (generated)

### Screen, 2026-11-12 (`tools/exp014.py --data <data repo>`, data repo at 2026-11-12 20:59 UTC; output in the project files
`evals/exp014/screen-2026-11-12.txt`)

| | N | B − A, baseline points/q | 95% CI | B wins | SD of Δ | MDE |
|---|---|---|---|---|---|---|
| All types (primary) | 88 (88 clusters) | **+0.41** | −2.32 to +3.38 | 45% (11 identical) | 13.6 | ≈ 4.1 |
| binary | 38 | +1.16 | | | | |
| numeric | 23 | −0.86 | | | | |
| discrete | 22 | +0.57 | | | | |
| multiple choice | 5 | −0.22 | | | | |

Descriptive: mean score A 29.78, B 30.19; binary log score B − A +0.0081 nats (n = 38). All 88 questions are from the
`frontier-2026-10-08` era (no `frontier-2026-11-07` question has resolved yet). Dropped: 158 not resolved yet, 1 annulled;
none for missing scaling, CDF conversion or a mismatch between the recomputed and stored live median.

Reading: **inconclusive**. The point estimate is slightly in B's favour, but the CI is wide (it spans −2.3 to +3.4 points)
because pooling changes numeric and discrete forecasts a lot on some questions (SD of Δ 13.6 points). Certainty: low
(n = 88, one era, screen).

## Decision

Screen rule: the CI's upper end (+3.38) is above 0.75, so B is **not dropped**; inconclusive → the deciding rerun goes
ahead as written: questions resolved after 2026-11-12 only, at ≥ 80 such questions (`tools/exp014.py --data data
--resolved-after 2026-11-12`). No change to live.
