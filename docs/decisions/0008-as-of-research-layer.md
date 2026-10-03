# 0008: As-of research layer for backtests (AskNews archive, cache, leakage screen, credit guard)

- **Status:** Proposed (B-28; awaiting Christian's review of the PR and the live check)
- **Date:** 2026-10-03

## Context
Backtests need research as it was available at each question's as-of time (evaluation protocol T2, T3, T7, §7). The live bot
uses AskNews "latest news" plus Gemini grounded search. Gemini search can't be date-restricted, so backtests use the AskNews
historical archive only. An archive search costs 5 AskNews credits. Christian's Pro plan includes **500 credits per billing period**
(usage page, 2026-10-03: period "Sep 30, 2026 - Oct 29, 2026", so periods are anchored on the 30th, not the calendar month).
AskNews allows up to 100k credits of overage, paid from a $5 wallet (≈ 250 credits at $0.02), so nothing on AskNews's side stops a
runaway batch. The live bot spends 1 credit per tournament question from the same pool.

Facts from the SDK (`asknews` 0.13.45): `news.search_news(historical=True, start_timestamp, end_timestamp, time_filter=
"pub_date"|"crawl_date")`; the default `time_filter` is `crawl_date`; returned articles carry only `pub_date` (no crawl or update
date); `None` parameters are left out of the request; the SDK has no usage or credits endpoint.

## Options considered
1. **Trust the archive's own date filter**: simplest. Cons: no check on mis-dated items or a server that ignores the bound; the
   protocol requires a guard that raises.
2. **Server filter plus client-side guard, a write-once cache, an LLM screen and a local credit ledger** (chosen).
3. **Item-level screen (drop flagged articles, keep the question)**: keeps N higher. Cons: lets the screen shape what the
   forecaster sees; with a model that knows outcomes, that is direct leakage.

## Decision
Option 2, all in `evals/`, reusing the core's `ResearchBundle`, `ResearchItem`, `LlmCall`, `Clock` and `LlmClient`:
- **Date guard** (`evals/asof.py`): an item is usable only with a timezone-aware `published_at` ≤ as_of and a URL outside the
  forecast-aggregator and prediction-market domains (T7). Retrieval drops and counts offenders; `assert_bundle_as_of` raises and runs
  on every cache write and read. An item dated exactly at as_of is allowed.
- **Archive search** (`evals/asknews_archive.py`): `end_timestamp = floor(as_of)`, `start_timestamp = as_of − 30 days`,
  `time_filter="pub_date"` (the date we can verify), `hours_back` not sent. Requires a `FixedClock`. Rejections are recorded by
  reason with title/URL/date only. Articles dated after as_of are additionally reported as a bundle error, because they mean the server
  ignored the bound.
- **Research cache** (`evals/research_cache.py`): key = sha256(question id, as_of in UTC, retrieval-config hash), stored under
  `data/research_cache/` (private data repo; bundles hold licensed text). Write-once, content hash verified on read, failed searches
  never cached. Screen verdicts are stored per screen-config hash, so changing the screen never re-buys research.
- **Leakage screen** (`evals/leakage_screen.py`): one LLM call per bundle; a flagged question is **excluded and counted**, never
  repaired. Fail closed: an error or unparseable verdict excludes the question as `screen_failed`. Run summary warns above 10% (T3).
  **The screen model must be released on or before the evaluation-model cutoff (2025-12-01)** and before the bundle's as_of
  (Christian, 2026-10-03), enforced in `ScreenConfig`. Reason: a model that knows outcomes can flag on that knowledge, which makes the
  excluded set correlate with outcomes (a selection leak) and adds false positives. The cost is lower recall on undated hindsight. A
  newer model may later be added as an **auditor** on a sample (reported only, never excluding), if wanted. Default model
  `google/gemini-2.5-flash` (released 2025-06-17); verify the id with `tools/check_models.py` and the date against Google's announcement.
- **Credit guard** (`evals/asknews_budget.py`): an append-only ledger `data/asknews_ledger.jsonl`; credits are written **before**
  each call and count even if it fails. Per billing period (anchor day 30), `remaining = limit − max(archive + live, observed) −
  max(0, live_budget − live)`, with limit = 500 (plus the wallet only with `--allow-overage`) and live_budget = 200, so archive searches
  get 300 credits (60 searches) unless live use runs higher. Live use is counted from `data/runs/` records (lags until the daily sync);
  `--used N` takes the authoritative number from the AskNews usage page. The counting window starts a day before the period, because
  the time zone of AskNews's reset is unknown. A batch must pass `check(total credits)` before its first call.
- **Live check**: `poetry run python -m evals.asknews_check` (one search, 5 credits, plus one screen call ≈ $0.001); `--dry-run` spends
  nothing.

## Consequences
- Every backtest research call is budgeted, dated, cached and screened; a leak needs both a wrong `pub_date` and a screen miss.
- **Known limitation:** AskNews gives one date per article. A story edited after as_of, or an AskNews summary written later, passes the
  date guard; only the screen can catch it. Measure the screen's flag rate on the first real batch.
- Archive capacity is about 60 searches per period on the Pro plan, i.e. ~60 questions/month at one query each. A one-off credit
  purchase (acceptable to Christian, log 2026-10-03) is the lever if that's too slow.
- The batch command over a question manifest is deferred to the runner (B-30); it must call `budget.check()` with the whole batch's
  estimate first.

## To verify on the PC
- `evals.asknews_check` passes: the server honours `end_timestamp`, every article is dated, and the kept articles span more than a
  day (that is, no hidden 24 h `hours_back` default on the server).
- The screen model id resolves on OpenRouter.
