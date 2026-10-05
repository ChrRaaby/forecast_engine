# EXP-004: Diversity of perspective

_Card written BEFORE running. Results section filled by the report generator. Not run yet: needs B-38 (frozen research + outcomes),
B-31 (reports) and B-47 (diversity metrics)._

- **Date / author:** 2026-10-05, Claude (I-16; research/08 §1)
- **Hypothesis:** our 3 members make correlated errors because they read the same research with the same prompt. Diversity of
  information and method (Tetlock), not costume personas (R-20), lowers that correlation and improves the ensemble. nostreambot's worst
  misses came from all models agreeing on one flawed briefing.
- **Change vs. baseline:** four arms on frozen research:
  - A: baseline (M1 prompt, median of 3)
  - B: structured method prompt for all members: outside view (reference class + base rate), inside view, ≥3 scenarios (status quo /
    change / surprise) summing to 1, "argue the opposite", final number
  - C: a distinct lens per member: outside view only / inside view and causal drivers / red-team
  - D: distinct research per member: AskNews only / Gemini search only / both
- **Question set:** resolved live records with frozen research (B-38); DEV manifest when available. Binary primary.
- **Evaluation model(s) + release dates:** the live roster on live-replay records (no leakage: research and questions are as of the
  forecast); evaluation roster (B-27) on DEV.
- **Primary metric:** binary log score. Secondary (descriptive): diversity bonus (ensemble vs mean member), pairwise member error
  correlation, member spread (B-47).
- **Expected effect & MDE:** priors: B > A small; C ≈ A; D unknown and most interesting. MDE ≈ 0.014–0.017 Brier at N ≈ 100–150.
- **Repeats k:** k=1 screen of all arms, k=3 decide on the best arm vs A.
- **Cost estimate:** arms B/C add tokens, not calls (≈ +20–40% per question, inside the $0.50 cap); total a few dollars on replay.
- **Decision rule:** adopt the best arm only if its paired CI vs A excludes 0 at k=3 and calibration doesn't worsen; otherwise keep A.
  Arm D also needs a live-cost check, since it changes which research each member sees.

## Results (generated)
## Decision
