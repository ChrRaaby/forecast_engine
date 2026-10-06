# EXP-007: Fermi decomposition, code does the arithmetic

_Card written BEFORE running. Results section filled by the report generator. Not run yet: runs after EXP-006 (order in research/10:
EXP-006 → EXP-004 → EXP-005/EXP-007); needs B-38 and B-31._

- **Date / author:** 2026-10-06, Claude (I-18; research/10 §2; relates to R-19 / B-15)
- **Hypothesis:** on questions with the right shape, having the model estimate components and letting code do the arithmetic beats a
  holistic number. LLMs are poor at compounding rates and at keeping intervals wide; code isn't. Not expected to help on vague yes/no
  questions (chains of multiplied guesses drift low and ignore correlation), so those are excluded.
- **Change vs. baseline:** two arms, only on questions of a matching shape, tagged by a cheap classifier before forecasting:
  - A: baseline (M1 prompt, or EXP-006's winner if adopted)
  - B: Fermi path.
    - "Will X happen by date D?" (rate shape): the model estimates an event rate per year with evidence; code computes
      P = 1 − exp(−rate × time left); the model may adjust with stated reasons.
    - Numeric (decomposable quantity): the model breaks the quantity into factors with low/central/high values; code propagates them
      (Monte Carlo) into the 10–90 percentiles; the model may widen but not narrow.
- **Question set:** resolved live records with frozen research (B-38); DEV manifest when available. Rate-shape binary and numeric,
  reported separately per shape. Report the classifier's tag rate and spot-check its tags.
- **Evaluation model(s) + release dates:** as EXP-006.
- **Primary metric:** binary log score (rate shape); Metaculus numeric log score (numeric). Secondary: CRPS and 10–90 coverage
  (numeric); share and direction of the model's adjustments after the computed number.
- **Expected effect & MDE:** prior: small gain on numeric (wider, better-centred intervals), ≈ 0 on rate-shape binary. N per shape will
  be small at first; compute the MDE per shape before deciding (protocol §5) and treat an underpowered result as inconclusive.
- **Repeats k:** k=1 screen, k=3 decide.
- **Cost estimate:** one cheap classifier call per question plus extra tokens in arm B (≈ +10–20% per question); a few dollars on
  replay.
- **Decision rule:** adopt per shape only if its paired CI vs A excludes 0 at k=3 and calibration/coverage doesn't worsen; otherwise
  keep A for that shape. A numeric win feeds the numeric pipeline (B-15). EXP-005's scenario-tree variant stays in EXP-005.

## Results (generated)
## Decision
