# EXP-002: Question dates in the forecasting prompt

_Card written BEFORE running. Results section filled by the report generator. Not run yet: needs the M2 harness (B-27…B-32)._

- **Date / author:** 2026-10-03, Claude (from B-09 / B-09b)
- **Hypothesis:** stating the question's close and scheduled resolution dates explicitly ("This question is still open; it closes on
  X and resolves on Y") reduces open-vs-resolved confusion and time-horizon errors (R-18), improving log score mainly on binary
  questions whose research describes events that might already have happened.
- **Change vs. baseline:** prompts gain one line with close and scheduled-resolution dates, rendered from the question snapshot (no new
  date sources; the Clock still supplies "today"). `prompt_version` bump; everything else equal.
- **Question set:** DEV manifest (TBD in B-27) plus live-replay records (B-38). Binary primary; MC/numeric secondary.
- **Evaluation model(s) + release dates:** the evaluation roster from B-27 (models released by ~2025-12-01).
- **Primary metric:** binary log score (Metaculus baseline score).
- **Expected effect & MDE:** small, likely below the MDE at N≈400 (≈0.008–0.010 Brier); worth running only bundled with other
  low-cost prompt fixes, or as a check that it does no harm.
- **Repeats k:** 1 for screening, 3 if promising.
- **Cost estimate:** ~$5 (reasoning-only replay on cached research).
- **Decision rule:** adopt if the paired CI on the primary metric excludes 0 in favour of the change and calibration doesn't worsen;
  inconclusive → keep the simpler prompt (protocol §6). Also check T5: the leakage test must allow exactly these dates and no others.

## Results (generated)
## Decision
