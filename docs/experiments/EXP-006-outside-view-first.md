# EXP-006: Outside view first

> **Merged into EXP-005 on 2026-10-07** (Christian): arm B here became EXP-005's protocol base-rate step, arm C (blind outside-view call) is EXP-005 arm C, and arm D (retrieved anchor, B-56) is deferred to a new card once the library is big enough. This card is kept for the reasoning; it will not be run as written.

_Card written BEFORE running. Results section filled by the report generator. Not run yet: needs B-38 (frozen research + outcomes),
B-31 (reports) and, for arm D, B-56 (reference-class library). Runs before EXP-004. Absorbs B-12's experiment._

- **Date / author:** 2026-10-06, Claude (I-18; research/10 §1; R-08, R-09)
- **Hypothesis:** starting from an explicit outside view (reference class + base rate) and adjusting with the inside view improves
  accuracy. R-08: 40% of top-15 bots computed base rates vs 7%; R-09: 34% of winners referenced similar resolved questions vs 0%. For an
  LLM the gain should come mostly from where the base rate comes from: a prompted base rate may be invented from the same news and add
  nothing (or anchor badly); a blind or retrieved one is independent of the inside view.
- **Change vs. baseline:** four arms on frozen research:
  - A: baseline (M1 prompt, median of 3)
  - B: prompted anchor: before the research summary, the model names a reference class and a base rate, then adjusts with the inside
    view, stating the size and reason of each adjustment
  - C: blind outside-view call: a separate cheap call sees only the question text (no news) and returns reference class + base rate;
    the forecasting call gets that anchor and must explain any move away from it
  - D: retrieved anchor: as C, plus (i) the Yes-rate of resolved binary questions in the same tournament/category and (ii) up to 5
    similar resolved questions with outcomes, all resolved before as-of (B-56; hard filter, unit-tested, protocol T1/T6)
- **Question set:** resolved live records with frozen research (B-38); DEV manifest when available. Binary primary.
- **Evaluation model(s) + release dates:** the live roster on live-replay records (no leakage: research and questions are as of the
  forecast); evaluation roster (B-27) on DEV.
- **Primary metric:** binary log score. Secondary (descriptive): anchor-to-final shift and whether its direction was right; member
  spread.
- **Expected effect & MDE:** priors: B ≈ A; C small gain; D most promising once the library is big enough. MDE ≈ 0.014–0.017 Brier at
  N ≈ 100–150. Arm D is only meaningful once the library holds enough resolved questions; report its library size per question.
- **Repeats k:** k=1 screen of all arms, k=3 decide on the best arm vs A.
- **Cost estimate:** B adds tokens only; C/D add one short call (≈ +10–15% per question, inside the $0.50 cap); total a few dollars on
  replay.
- **Decision rule:** adopt the best arm only if its paired CI vs A excludes 0 at k=3 and calibration doesn't worsen; otherwise keep A.
  The winning variant becomes the base-rate step of EXP-004 arm B.

## Results (generated)
## Decision
