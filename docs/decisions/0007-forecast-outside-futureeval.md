# 0007: Forecast outside FutureEval to unlock outcomes

- **Status:** Accepted (Christian, 2026-10-03)
- **Date:** 2026-10-03

## Context
Metaculus's API only returns a closed question's resolution to accounts that forecast on it (resources page, post 38928;
research/05). Our backtest census found ~680 eligible past questions, but no outcomes for them. Every question the bot forecasts
live unlocks its outcome later and comes with research stored as of that day, which makes it leakage-free evaluation data (B-38).

## Options considered
1. **FutureEval + MiniBench only**: the prize tournaments. Cons: ~300–400 questions per season plus MiniBench; slow to build a dataset.
2. **Add the Metaculus Cup**: bots allowed, no prizes, compared against a large human crowd. ~a few dozen questions per season.
3. **Add capped main-site questions**: Metaculus explicitly allows testing bots on main-site questions; bot comments stay private.
   Cons: published forecasts are public, and a different question mix from the tournament.
4. **LLM-resolved outcomes** for past questions: possible but noisy; kept as a fallback.

## Decision
Options 2 + 3, as a separate `--mode wide` run inside the tournament workflow, five times a day (00/05/10/15/20 UTC): all new
Metaculus Cup questions plus **2 main-site questions per run (~10/day)**, open, binary/MC/numeric/discrete, scheduled to resolve
within 90 days, soonest-resolving first, not in FutureEval/MiniBench. Same models and safety rails as the tournament run, but
**research is Gemini search only** (`WIDE_CONFIG`): AskNews credits (Pro plan, ~600/month) are kept for tournament questions and
backtest archive searches (Christian, 2026-10-03). Also
apply for the Bot Benchmarking Access Tier (B-39).

## Consequences
- ~300 extra scorable questions per month at ~$0.01–0.02 each (≈ $3–6/month).
- Wide-mode records are tagged by run (`-wide`) and shown separately on the monitor, because their distribution differs from the
  tournament's (protocol §3: report pools separately).
- Forecasts on the Cup and main site are public under the bot's name, which is the point of the comparison but also visible.
