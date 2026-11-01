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

## Fresh confirmation (F-09, 2026-11-01)
**Written before the run** (approved by Christian, 2026-11-01).
- **Sample:** 20 new questions, seed 20261101, same eligibility as above, excluding all 50 questions used in October (the dev and
  confirmation sets). Fresh planted sentences from the same generator.
- **Screen:** frozen as merged: `DualScreenConfig` (prompt v3, GPT-5 mini low reasoning 16k + Gemini 2.5 Flash, both must flag).
  Only the dual screen runs. The first run counts: no reruns, no choosing between runs.
- **Pass bar:** date bound holds on 20/20 bundles; at most 1 missed plant per kind (obvious, subtle); at most 2/20 clean bundles
  excluded (leak or screen_failed). On a fail: stop and discuss; nothing is tuned on this set.
- **Power:** 20 questions catch a broken rule, they don't prove ≤10%: 0/20 excluded gives a 95% upper bound of about 15%. The T3
  >10% warning on the first real batch remains the main safeguard.
- **Cost:** 100 AskNews credits (of 300 archive credits this period) and under $1 of model calls.

**Result: pass** (run 2026-11-01, `data/qa/screen_validation_20261101T202921Z.json`, private).

| Check | Bar | Result |
|---|---|---|
| Date bound | 20/20 | **20/20** (no article after as-of, all dated, guard passed; one bundle had 4 articles, the rest 9–10) |
| Obvious plants missed | ≤ 1/20 | **0/20** |
| Subtle plants missed | ≤ 1/20 | **0/20** |
| Clean bundles excluded | ≤ 2/20 | **0/20** (no screen_failed) |

Cost: 100 AskNews credits; $0.064 for the screen calls, plus the plant generator. Questions covered four types (binary, numeric,
discrete, multiple choice), with as-of dates from December 2025 to August 2026.

- **The agreement rule earned its keep on this set too.** Two clean bundles were flagged by one screen only, and both flags were
  wrong (read by hand): a newspaper election forecast from June with projected vote shares (as of 13 Jul), which protocol T7 allows;
  and a low-quality travel article published 12 Feb that claims a first-quarter 2026 airline load factor before the quarter had
  ended (as of 15 Feb). That claim can't contain real later information, the same "wrong-date source text" pattern seen in October.
  A single screen would have excluded 2/20 (at the bar); the dual screen excluded none.
- **Caveats:** (1) The plants are easy: all name an explicit later date, and 6 of 40 even contain the word "fictional" (a flaw in
  the generator prompt; it doesn't change the subtle-plant result much, because the screens cite the date, not that word). Real
  leaks are messier, so this confirms the rule isn't overfitted but doesn't bound real-world recall. (2) N = 20: 0/20 excluded puts
  the true clean-exclusion rate below about 15% (95%). (3) Across all three samples the dual screen has now excluded 0–13% (dev),
  0–5% (October confirmation) and 0% (this one) of clean bundles, with 0/140 plants missed.
- **How certain is this?** (one-sided 95% exact binomial upper bounds; Christian asked for these to be stated explicitly)
  - *Clean questions wrongly excluded:* 0/20 on this fresh set means the true rate is **probably below 14%**. The 10% bar is
    therefore *not* proven by this test alone; it rules out a badly overfitted rule (e.g. a true rate of 20–30%, like the single
    screens) but can't tell 0% from 10%. Pooling the final runs of all three sets (2/70) gives below 9%, but the dev and October
    sets were used to choose the rule, so that pooled figure is optimistic.
  - *Planted leaks missed:* 0/20 per kind gives below 14% per kind; 0/40 on this set gives below 7%; 0/140 across all three
    samples gives below 2%. These numbers apply only to **planted** leaks that name a later date. For real leaks (undated
    hindsight, AskNews summaries written later, edited stories) we have **no measured recall**: not tested, not bounded.
  - *Date bound:* 20/20 here and 70/70 across all samples; a server that ignored the bound would have shown up. Below ~4% of
    dates (0/70) could be affected by an intermittent failure, and the date guard would then still drop or raise on such items.
  - *Run-to-run noise:* reruns of identical cases in October moved the clean-exclusion rate by a few points (0–13% on dev). This
    run was a single, pre-committed run, so it carries that noise too.
  - **Bottom line:** confident the rule isn't overfitted and catches clear, dated hindsight; moderately confident it excludes
    ≤ ~10% of clean questions; unknown how well it catches messy real leaks. The T3 flag-rate warning and hand-reading flagged
    items on the first real batch are what close that gap.
- **Consequence:** the first large archive batch is no longer blocked on the screen. The T3 >10% warning on that batch stays the
  main safeguard.
