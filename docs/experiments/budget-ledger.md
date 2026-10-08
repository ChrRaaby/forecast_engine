# Experiment spend ledger

Frontier-model experiment budget: **$180**, approved by Christian 2026-10-07 (ADR-0009 amendment), on top of the $100 PoC.
Every run that spends money adds a row (tools append automatically). Costs are OpenRouter-reported.

| Date | Experiment | Run | What | Calls | Cost | Cumulative | Left of $180 |
|---|---|---|---|---|---|---|---|
| 2026-10-07 | EXP-005 | 20261007T215557Z-pilot | protocol pilot, arms B/C, openai/gpt-6.1-sol, openai/gpt-6-luna, 10 questions (unscored) | 60 | $0.666 | $0.666 | $179.33 |
| 2026-10-08 | EXP-005 | benchmark-forward (planned) | forward test on 11 benchmark questions, arms A/B/C, gpt-6.1-sol + gemini-3.1-pro-preview, k=1; **hard cap $5** (actual cost added as its own row after the run) | 0 | $0.000 | $0.666 | $179.33 |
| 2026-10-08 | EXP-005 | 20261008T211403Z | benchmark-forward, 11 questions, arms A/B/C, openai/gpt-6.1-sol, google/gemini-3.1-pro-preview, k=1 (research incl.) | 98 | $1.972 | $2.638 | $177.36 |
