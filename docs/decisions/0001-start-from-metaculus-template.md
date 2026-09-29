# 0001: Start from the Metaculus bot template

- **Status:** Accepted (for M1; revisit at M3)
- **Date:** 2026-09-29

## Context
M1 needs a bot forecasting live on Fall 2026 FutureEval + MiniBench quickly and cheaply, as a baseline to measure against.
We have ~10 h/week (I-09). Three starting points exist (research/00, research/04).

## Options considered
1. **Metaculus `metac-bot-template` (uses `forecasting-tools`)**: official, small, maintained by Metaculus; handles the API,
   question types, publishing and GitHub Actions scheduling. Cons: its orchestration (`ForecastBot`) owns the research → forecast
   → publish loop, which doesn't separate research from reasoning the way the evaluation harness needs; default numeric pipeline is weak (R-19).
2. **Fork nostreambot**: proven (15th/277), many good ideas. Cons: ~280k lines of AI-generated code we can't maintain on 10 h/week;
   ~$2.60/question, 5× our cap (research/04).
3. **Build from scratch**: full control. Cons: re-implements the Metaculus API client, question parsing and publishing for no
   scoring benefit; slowest route to a live baseline.

## Decision
Option 1. Bring in the template's code (not its history), keep an `upstream` reference, and stay close to it for M1.
Use `forecasting-tools` for the Metaculus API, question objects and publishing. Our own forecasting core sits behind a thin
adapter (ADR-0005) so the harness isn't tied to `ForecastBot`'s orchestration. Borrow individual ideas from nostreambot, not code.

Stack follows the template for now: Python 3.11+, Poetry. uv vs. Poetry is deferred until it matters.

## Consequences
- Fastest path to a live baseline; upstream fixes can be pulled.
- We depend on `forecasting-tools` internals; the adapter limits the blast radius.
- At M3, re-check whether the template still earns its place or the core has outgrown it.
