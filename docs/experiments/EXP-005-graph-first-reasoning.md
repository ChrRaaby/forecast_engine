# EXP-005: Graph-first reasoning

_Card written BEFORE running. Results section filled by the report generator. Not run yet: runs after EXP-004._

- **Date / author:** 2026-10-05, Claude (I-16, I-12; research/08 §4b)
- **Hypothesis:** having each model first build an explicit reasoning graph (base rate → drivers/cruxes → evidence with source, date,
  direction and strength → scenarios) and derive its probability from it improves accuracy over free-text reasoning. Evidence for this
  in LLMs is thin; rigid structure can hurt smaller models, and "Bayesian" prompts underperformed (R-21).
- **Change vs. baseline:** graph-first prompt with a structured output; variant: probability computed mechanically from the scenario
  tree vs the model's holistic number. Baseline: free-text reasoning (or EXP-004 arm B if that arm wins).
- **Question set:** resolved live records with frozen research (B-38); DEV manifest when available. Binary primary.
- **Evaluation model(s) + release dates:** as EXP-004.
- **Primary metric:** binary log score. Secondary: parse-failure rate, tokens and cost per question.
- **Expected effect & MDE:** expected small or negative; MDE ≈ 0.014–0.017 Brier at N ≈ 100–150.
- **Repeats k:** k=1 screen, k=3 decide.
- **Cost estimate:** more output tokens per call (estimate +30–60%); a few dollars on replay.
- **Decision rule:** adopt only if the paired CI excludes 0 in its favour at k=3 and parse failures don't rise; otherwise keep the
  baseline. The inspection graph (B-46) stays regardless, because it doesn't change forecasts.

## Results (generated)
## Decision
