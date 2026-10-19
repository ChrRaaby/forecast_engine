# EXP-010: Numeric range sharpening

_Card written BEFORE running. Results section filled by the report generator. Not run yet: needs ≥ ~40 resolved numeric/discrete
records and B-31._

- **Date / author:** 2026-10-19, Claude (B-68; first scored evaluation, B-67)
- **Hypothesis:** our numeric and discrete distributions are too wide, so narrowing them around the median raises the score.
  Evidence (ours, weak: one MiniBench batch forecast 5–7 Oct by the cheap roster): 0 of 19 real answers fell outside our 10–90%
  range where ≈4 were expected (chance ≈1.4% if the ranges were right), and the numeric peer score averaged −6.3 over 11 questions.
  A wider range is safer, so the prior is that a modest narrowing helps and a strong one hurts through the tails. Relates to R-19 / B-15.
- **Change vs. baseline:** post-processing only, no API calls: scale every quantile's distance from the median by s ∈ {0.5, 0.6,
  0.7, 0.8, 0.9, 1.0}, then rebuild the 201-point CDF exactly as the live code does (bounds and open-bound mass unchanged).
  Baseline = s = 1.0.
- **Question set:** resolved numeric + discrete live records (B-38), reported per config era (cheap M1 vs frontier) and per type.
- **Evaluation model(s) + release dates:** none (transforms stored forecasts).
- **Primary metric:** Metaculus numeric baseline score (evals/scoring.py); secondary: share of answers outside the 10–90% range.
- **Expected effect & MDE:** expected small positive at s ≈ 0.7–0.9; MDE to be computed at the N available (protocol §5).
- **Arms:** one parameter swept, fitted by nested cross-validation, so one candidate (m = 1); calls/q 0 for all.
- **Tier (protocol §6.2):** 1, cheap, simple and undoable post-processing; old config kept in shadow when it goes live.
- **Rerun:** first run.
- **Repeats k:** n/a (deterministic transform); nested cross-validation (fit s on inner folds, score on outer).
- **Cost estimate:** $0.
- **Decision rule:** adopt a factor only if it is stable across folds and eras and the paired CI on the primary metric excludes 0 in
  its favour; inconclusive → one rerun on numeric questions resolved after the first run (protocol §6.3), else keep s = 1.0.

## Results (generated)
## Decision
