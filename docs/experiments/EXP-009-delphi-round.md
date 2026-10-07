# EXP-009: Delphi round

_Card written BEFORE running. Results section filled by the report generator. Not run yet: needs B-38 (frozen research + outcomes),
B-31 (reports) and B-47 (diversity metrics). Runs after EXP-004. Backlog B-65._

- **Date / author:** 2026-10-07, Claude (I-19; research/12)
- **Hypothesis:** letting each member see the others' reasoning once and revise (as forecasters do in a community) corrects
  individual blind spots and improves the ensemble. Counter-evidence: nostreambot's LLM stacker that rewrote disagreements was no better
  than the median. Main risk: herding, where members converge and the ensemble loses its diversity bonus.
- **Change vs. baseline:** three arms on frozen research:
  - A: median of the 3 independent forecasts (today)
  - B: each member sees the other two members' rationales, anonymised and with their numbers, and gives one revised forecast; median
    of the revisions
  - C: as B, but members see only the others' arguments, not their numbers (less pure anchoring)
- **Question set:** resolved live records with frozen research (B-38); DEV manifest when available. Binary primary.
- **Evaluation model(s) + release dates:** as EXP-004.
- **Primary metric:** binary log score. Secondary: B-47 member spread and pairwise error correlation before vs after the round
  (herding check); share of members that changed their number; cost.
- **Expected effect & MDE:** priors: small or zero gain; C ≥ B. More promising if EXP-004 arm D wins (members then bring genuinely
  different information). MDE ≈ 0.014–0.017 Brier at N ≈ 100–150.
- **Repeats k:** k=1 screen, k=3 decide on the best arm vs A.
- **Cost estimate:** +3 calls per question, ≈ +$0.01–0.02/q; a few dollars on replay.
- **Decision rule:** adopt only if the paired CI vs A excludes 0 at k=3, calibration doesn't worsen, and the round doesn't collapse
  members onto one number (member spread after the round stays above a level pre-registered from arm A's spread distribution before
  running). Otherwise keep A.

## Results (generated)
## Decision
