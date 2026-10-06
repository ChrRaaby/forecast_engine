# EXP-005: Graph-first reasoning

_Card written BEFORE running. Results section filled by the report generator. Not run yet: runs after EXP-004._

- **Date / author:** 2026-10-05, Claude (I-16, I-12; research/08 §4b)
- **Hypothesis:** having each model first build an explicit reasoning graph (base rate → drivers/cruxes → evidence with source, date,
  direction and strength → scenarios) and derive its probability from it improves accuracy over free-text reasoning. Evidence for this
  in LLMs is thin; rigid structure can hurt smaller models, and "Bayesian" prompts underperformed (R-21).
- **Change vs. baseline:** graph-first prompt with a structured output; variant: probability computed mechanically from the scenario
  tree vs the model's holistic number. Baseline: free-text reasoning (or EXP-004 arm B if that arm wins).
  The graph-first arm outputs the B-46 schema (`forecast_engine/graph.py`, `b46-graph-v1`) and goes through the same checks (quote
  check against the model's own text, tracing in code, final probability from the parsed answer). So its graphs and the inspection
  graphs extracted from baseline answers (`tools/extract_graphs.py`) are directly comparable, node for node.
- **Step 0 (observational, hypothesis-generating only; no decision rests on it):** once outcomes arrive (B-38), backfill inspection
  graphs for resolved binary records and check whether graph features predict larger member errors (log score / Brier per member):
  no stated base rate, untraced-evidence share, net push / direction mismatch (`graph_features`), and driver overlap between members
  (to build). Report rank correlations with CIs and say how many features were tried. A feature that "works" here is a hypothesis for
  the replay, not evidence for adoption. Pilot (research/11): no member states a numeric base rate today and graphs average ~2 evidence
  nodes, so expect little variance in most features.
- **Question set:** resolved live records with frozen research (B-38); DEV manifest when available. Binary primary.
- **Evaluation model(s) + release dates:** as EXP-004.
- **Primary metric:** binary log score. Secondary: parse-failure rate, tokens and cost per question.
- **Expected effect & MDE:** expected small or negative; MDE ≈ 0.014–0.017 Brier at N ≈ 100–150.
- **Repeats k:** k=1 screen, k=3 decide.
- **Cost estimate:** more output tokens per call (estimate +30–60%); a few dollars on replay.
- **Decision rule:** adopt only if the paired CI excludes 0 in its favour at k=3 and parse failures don't rise; otherwise keep the
  baseline. The inspection graph (B-46) stays regardless, because it doesn't change forecasts.
- **Path to live (all three, in order):**
  1. win on replay under the decision rule above;
  2. shadow run: the graph-first arm runs alongside the live bot on live questions, unpublished, records stored next to the live
     ones; compare paired scores as outcomes arrive, plus parse failures, cost and latency against the live run's limits;
  3. only then a CONFIG_VERSION bump that switches the live bot, and only with Christian's OK.

## Results (generated)
## Decision
