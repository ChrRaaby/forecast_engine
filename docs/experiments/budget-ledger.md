# Experiment spend ledger

Frontier-model experiment budget: **$180**, approved by Christian 2026-10-07 (ADR-0009 amendment), on top of the $100 PoC.
Every run that spends money adds a row (tools append automatically). Costs are OpenRouter-reported.

| Date | Experiment | Run | What | Calls | Cost | Cumulative | Left of $180 |
|---|---|---|---|---|---|---|---|
| 2026-10-07 | EXP-005 | 20261007T215557Z-pilot | protocol pilot, arms B/C, openai/gpt-6.1-sol, openai/gpt-6-luna, 10 questions (unscored) | 60 | $0.666 | $0.666 | $179.33 |
| 2026-10-08 | EXP-005 | benchmark-forward (planned) | forward test on 11 benchmark questions, arms A/B/C, gpt-6.1-sol + gemini-3.1-pro-preview, k=1; **hard cap $5** (actual cost added as its own row after the run) | 0 | $0.000 | $0.666 | $179.33 |
| 2026-10-08 | EXP-005 | 20261008T211403Z | benchmark-forward, 11 questions, arms A/B/C, openai/gpt-6.1-sol, google/gemini-3.1-pro-preview, k=1 (research incl.) | 98 | $1.972 | $2.638 | $177.36 |
| 2026-11-07 | EXP-002 | 20261107T225241Z | EXP-002 screen (cheap config, booked by hand: the tool crashed on the ledger before this fix), 88 binary records × 2 arms + polarity audit; verdict GO LIVE (no-harm) | 1056 | $1.446 | $4.084 | $175.92 |
| 2026-11-09 | EXP-012 | (planned) | market matcher, one cheap judge call per record (~590 records + weekly top-ups); **hard cap $5** (actual cost added as its own row after the run) | 0 | $0.000 | $4.084 | $175.92 |
| 2026-11-09 | B-79 | 20261109T211141Z | B-79 drift check, 88 binary records (88 with reused arm A), cheap config | 264 | $0.550 | $4.634 | $175.37 |
| 2026-11-09 | EXP-012 | 20261109T211634Z | market matcher, judge google/gemini-3.5-flash-lite, 6 binary records (step 1b, draft table) | 6 | $0.006 | $4.640 | $175.36 |
| 2026-11-09 | B-32 | 20261109T210025Z | A/A replay, 89 binary records × 2, cheap config | 534 | $1.190 | $5.830 | $174.17 |
| 2026-11-09 | EXP-012 | 20261109T212543Z | market matcher, judge google/gemini-3.5-flash-lite, 161 binary records (step 1b, draft table) | 86 | $0.077 | $5.907 | $174.09 |
| 2026-11-09 | EXP-012 | 20261109T212544Z | market matcher, judge google/gemini-3.5-flash-lite, 161 binary records (step 1b, draft table) | 29 | $0.026 | $5.933 | $174.07 |
| 2026-11-09 | EXP-012 | 20261109T212634Z | market matcher, judge google/gemini-3.5-flash-lite, 161 binary records (step 1b, draft table) | 1 | $0.001 | $5.934 | $174.07 |
| 2026-11-09 | B-32 | 20261109T215614Z | A/A replay, 28 binary records × 2, frontier config | 280 | $4.470 | $10.404 | $169.60 |

## EXP-008 research desk: separate line, $50 from the $100 PoC

Christian, 2026-11-01 (F-14 decision 5); outside the $300 season cap and the $180 frontier budget. Booked cost counts Google's
grounding fee at list price ($0.014 per search query); "real" is what is actually paid (Google's fee is $0 while the project stays
inside its 5,000 free queries a month). Add one row a month from the monitor (OpenRouter) and the Google billing report.
**Christian, 2026-11-11 (F-20): real spend counts against the $50; booked is shown beside it.**

| Date | What | Booked | Real (est.) | Real cumulative | Left of $50 |
|---|---|---|---|---|---|
| 2026-11-07 | B-70 pilot (PC): 5 arm-runs, 15 searches, critic + Gemini only, no forecasts | $0.516 | ~$0.03 (grounding inside the free tier) | ~$0.03 | ~$49.97 |
