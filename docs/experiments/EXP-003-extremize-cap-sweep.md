# EXP-003: Extremize / cap / Platt sweep

_Card written BEFORE running. Results section filled by the report generator. Not run yet: needs B-38 (replay + outcomes) and B-31._

- **Date / author:** 2026-10-05, Claude (I-16; research/08 §3)
- **Hypothesis:** a post-hoc transform of the ensemble's binary probability improves log score. Prior is skeptical: 3 models on shared
  research, median aggregation, log scoring and R-07 (winners cap extremes) all point against extremizing; nostreambot rejected all
  post-hoc calibration because the slope flipped between eras. LLM hedging is the case for it. Covers the B-11 (capping) and B-19
  (Platt) evaluations.
- **Change vs. baseline:** post-processing only, no API calls: logit(p') = a·logit(p) for a ∈ {0.8, 1.0, 1.2, 1.4, 1.6, 2.0}; caps
  [3%, 97%] and [1%, 99%]; Platt scaling. Baseline = current clip [1%, 99%].
- **Question set:** resolved binary live records (B-38 replay), later the DEV manifest (B-27). Reported per config era.
- **Evaluation model(s) + release dates:** none (transforms stored forecasts).
- **Primary metric:** binary log score (Metaculus baseline score).
- **Expected effect & MDE:** expected ≈ 0; MDE ≈ 0.014–0.017 Brier at N ≈ 100–150 (protocol §5). Earliest run at N ≥ ~150.
- **Repeats k:** n/a (deterministic transform); nested cross-validation (fit a on inner folds, score on outer).
- **Cost estimate:** $0.
- **Decision rule:** adopt a transform only if the fitted parameter is stable across folds **and** eras, and the paired CI on the primary
  metric excludes 0 in its favour. Otherwise keep the current clip (protocol §6).

## Results (generated)
## Decision
