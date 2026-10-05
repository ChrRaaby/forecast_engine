# 08: Superforecasting ideas → experiment proposals (2026-10-05)

_Approved by Christian 2026-10-05. Proposal from the "Superforecasting ideas" thread; ideas from Tetlock's *Superforecasting* (I-16)._

## What we already have, and what it says
- Evaluation protocol: card first, paired design, bootstrap CI, DEV/HOLDOUT, k=1 screen / k=3 decide, "inconclusive → keep the
  simpler config". Christian's rule on rigorous validation is already the law of the project; one addition below (B-47).
- Playbook R-20 "multi-persona aggregation": Negative / Avoid.
- R-07 "cap extreme predictions" correlates with winning, i.e. the opposite direction to extremizing.
- nostreambot: median beat mean / geo-mean of odds / LLM stacker; extremizing and all post-hoc calibration rejected because the
  calibration slope flipped sign between eras. Their worst misses: all models agreeing on one shared, flawed briefing.
- I-12 / B-20 / B-23: graph idea already parked with the verdict "inspection and post-mortems: well-founded; better accuracy: unproven;
  must win an A/B".
- Power: at N ≈ 100–150 resolved questions the MDE is ≈ 0.014–0.017 Brier. Most prompt tweaks are smaller, so expect several
  "inconclusive" results; hence few, bundled experiments.

## 1. Perspective diversity
Our 3 models differ by vendor but read the same research bundle with the same prompt, so their errors are correlated. Tetlock's
diversity is mostly about information and method (outside vs inside view, considering the opposite), not costume personas (R-20).

**EXP-004 Diversity of perspective** (one experiment, four arms, frozen research, screen k=1 → decide k=3 on the best arm):

| Arm | Change |
|---|---|
| A | Baseline (current M1 prompt, median of 3) |
| B | Structured method prompt, same for all 3 models: (1) outside view: reference class + base rate, (2) inside view, (3) ≥3 scenarios (status quo / change / surprise) with probabilities summing to 1, (4) "argue the opposite" check, (5) final number |
| C | Distinct lens per model: model 1 outside view only, model 2 inside view / causal drivers, model 3 red-team |
| D | Distinct research per model: model 1 AskNews only, model 2 Gemini search only, model 3 both |

Primary metric: binary log score. Secondary (descriptive): diversity bonus (ensemble vs mean member), pairwise member error
correlation, member spread. Cost: B/C add tokens not calls (≈ +20–40%/q, inside the $0.50 cap). Prior: B > A small; C ≈ A; D unknown,
most interesting.

## 2. Rigorous validation
**B-47:** every experiment report prints member error correlation, member spread and the diversity bonus.

## 3. Extremizing
Why it worked for Tetlock: GJP averaged many independent forecasters each holding part of the information; averaging pulls toward 50%
more than the pooled evidence justifies.
Why it may not work for us: 3 models on shared research (information mostly shared, extremizing a shared error makes it worse); we use
the median; Metaculus log score punishes confident misses; winners cap extremes (R-07); nostreambot rejected it.
Why it may still help: LLMs often hedge; more justified if EXP-004 arm D makes members more independent.

**EXP-003 Extremize / cap sweep** (zero API spend, post-processing): on resolved records (live replay via B-38, later DEV),
logit(p') = a·logit(p) for a ∈ {0.8 … 2.0}, plus caps [3%, 97%] / [1%, 99%] and Platt, nested cross-validation, split by era. Adopt only
if a is stable across folds and eras and the CI excludes 0. Earliest run: N ≥ ~150 resolved binary questions.

## 4. Graph-based structured reasoning
**(a) Inspection, B-46:** after each forecast, a cheap model (e.g. Gemini 3.5 Flash-Lite, ≈ $0.001/q) extracts from each member's
rationale a small graph: question → base rate → key drivers/cruxes → evidence (source, date, direction, strength) → scenarios →
probability. Stored with the forecast record (B-20 schema); rendered in the bot monitor side by side for the 3 members; enables
"load-bearing claims" post-mortems. Doesn't change forecasts, so no experiment needed, only a cost check.

**(b) Accuracy, EXP-005 Graph-first reasoning:** model builds the graph first and derives the probability from it, vs baseline free
text. Variant: probability computed mechanically from the scenario tree vs holistic number. Risks: rigid JSON can hurt small models'
reasoning; "Bayesian" prompts underperformed (R-21). Run after EXP-004 (compare against arm B if it wins).

## Order
1. B-47 and EXP-003 cards now. All experiments wait on B-38 replay + B-31 scorer/report.
2. B-46 any time; helps B-08 review of the MiniBench forecasts.
3. EXP-004 then EXP-005 at ≥ ~100–150 resolved questions with frozen research. No AskNews archive use, so F-09 doesn't block.
4. EXP-003 at ≥ ~150 resolved binary forecasts.

_Model labels: the proposal suggested Sonnet 5.5 · PC for B-47 and EXP-003; they are labelled Opus 5.5 · PC in the backlog, because the
project's model rule (2026-10-03) uses Opus on the PC for all development._
