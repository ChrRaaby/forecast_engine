# EXP-012: Prediction-market prices as a second opinion (B-13)

_Card written BEFORE running: pre-registered 2026-11-09, approved by Christian the same day. Written before any market
price or outcome was looked at. Results section filled by the report generator._

## In plain words

Some tournament questions are also traded on prediction markets (Polymarket, Kalshi, Manifold). A liquid market is a
strong crowd forecast. The idea: when a question has a matching market, mix the market's price into our forecast.

Example: "Will the Fed cut rates in December?" Our bot says 45%, Polymarket says 70% at the moment we forecast.
A 50/50 blend (done on the log-odds scale, which is the standard way to average probabilities) gives **58%**.
If the market is usually right where we disagree, the blend scores better. If the market is badly matched or thin,
the blend scores worse. That is what the test measures.

```
 our past forecasts (~590, each with its time stamp)
          │
          ▼
 [1] find markets that EXISTED at that time ──► cheap AI judge: same question? (A = same, B = close, none)
          │                                        (judge never sees prices or outcomes)
          ▼
 [2] price AT that time from the market's history (never today's page)
          │
          ▼
 [3] freeze the match table (commit + hash)  ◄── only now are outcomes allowed in
          │
          ▼
 [4] score three arms on resolved matched questions:
       A = bot as published   B = 50/50 blend   C = market alone
```

## Card

- **Date / author:** 2026-11-09, Claude (Opus), for Christian. Step 0 (F-16 rules) settled by Christian's decision
  2026-11-09: market prices are fair game.
- **Hypothesis:** on questions with a same-question market, blending the market price into the bot's forecast improves
  log score, because liquid markets aggregate many informed traders (I-20; R-06 two-source finding, moderate, other teams;
  nostreambot uses market prices, other team). Our own evidence: none yet.
- **Change vs. baseline:** post-processing only, applied after the bot's final forecast. No prompt change, no extra
  forecasting calls. Unmatched questions are untouched.
  - Blend: `logit(p_B) = 0.5·logit(p_bot) + 0.5·logit(p_mkt)`, market price clamped to [0.01, 0.99]. Weight fixed at
    0.5 here; no weight tuning on this data (a weight sweep may be shown as a descriptive chart only).
- **Question set:** all live binary forecast records (frontier roster as published) from Fall 2026 FutureEval and
  MiniBench, from 2026-10-01 on, that get a **grade-A, real-money** match (Polymarket or Kalshi). Manifold-only and grade-B
  matches are reported separately, descriptive only. MC and numeric questions are out of scope for this card.
  Clusters as in the report generator (same series/event = one cluster).
- **Evaluation model(s):** none for forecasting (arm A is the record as published). Matcher: one cheap model outside the
  forecasting roster (Gemini 3.5 Flash-Lite), temperature 0.
- **Primary metric:** binary log score (nats), paired per question, B − A. Secondary: Brier, calibration, C − A, B − C.
- **Data today (counted 2026-11-09 from `data/outcomes/outcomes.jsonl`):** 89 resolved binary questions (67 resolved in
  October, 22 in the first 9 days of November, so roughly 2–3 a day), plus 65 binary questions closed and awaiting
  resolution. At a 10–30% match rate that is about 9–27 matched resolved questions today and an estimated 25–75 by
  end of January 2027 (estimate, assumes the current pace holds).
- **Expected effect & MDE (honest):** expected +0.03 to +0.08 nats per matched question (a guess; no own data).
  Assuming SD of the per-question difference ≈ 0.20 nats (guess, measured at analysis): N = 40 → MDE ≈ 0.09;
  N = 80 → 0.06. **A first read at N ≈ 40 can only detect a large effect.** Certainty will be stated as LOW unless N ≥ 80.
- **Arms:** A baseline; candidate arms B and C, so m = 2 and the CI level is 97.5% (protocol §5). B and C add **no
  forecasting calls** (same compute as A), so no compute-matched control is needed. The matcher call is shared
  infrastructure, made once per question whatever the arm.
- **Tier (protocol §6.2):** Tier 1 (post-processing, no prompt change, one flag to undo). If adopted, the live bot publishes
  B on grade-A matches and the unblended forecast keeps being recorded as the shadow (it is the same record, so free).
- **Rerun:** first run. A narrow win or inconclusive result gets one rerun on matched questions **resolved after** the
  first analysis date (`--resolved-after`), same arms and rule.
- **Repeats k:** 1 (no stochastic model calls in the arms; the matcher runs once and is frozen).
- **Cost estimate:** matching ≈ $0.001 per question → under $1 for all ~590 records, plus ~$0.10/week afterwards.
  Market APIs are free and read-only (from memory; checked in step 1). Scoring $0. Build time on the PC is subscription
  time, not API money. Ledger line: $5 cap.

### Matching rule (frozen here)
1. **Candidates:** keyword search on each venue for the question title. Keep a market only if it was **created before**
   the record's `as_of` and was still **open** at `as_of`.
2. **Judge input:** the Metaculus question text, resolution criteria and close date as forecast; for up to 5 candidates,
   the market title, rules text and end date. **Never** prices, volume, status, outcome or resolution notes.
3. **Grades:** **A** = same event, same threshold/number, deadline within ±3 days, compatible resolution source.
   **B** = same event and threshold, deadline within ±31 days or a slightly different source. **None** otherwise.
   The judge also returns **polarity** (same or inverted wording); inverted prices are flipped in code.
4. Several grade-A real-money markets: average their log-odds. Venue order for reporting: Polymarket, Kalshi, Manifold.
5. **Blind audit before freezing:** 20 random grade-A matches are checked by hand with no outcome shown. More than 2 wrong
   → fix the judge prompt (still blind) and re-run the whole table once. Then freeze.

### As-of price rule
- The last price point at or before `as_of` from the venue's price history (Polymarket price history, Kalshi trades or
  candles, Manifold bets). If that point is more than 7 days old, the market counts as dead: no price.
- The price age is stored with every match.

### Leakage checks (must all pass before the table is frozen)
| # | Check | How |
|---|---|---|
| LK1 | No price from after the forecast | Unit test: synthetic history with a planted 99% point 1 minute after `as_of` must not be picked |
| LK2 | No market created after the forecast | Unit test: planted late market is dropped |
| LK3 | Judge never sees outcomes | Unit test: fixtures with "resolved Yes", final prices, closed status, resolution notes; none may appear in the rendered judge prompt |
| LK4 | Outcomes only after the freeze | The matcher code never reads the outcomes file (test); the scorer refuses to run unless the match table's hash is committed in the data repo first |
| LK5 | Real-data audit | For every match: price time ≤ `as_of` and market created ≤ `as_of`; any violation stops the run and is reported as a bug |
| LK6 | Price-history granularity | Measured and reported (closed markets may only keep coarse history; from memory, unchecked). Coarse points still respect LK1 but raise price age |
| LK7 | No back door through research | `evals/asof.py` keeps blocking market sites in research (already in place); prices enter only through this path |

Residual risk, stated: the judge model may "know" how a question ended and lean towards calling a match. Mitigated by the
blind audit and the date rule in code; not removable.

### Decision rule
1. **Futility check, before any outcome (after step 1):** if fewer than 8% of binary records get a grade-A real-money
   match, **or** the median bot-vs-market gap on matched questions is under 5 percentage points, B-13 is parked: the blend
   could not move our score enough to matter. Reported with the numbers.
2. **Analysis point:** when 40 grade-A real-money matched questions have resolved, or on 2027-01-31, whichever comes first.
   Fewer than 20 by then → descriptive only, no decision, new analysis point written then.
3. **Adopt B** (Tier 1, behind a flag, Christian's explicit OK before publishing) if the 97.5% CI of B − A is above 0
   **and** its lower end is at least the smallest effect of interest, **0.015 nats**, with no calibration regression.
   CI above 0 but lower end under 0.015 → narrow win → one rerun on fresh questions.
4. **Kill** B if the 97.5% CI of B − A lies entirely below 0.
5. **C (market alone)** is judged by the same rule. If C also wins, B is still the one adopted (safer against a wrong match);
   a higher weight would need its own card.
6. Inconclusive → keep the bot unchanged, one rerun on fresh questions (§6.3), then drop if still inconclusive.

### What happens in which step
| Step | What | Where / model | Cost | Needs |
|---|---|---|---|---|
| 1a | Build market clients, as-of price, matcher, tests LK1–LK4 (eval code only; live bot untouched) | PC, Sonnet 5.5 | ~$0 | Christian's OK |
| 1b | Run matcher on all ~590 records, LK5/LK6 audit, blind audit, freeze table; report match rate per tournament and the futility check | PC | < $1 | — |
| 1c | "Market said X%" column in the weekly miss review (B-72), no forecast change | routine | $0 | — |
| 2 | Scored analysis at the analysis point | PC or cloud | $0 | enough resolved matches |
| 3 | Live blend behind a flag (needs live code to fetch prices at forecast time; CI on 3.11) | PC | ~$0 | a pass + Christian's OK |

Later, separate card: price **in the prompt** (the models see the price). Risk: they just copy it. Screened on the cheap
roster reusing the stored baseline arm (B-79) per protocol §8, on matched questions only.

Note: using the price makes the forecast useless as a trading signal (research/06); the paper-trading branch keeps the
price-free arm A, which is recorded anyway.

## Results (generated)
## Decision
