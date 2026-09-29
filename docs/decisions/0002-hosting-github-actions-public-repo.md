# 0002: Host on GitHub Actions from a public repo

- **Status:** Accepted
- **Date:** 2026-09-29 (decided 2026-09-28, I-05)

## Context
The live bot needs a scheduler and somewhere to run, within a $100 PoC budget and without ops overhead.

## Options considered
1. **GitHub Actions, public repo**: what the template ships with; unlimited free minutes for public repos. Cons: prompts are public;
   GitHub cron is unreliable (nostreambot measured ~22% of scheduled firings delivered, research/04).
2. **GitHub Actions, private repo**: prompts stay private. Cons: 2,000 free minutes/month, which a 20-minute cron can exhaust.
3. **GCP (Cloud Run + Scheduler)**: reliable scheduling. Cons: setup and ops cost for no scoring benefit at PoC stage (I-05, dropped by Christian).

## Decision
Option 1: public GitHub repo `forecast_engine`, bot runs in GitHub Actions. Secrets live only in `.env` (gitignored) and GitHub
Actions secrets. Mitigate cron unreliability with redundant cron entries (B-06); an external dispatcher calling `workflow_dispatch` is
the documented fallback.

## Consequences
- Zero hosting cost.
- Prompts and code are public. Acceptable: the edge is expected to come from research quality and evaluation, not secret prompts.
- Every commit must pass a secrets check before push; `.env.template` holds placeholders only.
- Forecast records and research bundles are uploaded as workflow artifacts, not committed (they may contain licensed news text).
