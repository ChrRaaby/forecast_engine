# 0005: Evaluation-ready architecture (pure forecasting core)

- **Status:** Accepted (Christian, 2026-09-29)
- **Date:** 2026-09-29

## Context
The evaluation protocol (§7) requires the same code path for live and backtest, as-of enforcement everywhere, and research separated
from reasoning so reasoning-only changes can be compared on identical, cached research. The template's `ForecastBot` runs research,
forecasting and publishing in one loop, and (to be verified in M1) its prompts read today's date from the system clock, which violates
protocol T5. If M1 copies that shape, M2 has to rewrite the bot before it can be evaluated.

## Options considered
1. **Use `ForecastBot` as-is in M1, restructure in M2**: fastest M1. Cons: M1 results come from a code path the harness can't
   replay; the M2 refactor risks changing behaviour we'd then be unable to compare against.
2. **Pure core from day one, template as a thin adapter**: `forecast(question_snapshot, as_of, research_bundle, config) -> ForecastRecord`
   and `gather_research(question_snapshot, as_of, research_config) -> ResearchBundle` are our own functions; the `ForecastBot` subclass
   only fetches questions, calls them, and publishes. Cons: a little more work in M1; we diverge from upstream sooner.
3. **Drop `forecasting-tools` entirely**: Cons: see ADR-0001, option 3.

## Decision
Option 2. Specifically:
- One `Clock` supplies "today" (live: now; backtest: the question's as-of time). No other code reads the system clock.
- Research returns dated items; the as-of guard is built in M2, but the interface carries `as_of` from M1.
- Every forecast writes a `ForecastRecord` (config version + hash, model IDs, params, exact prompts, raw outputs, tokens, cost,
  timestamps) and its `ResearchBundle` to disk.
- Model IDs and the ensemble roster live in one config module with a `CONFIG_VERSION`.
- The harness (`evals/`, M2) imports the core; it never imports the template adapter.

## Consequences
- M1 forecasts are replayable by the M2 harness, so M1 becomes a genuine baseline.
- Slightly more M1 work and earlier divergence from upstream template changes.
- The "no system clock" rule needs a test from M1 onward (cheap: grep prompts rendered with a fixture date).
