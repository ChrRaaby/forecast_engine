# EXP-008: Research desk (gap-fill)

_Card written BEFORE running. Results section filled by the report generator. Not run yet: needs B-62 (shadow-run mode). Backlog B-64._

- **Date / author:** 2026-10-07, Claude (I-19; research/12)
- **Hypothesis:** research quality is the main failure point (nostreambot: the worst misses come when all models agree on one flawed
  briefing; R-03, R-06). A critic that lists what the brief is missing, followed by targeted searches, gives members a more complete
  brief and improves accuracy. A critic aimed at evidence *against* the leading conclusion helps most on one-sided briefs.
- **Change vs. baseline:** three arms; members and the forecasting prompt are unchanged, so only the research differs:
  - A: baseline research (AskNews for tournament questions + Gemini briefing)
  - B: a cheap critic call returns up to 5 gaps (missing facts, stakeholders' views, base-rate data, contrary evidence); each gap
    becomes one targeted Gemini grounded search (no extra AskNews credits); the answers are appended as a separately labelled section
  - C: as B, but the critic looks specifically for evidence against the brief's leading conclusion
- **Question set:** live questions run as an unpublished shadow (B-62), scored as they resolve. New searches can't be replayed as of a
  past date, so frozen-research replay is not possible for this experiment. Binary primary.
- **Evaluation model(s) + release dates:** the live roster (no leakage: everything runs at forecast time).
- **Primary metric:** binary log score. Secondary (descriptive): B-47 diversity metrics; share of B-46 evidence nodes that trace to
  the gap-fill section (did the members use it?); cost per question.
- **Expected effect & MDE:** priors: B small gain; C possibly larger on one-sided briefs. MDE ≈ 0.014–0.017 Brier at N ≈ 100–150;
  accumulating N takes weeks of live questions.
- **Repeats k:** k=1 (shadow runs are live, one per question); report within-arm variance where questions repeat.
- **Cost estimate:** +1 critic call and ~5 searches per question, roughly +$0.03–0.06/q per shadow arm.
- **Decision rule:** adopt the best arm only if its paired CI vs A excludes 0, calibration doesn't worsen and live cost stays within
  the $0.50/q cap; otherwise keep A. Going live needs a CONFIG_VERSION bump and Christian's OK.

## Results (generated)
## Decision
