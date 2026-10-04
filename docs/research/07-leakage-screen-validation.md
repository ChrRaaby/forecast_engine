# 07: Leakage-screen validation (B-45, 2026-10-04)

**Question:** Does the backtest leakage screen (ADR-0008) catch information from after the as-of date without throwing out clean
questions? And does AskNews respect the archive date bound across many dates?

Tool: `poetry run python -m evals.screen_validation` (seeded, re-runnable). Per-case results quote article text, so they are kept in
the private data repo (`data/qa/`); this note holds only the summary.

## Method
- **Questions:** 30 (development set, seed 20261004) + 20 (confirmation set, seed 777, disjoint) real tournament questions from the
  census, as-of dates January–August 2026 (after the evaluation-model cutoff of 2025-12-01).
- **Research:** one AskNews archive search per question through the normal backtest path (budget guard, date guard, cache).
- **Cases per question:** the real bundle ("clean"); plus an **obvious** planted leak (hindsight wording and a later date); plus a
  **subtle** planted leak (a later-dated event reported plainly). Planted sentences are fictional, written by a separate generator
  model, and dated the day before as-of, so only the screen can catch them.
- **Pass bar** (agreed with Christian 2026-10-04): at most 1 miss in 30 plants per kind; at most 10% of clean bundles excluded.
- Clean bundles that were flagged were read by hand: none contained a real report of a later event. Most flags were schedules,
  previews, forecasts or betting odds; the rest were source text with a wrong year (for example "Q4 2026" for Q4 2025).

## Results
**Date bound: 50 of 50 bundles clean** (no article after as-of, all dated, guard passed). Cost: 250 AskNews credits, about $3 of
model calls in total.

| Screen | Prompt | Clean bundles excluded | Obvious plants missed | Subtle plants missed | Notes |
|---|---|---|---|---|---|
| Gemini 2.5 Flash | v2 | 67% (dev) | 0/30 | 0/30 | flags anything mentioning a later date |
| Gemini 2.5 Flash, reasoning on | v2 | 37% (dev) | 1/30 | 1/30 | 4 API errors |
| GPT-5 mini | v2 | 43% (dev) | 0/30 | 0/30 | |
| Claude Haiku 4.5 | v2/v3 | n/a | n/a | n/a | 70/90 calls rate-limited by OpenRouter; untested |
| Gemini 2.5 Flash | v3 | 20% dev, 30% confirm | 0/50 | 0/50 | |
| GPT-5 mini (low reasoning, 16k tokens) | v3 | 10% dev, 15% confirm | 0/50 | 0/50 | |
| **Both must flag (GPT-5 mini + Gemini 2.5 Flash)** | v3 | **0–13% dev, 0–5% confirm across reruns** (final: 7% and 0%) | **0/50** | **0/50** | chosen |

Prompt v3 states that schedules, plans, forecasts, odds and market prices written before the reference date are allowed (protocol T7
permits as-of market prices) and flags only reports of things that already happened after it, hindsight wording, or a forecasting
platform's aggregate forecast.

## Judgement
- The date guard works. The screen is the weak part, and a single cheap pre-cutoff model is not reliable enough: it either excludes
  far too many clean questions (v2) or sits just above the 10% bar (v3).
- Requiring two different screens to agree meets the bar on both sets with no missed plants. Exclusions that remain are mostly source
  text asserting a later date as fact; excluding those is defensible.
- **Caveats:** (1) The agreement rule and the GPT-5 mini settings were chosen after seeing the confirmation set, so they need a fresh
  confirmation sample in the next AskNews period (B-45 follow-up). (2) Planted leaks are cleaner than real ones (AskNews summaries,
  edited stories); real-batch flag rates must still be watched (protocol T3, >10% warning). (3) Results vary between reruns of the
  same cases by a few percentage points.
