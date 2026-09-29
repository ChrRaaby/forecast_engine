# 03: Cost model & PoC budget (2026-09-28)

**Question:** What does a forecast cost per question for the pipeline designs the research favours, and what can
the $100 PoC budget buy?

Model: `tools/cost_model.py`. Prices are from the official Anthropic, OpenAI and Google pricing pages (2026-09-28).
**Token counts are assumptions** (research ~40k in / 4k out plus 6 searches; each forecaster ~12k in / 6k out incl.
reasoning). Replace them with measured numbers from the M1 bot.

## Results (USD per question)
| Scenario | Live | Batch* | Live, free search |
|---|---|---|---|
| A: Baseline: 1 cheap call (GPT-5.6 Luna), no research | 0.01 | 0.005 | 0.01 |
| B: Cheap pipeline: Gemini Flash-Lite research + 3× Luna | 0.11 | 0.09 | 0.05 |
| C: Mid ensemble: Gemini Flash research + Sonnet 5.5 / GPT-5.6 Terra / Gemini 3.1 Pro | 0.38 | 0.24 | 0.32 |
| D: Frontier ensemble: 5 members incl. Opus 5.5 & GPT-5.6 Sol + aggregator | 0.82 | 0.46 | 0.76 |
| E: Hybrid: research on local 4090 + mid ensemble | 0.34 | 0.20 | 0.28 |

\*Batch = 50% off the forecasting calls (Anthropic/OpenAI/Google batch APIs). Only usable in backtests, where latency
doesn't matter; agentic research isn't batchable. "Free search" = AskNews bot access or the Gemini grounding free
quota (5k requests/month).

## What this means
1. **The ≤$0.10/question gate criterion (I-08) is incompatible with the designs that win.** The research says
   frontier models + ensembles + agentic search matter most (research/02), and those cost ~$0.30–0.80 per question.
   At $0.10 we can afford scenario B, which is roughly what the research says *loses*. The criterion needs revising (see
   below).
2. **Search is a big share of the cheap scenarios.** Free AskNews access for Metaculus bots roughly halves B.
3. **The 4090 saves less than hoped on the live bot** (C→E: ~$0.05/q), because forecasting tokens dominate. Its real
   value is in backtesting (free open-weight baselines and ensemble members) and development.
4. **A full season is expensive at frontier level.** Metaculus seasons have ~300–500 questions: C ≈ $130–190,
   D ≈ $250–400. That's a meaningful slice of laertes' "couple of thousand dollars".
5. **Free credits are the swing factor.** Metaculus has arranged LLM credits (Anthropic, Google, OpenAI) and AskNews access
   for tournament bots via an application form. Apply now: it can take a while.

## Proposed $100 allocation
| Bucket | $ | What it buys |
|---|---|---|
| M1 development & smoke tests | 10 | Template bot on MiniBench / test questions |
| M2 backtest | 40 | ~120 resolved questions × configs A, B, C (C in batch ≈ $30) |
| Live forecasting | 40 | Scenario B on Fall 2026 AIB + MiniBench (≈400 Qs ≈ $20–40); upgrade to C as credits arrive |
| Reserve | 10 | Mistakes, reruns |

## Proposed revision to the go/no-go gate
Replace "≤ $0.10/question" with: **the passing configuration must cost ≤ $0.50/question at live prices** (so a
400-question season ≤ $200), and the PoC itself must stay within $100 + credits.

## Timing
- The **Fall 2026 FutureEval tournament** ($58k) appears to start around **2026-09-28** (per a Metaculus repo PR), and seasonal
  tournaments are "always open to new entrants".
- **MiniBench** runs every 2 weeks ($1k): fast feedback on live questions with **no leakage**.

## Open issues
- Model training cutoffs: the backtest set must post-date them (candidate set: Summer 2026 AIB questions, resolved Jun–Sep 2026).
- Does AskNews support date-restricted historical search? This is needed for leakage-free backtests.
- Measure real token usage in M1 and re-run this model.

## Sources
- Claude API pricing: https://platform.claude.com/docs/en/about-claude/pricing
- OpenAI API pricing: https://openai.com/api/pricing/
- Gemini API pricing: https://ai.google.dev/gemini-api/docs/pricing
- AskNews plans: https://my.asknews.app/en/plans
- Metaculus Summer 2026 announcement (credits, 300–500 Qs): https://forum.effectivealtruism.org/posts/ZfLAN557rGWACKtmc/announcing-metaculus-summer-2026-futureeval-bot-tournament
- Fall 2026 tournament + Spring 2026 pros-vs-bots: https://github.com/Metaculus/metaculus/pull/5205
- FutureEval info (seasonal + MiniBench): https://www.metaculus.com/futureeval/info/

## Addendum 2026-09-29: AskNews cost
Credits: latest-48h news search 1 credit, archive (historical) search 5 credits, web search 5 credits.
Plans: pay-as-you-go $0 (25 credits, then $0.025/credit); Pro $7.99/mo (600 credits incl. bonus, then $0.02); Spelunker $250/mo.
Rate limit on PAYG/Pro: 1 request / 2 s (fine for us).

| Use | Assumption | Credits | PAYG | Pro |
|---|---|---|---|---|
| Live season (AIB + MiniBench) | ~500 Qs × (1 latest + 1 archive) | ~3,000 (~750/mo) | ~$75 | ~$32 (4 × $8 + small overage) |
| Backtest research (one-off, cached) | ~350 Qs × 3 archive queries | ~5,250 | ~$131 | ~$105 |
| Backtest research (lean) | ~350 Qs × 1 archive query | ~1,750 | ~$44 | ~$35 |

Conclusion: the free Metaculus bot access decides this. Without it, AskNews for backtests alone would eat most of the PoC budget.
Don't buy a plan yet. Ask explicitly whether bot access covers archive searches for backtesting. Fallbacks: Pro for live, a lean
1-query backtest, or free dated sources (e.g. Wikipedia revision history) for part of the as-of research.
Sources: https://docs.asknews.app/en/rate-limiting · https://my.asknews.app/en/plans

## Addendum 2026-09-29 (b): the Metaculus AskNews grant
Per the FutureEval resources page: **1,000 calls/month, 4,000 calls total for the tournament**, 5M tokens (DeepNews), `/news` and
`/deepnews` endpoints. Latest-48h search = 1 call, archive search = 5 calls.

Implications:
- The grant is sized for live forecasting. Live at ~500 Qs × (1 latest + 1 archive) = 3,000 calls would leave almost nothing for backtests.
- **The monthly cap is the real bottleneck for backtests:** 1,000 calls = at most 200 archive searches/month.

**Allocation (proposed):**
| Use | Source | Calls |
|---|---|---|
| Live: recent news | AskNews latest (1 call/question) | ~500/season (~125/month) |
| Live: background context | Gemini grounded search (5,000 free requests/month) | 0 AskNews |
| Backtest research (as-of) | AskNews archive, 1 query/question to start, cached forever | ~3,000 = 600 archive queries/season, ≤ ~175/month |

- A DEV set of 150 + HOLDOUT of 150 at 1 query each = 1,500 calls, about 2 months of backtest allowance. Build research in monthly
  batches. If that's too slow, one month of AskNews Pro for a burst costs ~$30; decide when needed.
- Live web search (Gemini grounding) can't be date-restricted, so it's **never** used in backtests (protocol T2). Backtests of live
  configs therefore substitute AskNews archive research. Note this mismatch in reports.
- The harness tracks AskNews calls with a monthly budget guard.

## Addendum 2026-09-30: first measured numbers (M1 dry run)
Config `m1-baseline-2026-09-30b` (ADR-0006), 8 bot-testing-area questions, **no research** (Gemini grounding unavailable). Costs are
as reported by OpenRouter per request.

| Model | Mean $/question | Output tokens (typical) |
|---|---|---|
| openai/gpt-6-luna | 0.0007 | 550–2,500 |
| google/gemini-3.5-flash-lite | 0.0036 | 1,000–1,800 |
| deepseek/deepseek-v4.1-flash | 0.0027 | 800–7,700 (reasoning-heavy) |
| **Total** | **0.0069** (max 0.0098) | |

Prompt input was only 450–2,300 tokens because research was empty, versus the 12k assumed above. With ~5k tokens of research the
total should stay around $0.01–0.02/question (estimate). Re-measure with research before closing B-10.

**Update, same day, with research** (config `m1-baseline-2026-09-30c`, Gemini grounded search now billed): mean **$0.008/question**
over the same 8 questions (max $0.016). Gemini research adds ~$0.0015/question as a token estimate at list price. Google's per-search
grounding fee is **not** included and is unknown to us; check the Google billing console after the first real runs before trusting
this number.
