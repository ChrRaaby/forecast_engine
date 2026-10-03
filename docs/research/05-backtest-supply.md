# 05: Backtest question supply (2026-10-03)

**Question:** How many resolved, leakage-eligible questions can we backtest on, and how does that depend on the evaluation
models' release dates? (B-27, I-14, protocol §3.)

Source: `poetry run python -m evals.census` (Metaculus API, 2026-10-03 18:52 UTC). Raw rows: `data/census/questions.jsonl`
(private data repo). Eligible = opened after the cutoff, status resolved, not annulled; binary / multiple choice / numeric / discrete.

## Facts
| Tournament | Questions | Resolved | binary | MC | numeric | discrete | Opened |
|---|---|---|---|---|---|---|---|
| Fall 2025 AIB (32813) | 458 | 458 | 251 | 38 | 138 | 31 | 2025-08-05 → 2025-12-13 |
| Spring 2026 AIB (32916) | 314 | 314 | 172 | 41 | 75 | 26 | 2025-12-15 → 2026-04-13 |
| Summer 2026 FutureEval (33022) | 336 | 335 | 169 | 54 | 77 | 36 | 2026-04-30 → 2026-08-20 |

| Evaluation models released on or before | Eligible questions | binary | MC | numeric + discrete |
|---|---|---|---|---|
| 2025-08-01 | 1,107 | 591 | 133 | 383 |
| 2025-10-01 | 944 | 511 | 119 | 314 |
| 2025-12-01 | 680 | 351 | 99 | 230 |
| 2026-02-01 | 599 | 319 | 89 | 191 |
| 2026-04-01 | 385 | 199 | 62 | 124 |
| 2026-06-01 | 297 | 152 | 49 | 96 |

**Blocker found:** the API returns `resolution: null` for every resolved question when called with the bot account's token
(`is_bot: true`), including main-site questions. Outcomes are therefore not available to the bot account. Annulled questions can't be
told apart yet either, so the counts above are upper bounds by the (small) annulled share.

**Not yet counted:** past MiniBench rounds. The `minibench` slug now points to the next round (id 33129, starts 2026-10-05), and the
earlier rounds' ids still need finding.

## Judgement
- Supply is much better than feared. The bottleneck is outcomes (above) and as-of research (AskNews archive quota), not questions.
- **Recommended cutoff: evaluation models released by about 2025-12-01 → ~680 questions.** That is enough for DEV ≈ 400 and
  HOLDOUT ≈ 250 (MDE ≈ 0.008–0.010 Brier at the assumed SD, protocol §5), with models only ~10 months older than the live roster, which
  keeps the "scaffolding effects transfer" assumption (protocol §1) less stretched than an Aug-2025 cutoff would.
- Candidate evaluation models (OpenRouter listing dates; **verify against vendor announcements before use**): `openai/gpt-5-mini`
  (2025-08-07), `anthropic/claude-haiku-4.5` (2025-10-15), `deepseek/deepseek-v3.2` (2025-12-01, borderline), `google/gemini-2.5-flash`
  (2025-06-17). Several have `:batch` variants at half price.
- AskNews archive at 5 calls per query caps as-of research at ~175 questions/month (research/03 addendum b): ~4 months for 680 questions
  at one query each. Free dated sources (I-14 idea 4) or one month of AskNews Pro would shorten that.

**Update, same day:** a token from Christian's personal (non-bot) account also gets `resolution: null`, on the list and detail
endpoints, for bot-tournament and main-site questions alike (checked 2026-10-03 on posts 43155, 45793 and others). Aggregation
`score_data` is empty too, and the public question page returns only a stub to scripts. So the hiding is not specific to bot accounts
(my earlier guess was wrong). Outcomes are currently not available through the API to us.

### Metaculus's own guidance (resources page, post 38928, read 2026-10-03; found by Christian)
- **Facts.** "For closed questions, you can access the text and resolution value of any question you have predicted on." That is why
  both tokens see `null`: neither account forecast on those questions. Our bot **will** see outcomes for everything it forecasts, so
  live records become scorable (B-38 is viable).
- A **Bot Benchmarking Access Tier** gives ~250 open and ~250 resolved questions with resolution and Community Prediction, "intended
  for training and evaluation". Requested via the Metaculus Data Needs Form.
- Recommended feedback routes, slowest to fastest: end of the seasonal tournament; end of each 2-week MiniBench; pastcasting after the
  model cutoff (citing the leakage pitfalls paper we already use); comparison with the community prediction (now noisy, since bots are
  close to it).
- Bots may test on closed tournament questions and on **main-site questions** (bot comments there stay private). The **Metaculus Cup**
  accepts bots (no prizes) for comparison with humans. **Market Pulse** ($7k, bots prize-eligible) needs numeric group questions and
  continuous forecast updates.
- **Judgement.** The census pool above has no outcomes for us unless it overlaps the benchmarking tier. The fastest real data engine is
  to forecast more questions live: every question the bot forecasts unlocks its outcome later, with leakage-free research already stored.

Decisions: Christian accepted the **evaluation-model cutoff of about 2025-12-01** (2026-10-03).

## Next
1. Get outcomes: apply for the Bot Benchmarking Access Tier (B-39), and widen live coverage (B-40).
2. Verify evaluation-model release dates; pick the roster (ADR).
3. Find past MiniBench round ids; then build frozen DEV/HOLDOUT manifests.
