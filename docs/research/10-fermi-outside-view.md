# 10: Fermi estimation and the outside view → experiment proposals (2026-10-06)

_Approved by Christian 2026-10-06. Proposal from the Superforecasting thread (I-18); Fermi estimation and the outside view from
Tetlock's *Superforecasting*._

## What the bot does today (`forecast_engine/prompts.py`, inherited from the Metaculus template)
- Binary: time left, the status quo outcome, one Yes and one No scenario, "good forecasters put extra weight on the status quo".
  Multiple choice: the same plus "leave moderate probability on most options". Numeric: status quo, trend, expert/market
  expectations, low/high surprise scenarios, wide 10/90 interval.
- No reference class, no base rate, no decomposition. "Weight the status quo" is a crude outside view: a heuristic, not a number the
  forecast starts from.
- Already planned: B-12 (explicit base-rate step + similar resolved questions; R-08: 40% of top-15 bots computed base rates vs 7%;
  R-09: 34% of winners referenced similar resolved questions vs 0%). EXP-004 arm B bundles a base-rate step with scenarios and "argue
  the opposite". EXP-005 has a "compute the probability from the scenario tree" variant (a form of Fermi). B-46's graph already has a
  base-rate node.

## The LLM-specific catch
Asked to "state a base rate", an LLM will invent one, often from the same news it then uses for the inside view. That anchors the
forecast on an invented number, which can be worse than no anchor. For humans the discipline is the point; for an LLM the value is
mostly in where the base rate comes from. So the proposals separate a prompted base rate from a retrieved or computed one, and keep
arithmetic in code.

Second catch: Metaculus only shows outcomes for questions we forecast (research/05). A similar-resolved-questions library can only use
our own resolved live records plus the Bot Benchmarking tier (B-39) if granted. It starts small and grows. In backtests every
reference question must have resolved before the as-of date (protocol T1/T6).

## 1. Outside view: EXP-006 "Outside view first" (absorbs B-12's experiment)
Frozen research, binary primary, screen k=1, decide k=3 on the best arm vs A.

| Arm | Change |
|---|---|
| A | Baseline (current prompt) |
| B | Prompted anchor: before the research summary, the model names a reference class and a base rate, then adjusts with the inside view, stating the size and reason of each adjustment |
| C | Blind outside-view call: a separate cheap call sees only the question text (no news) and returns reference class + base rate; the forecasting call gets that anchor and must explain any move away from it |
| D | Retrieved anchor: as C, plus (i) the Yes-rate of resolved binary questions in the same tournament/category and (ii) up to 5 similar resolved questions with outcomes, all resolved before as-of (B-56) |

Secondary (descriptive): anchor-to-final shift, and whether its direction was right. Prior: B ≈ A; C small gain; D most promising once
the library is big enough. Cost: C/D add one short call (~+10–15%/q). Order: run EXP-006 before EXP-004 (strongest outside evidence,
R-08/R-09); EXP-004 arm B then builds on the winner. Opus 5.5 · PC. Card: `docs/experiments/EXP-006-outside-view-first.md` (B-58).

**B-56 Reference-class library:** tournament/category Yes-rates and a similar-question lookup over our resolved records + B-39 data,
hard "resolved before as-of" filter with a unit test. Needs B-38. Opus 5.5 · PC.

## 2. Fermi estimation: EXP-007 "Fermi decomposition, code does the arithmetic"
Fits two shapes; fits vague yes/no questions poorly (chains of multiplied guesses drift low and ignore correlation).
- "Will X happen by date D?": the model estimates an event rate per year with evidence; code computes P = 1 − exp(−rate × time left);
  the model may adjust with stated reasons.
- Numeric: the model breaks the quantity into factors with low/central/high values; code propagates them (Monte Carlo) into the 10–90
  percentiles; the model may widen but not narrow. Fits the planned numeric pipeline replacement (R-19 / B-15).

Arms: A baseline vs B Fermi path, only on questions of the matching shape (tagged by a cheap classifier), reported per shape. Primary:
log score (binary), Metaculus numeric log score (numeric). Run after EXP-006. Opus 5.5 · PC. EXP-005's scenario-tree variant stays in
EXP-005. Card: `docs/experiments/EXP-007-fermi-decomposition.md` (B-59).

## 3. Inspection
**B-57:** extend B-46's graph to show "anchor → adjustments → final"; the monitor shows how far each forecast moved from its base rate.
Sonnet 5.5 · PC.

## Order
1. B-56 (needs B-38) and the EXP-006/EXP-007 cards now.
2. EXP-006 → EXP-004 → EXP-005/EXP-007 as resolved questions with frozen research accumulate (same replay set; this order limits
   forking paths, protocol T8).
3. B-57 alongside B-46.
