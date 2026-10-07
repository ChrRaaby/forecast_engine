# 0009: Frontier ensemble for tournament questions, cheap shadow for evidence

- **Status:** Accepted (Christian, 2026-10-05). Roster to be confirmed in B-16.
- **Date:** 2026-10-05

## Context
Metaculus declined LLM credits for this season (B-01). Christian is willing to fund frontier models himself for tournament questions
to improve the chance of prize money, while experiments stay on cheap models. Frontier models are the strongest known lever (R-01),
but the field (250+ bots) mostly uses them too, so they raise the floor rather than guarantee a prize. Backtests can't choose the
newest models (they may know outcomes; protocol §1), so the switch has to be justified by live results.

## Options considered
1. **Stay cheap everywhere**: lowest cost; leaves the biggest known lever unused.
2. **Frontier for everything** (tournament, wide mode, experiments): simplest; multiplies cost with little evidential gain.
3. **Frontier for tournament questions only, with the cheap config run unpublished on the same questions** (chosen): pays for accuracy
   where prizes are, and every resolved question gives a paired live comparison of the two configs.

## Decision
- **Scope:** FutureEval and MiniBench questions use a frontier ensemble. Wide mode (Cup, main site) and all experiments stay cheap.
- **Budget:** **$300 for Fall 2026 tournament forecasting**, enforced in code (a season spend cap), on top of the **$100 PoC budget**
  for experiments and the cheap configs.
- **Switch trigger:** after the first review of real forecasts (B-08) finds no systematic bugs **and** about a week of clean live runs
  on MiniBench.
- **Evidence:** the cheap M1 config keeps running on every tournament question unpublished (shadow, ~$0.01/q). Resolved questions give a
  paired comparison per config era (protocol §9); reported on the monitor.
- **Roster (B-16):** diverse vendors, including **Grok and a Chinese model** (Christian). Candidates and list prices on OpenRouter
  (2026-10-05, per M tokens in/out): GPT-6.1 Sol $2/$10, Claude Sonnet 5.5 $2/$10, Gemini 3.1 Pro $2/$12, Grok 4.7 $2/$6, Qwen3.8 Max
  $2/$6, DeepSeek V4 Pro $0.40/$5. Five members at ~6k in / 6k out tokens each ≈ $0.32/question (estimate); heavier reasoning
  pushes toward the $0.50 cap. Final roster and reasoning effort are set from measured cost on the first live questions, so that
  ~800 questions per season fit the $300.

## Consequences
- Tournament cost rises from ~$0.01 to ~$0.3–0.5 per question; the season cap stops publishing new frontier forecasts if reached
  (cheap config takes over rather than going silent; to implement in B-16).
- nostreambot found 3 members ≈ 6 on accuracy; five members are chosen for diversity, so member error correlation (B-47) should
  show whether Grok and the Chinese model actually add independent signal.
- The shadow comparison may show the money isn't buying accuracy; then we revisit at the PoC gate.

## Amendment 2026-10-07: method experiments run on frontier models
- **Change:** experiments that decide *forecasting method* for the tournament bot (first: EXP-005, structured reasoning) run on
  frontier models, not cheap ones. A method that helps a cheap model may not help a strong one (rigid structure can help weak
  models and hurt strong ones), so evidence gathered on cheap models has little bearing on the frontier bot's design (Christian).
  This replaces "all experiments stay cheap" above for method experiments; research and plumbing experiments may still use cheap models.
- **How:** live-replay of our own stored records with their frozen research and as_of. A model may re-forecast a record only if it
  was released at least one day before the record's as_of (`evals/model_release.py`, refused in code, unit-tested). That keeps the
  model from knowing anything after as_of (protocol T1). Long-running questions stay in, as their as_of is recent.
- **Budget:** **$180 experiment budget for frontier-model method experiments**, approved by Christian 2026-10-07, on top of the
  $100 PoC budget and separate from the $300 tournament budget. Spend is tracked in `docs/experiments/budget-ledger.md`; each run
  has its own hard cap in code.
- **Cost control:** screens use 2 frontier members at k=1; only the confirmation uses the full roster at k=3 (EXP-005 card).
