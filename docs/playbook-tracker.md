# Playbook tracker

_How forecast_engine measures up against the evidence on what makes forecasting bots good. Maintained by Claude at the end of
every session that changes the bot or produces results. Last updated: 2026-09-29_

**Source:** "AI Forecasting in 2026: What 11 Analyses Say" (EA Forum), https://forum.effectivealtruism.org/posts/Spyz3wESZu2eeqhDj/ai-forecasting-in-2026-what-11-analyses-say.
Supplemented by nostreambot's findings (research/04), flagged where they disagree.

**Columns**
- **Evidence:** strength as reported by the source: Strong · Moderate · Negative (= evidence it *doesn't* work).
- **Stance:** Adopt · Adopt later · Test (A/B before keeping) · Avoid · Info (shapes how we work, nothing to build).
- **Built by:** backlog items (B-xx) and, once built, code/ADR references.
- **Status:** Not started · Planned · Partial · Done · N/A.
- **Assessment / results:** our current honest view plus measured results (backtest Brier, live peer score, $/question). Update, don't append.

## What works
| ID | Recommendation | Evidence | Stance | Built by | Status | Assessment / results |
|---|---|---|---|---|---|---|
| R-01 | Use frontier reasoning models (the biggest single differentiator) | Strong | Adopt later | B-16 | Planned | Deliberately *not* in M1 (budget). The biggest known lever we're leaving on the table; credits (B-01) decide when. |
| R-02 | Invest in scaffolding (worth 5–11 peer pts/question, ~9 months of model progress) | Strong | Adopt | B-04 → B-17 | Planned | M1 uses template-level scaffolding only. Real scaffolding arrives with M3. |
| R-03 | Agentic / iterative search (removing search degraded Brier 3.6×) | Strong | Adopt later | B-17 | Planned | Costly in tokens; design it to fit ≤ $0.50/q. |
| R-04 | Ensemble 3–7 diverse models (86% of winners aggregate) | Strong | Adopt | B-04, B-18 | Partial | Built 2026-09-30: median of GPT-6 Luna, Gemini 3.5 Flash-Lite, DeepSeek V4.1 Flash (ADR-0006). Not live yet. nostreambot found median > mean/stacking and 3 ≈ 6 members. |
| R-05 | Post-hoc Platt scaling (−0.016 Brier binary) | Strong (contested) | Test | B-19 | Not started | ⚠️ nostreambot rejected all post-hoc calibration: the slope flipped between eras. Needs our own data, likely N > 300. |
| R-06 | 2+ distinct research sources (r = 0.42) | Moderate | Adopt | B-13 | Planned | Prediction-market prices are the cheapest second source. |
| R-07 | Cap extreme predictions (r = +0.48 within winners) | Moderate | Adopt | B-11 | Planned | Cheap. Tune bounds after M2. |
| R-08 | Explicitly compute base rates (40% of top-15 vs 7%) | Moderate | Adopt | B-12 | Planned | Consistent with Silver/Tetlock. |
| R-09 | Reference similar resolved questions (34% of winners vs 0%) | Moderate | Adopt | B-12 | Planned | Metaculus API gives us resolved questions for free. |
| R-10 | Spend a meaningful inference budget (winners ~28 calls/q; top-15 ~$1.40/q vs $0.50) | Moderate | Info | B-16 | N/A | ⚠️ In tension with our $0.50/q cap. The evidence is correlational (well-funded bots differ in other ways), but it's a warning the cap may limit our ceiling. Revisit at the gate. |
| R-11 | Use recently deployed models (older training data degrades ~21.5% over 4 years) | Moderate | Adopt | B-16 | Partial | M1 roster: GPT-6 Luna and DeepSeek V4.1 Flash are Sep 2026 releases, Gemini 3.5 Flash-Lite Jul 2026. `tools/check_models.py` checks IDs against the live lists. |
| R-12 | Fine-tuned open-weight models can reach prior-gen closed parity | Moderate | Adopt later | B-21, B-24 | Not started | The 4090 angle. Fine-tuning is a big project, so it's parked. |
| R-13 | Add Grok as a decorrelating ensemble member | Moderate | Test | B-18 | Not started | nostreambot later dropped Grok (cost), so test before adopting. |
| R-14 | Manually review bot logs (66% of winners; catches units and date bugs) | Moderate | Adopt | B-05, B-08 | Partial | Built: every forecast writes prompts, raw outputs, research, tokens and $ to `data/forecasts/` (workflow artifact). Review itself (B-08) not started. |
| R-15 | Use MiniBench for iteration (fast, no leakage) | Moderate | Adopt | B-04 | Planned | Part of M1's live targets. |

## What doesn't work (avoid)
| ID | Anti-pattern | Evidence | Stance | Built by | Status | Assessment / results |
|---|---|---|---|---|---|---|
| R-16 | Betting on a single "best" search provider | Negative | Avoid | B-13 | Planned | Use breadth over brand. |
| R-17 | Adding scaffolding before reliability (one units bug cost 80 pts) | Negative | Avoid | B-08, B-09 | Partial | Reliability items rank above new features. The M1 parser rejects ambiguous magnitudes ("2 million", "2k") instead of guessing. |
| R-18 | Naive open-vs-resolved handling | Negative | Avoid | B-09 | Planned | Explicit guard. |
| R-19 | The template's default numeric pipeline | Negative | Avoid | B-15 | Planned | Accept it for M1; replace before M3. |
| R-20 | Multi-persona aggregation | Negative | Avoid | none | N/A | Don't build. |
| R-21 | Prompts built around "Bayesian updating" (underperformed twice) | Negative | Avoid | B-12 | N/A | ⚠️ Relevant to I-01: keep Silver's *principles* (base rates, updating) in the design, but don't put "Bayesian" framing in prompts. |
| R-22 | Porous-cutoff backtesting (models can't "pretend not to know") | Negative | Avoid | B-27, B-28, B-32 | Planned | Strict: as-of must be after model *release* dates; as-of retrieval only; leakage screen + injection test. See `docs/evaluation-protocol.md`. |
| R-23 | Optimising prompts toward the community prediction | Negative (weak) | Avoid | B-31 | N/A | The gate *compares* to the community forecast; never tune toward it. |

## How to evaluate and build
| ID | Finding | Evidence | Stance | Built by | Status | Assessment / results |
|---|---|---|---|---|---|---|
| R-24 | Hobbyist-level effort is competitive (dev time vs score r = 0.08) | Strong | Info | none | N/A | Encouraging for 10 h/week. Focus beats volume. |
| R-25 | Strict-cutoff backtesting predicts live performance | Strong | Adopt | B-27…B-33, B-34 | Planned | Evaluation-first is now the project's governing rule (protocol §0). We verify the claim for our own harness via backtest ↔ live agreement (B-34). |

## Results log
| Date | Config version | Question set | N | Metric | Result | Note |
|---|---|---|---|---|---|---|
| 2026-09-30 | m1-baseline-2026-09-30b | bot-testing-area (dry run, no research) | 8 | $/question | mean $0.0069, max $0.0098 | 23/24 forecaster outputs parsed after the parser fixes; not a skill measure |
| 2026-09-30 | m1-baseline-2026-09-30c | bot-testing-area (dry run, Gemini research) | 8 | $/question | mean $0.0080, max $0.0155 | 24/24 parsed. Excludes Google's grounding fee (unknown) |
