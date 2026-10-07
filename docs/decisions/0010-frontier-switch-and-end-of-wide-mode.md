# 0010: Frontier roster goes live on tournament questions; wide mode ends

- **Status:** Accepted (Christian, 2026-10-07)
- **Date:** 2026-10-08

## Context
ADR-0009 set up a frontier ensemble for FutureEval and MiniBench, with the roster left to B-16. Christian decided on 2026-10-07 to
switch now, ahead of the planned week of clean MiniBench runs, because method work only matters in the context of the models the
tournament bot will use. The same day Metaculus approved our Bot Benchmarking Access Tier request (B-39): about 250 resolved and 250
open questions with outcomes and community prediction. ADR-0007's wide mode (Cup and main-site questions, about 10 a day) existed
only to unlock resolved questions for evaluation, so the dump replaces its purpose.

## Options considered
1. **Roster A: GPT-6.1 Sol, Claude Sonnet 5.5, Gemini 3.1 Pro, Grok 4.7, Qwen3.8 Max** (chosen): five vendors, incl. Grok and a
   Chinese model (Christian's requirement). Qwen rather than DeepSeek because the cheap shadow already contains DeepSeek.
2. **Roster B:** the same with DeepSeek V4 Pro instead of Qwen; slightly cheaper, one vendor fewer across live and shadow.
3. **Keep wide mode alongside the dump**: more resolved questions, but Cup/main-site forecasts are made late (coverage 0.5–2.4%), so
   official scores say little, and the benchmarking data covers the evaluation need.

## Decision
- **Roster A** for `TOURNAMENT_CONFIG` (`forecast_engine/config.py`), reasoning effort medium, median of five, at least three must
  succeed. Gemini 3.1 Pro is from February 2026, older than the rest; Google has no newer Pro-tier model on OpenRouter.
- **Measured cost** (replay of 4 stored tournament questions on their frozen research, 2026-10-08): $0.10–0.14 per question for the
  five members incl. the polarity check, plus ~$0.01 for the cheap shadow. That is below ADR-0009's $0.32 estimate, because GPT and
  Claude used few output tokens; Grok used the most (up to 8k). About 800 questions would cost ~$110 of the $300 season budget.
- **Season cap in code** (`forecast_engine/spend.py`): every live frontier forecast adds its cost to a Fall 2026 total kept in the
  Actions cache next to the retry ledger. At $300 the tournament falls back to the cheap config. If the cache is lost the total
  restarts at 0, so the OpenRouter key limit stays the hard backstop. Run cost cap for tournament runs raised to $10.
- **Shadow-run mode (B-62)**: `SHADOW_CONFIGS` run unpublished on every tournament question with the live question's research, in
  `data/forecasts/<run>-shadow-<name>/` with status `shadow`. Today it holds the cheap M1 ensemble (ADR-0009's paired comparison);
  experiments add their arm there. A shadow failure never affects the live forecast.
- **Wide mode stops**: removed from the tournament workflow. `--mode wide` still runs by hand. Outcomes for the wide-mode
  questions already forecast keep being collected as they resolve. ADR-0007 is superseded.

## Consequences
- Tournament forecasts change model family: CONFIG_VERSION `frontier-2026-10-08`. Results before and after are separate config eras.
- The paired frontier-vs-cheap comparison accrues only on tournament questions, at the tournament's pace.
- Fewer resolved live questions per week (no wide mode) until the benchmarking dump arrives; evaluation leans on B-39 data.
- Frontier models can't be backtested on questions before their release (protocol §1), so the frontier era's evidence comes from
  live and shadow results, and from live-replay experiments (ADR-0009 amendment).
