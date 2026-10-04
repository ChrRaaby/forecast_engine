# 0008: As-of research layer for backtests (AskNews archive, cache, leakage screen, credit guard)

- **Status:** Proposed (B-28; awaiting Christian's review after the B-45 QA)
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

## Verified on the PC (2026-10-03, Claude on Christian's PC)
- `evals.asknews_check` (as of 2025-11-01, Fed question): **all six checks pass.** 10 articles returned and kept, none after as_of,
  all dated with time zones, kept articles span ~15 days (the 30-day window is used, not a 24 h default), cache round trip identical.
  Ledger: 5 credits.
- **Screen false positive found and fixed.** Prompt v1 flagged a 30 Oct 2025 article about the 29–30 Oct Fed cut as describing an event
  "after" the 2025-11-01 reference date (2 of 2 runs), which would have excluded a clean question. Prompt **v2** (`leak-screen-v2`) says
  publication dates are already machine-checked and asks the model to date the described event before flagging. Calibration on the
  same real bundle plus two planted hindsight items ("went on to cut… December 10, 2025"; "in the end… held rates steady in December"),
  2 runs each: v1 caught both plants but flagged the clean bundle; v2 kept the clean bundle clean and caught both plants every time.
  Small sample: measure the flag rate on the first real batch (still the plan above). Cost: 10 credits + ~$0.02.
- `tools/check_models.py` now also checks the screen model: `google/gemini-2.5-flash` resolves on OpenRouter, listed 2025-06-17,
  matching the assumed release date.

## B-45 code review (2026-10-04, Claude on Christian's PC)
A full review of the PR found ten issues; all are fixed on the branch with regression tests (75 tests pass):
1. A "clean" screen verdict stored for an empty, failed search could later be reused for the real articles, which then were never
   screened. Verdicts are now bound to the cached bundle's content hash and only stored for cached bundles.
2. Stored verdicts had no integrity checks. They now carry key, screen-config hash and bundle hash, and their status is validated.
3. The screen model's release date was a separate field, so a newer model passed the cutoff check with the default's date. Dates now
   come from a table; unknown models are refused.
4. The cache key ignored the question text the archive query is built from. It is now part of the key and checked on read (T4).
5. Titles and URLs of articles dated after as_of were stored in the bundle's call record. Only the reason (and dates for other
   rejections) are kept now.
6. On the first UTC day of a new billing period the guard forgot the old period, although AskNews may not have reset yet. It now
   applies the stricter of the two that day.
7. Two processes could both pass the budget check. `reserve()` now runs under a cross-process lock file (stale locks broken).
8. Two processes could overwrite the same cache entry. Writes are now create-only (hard-link), with unique temp names.
9. A module-level asyncio lock broke on a second `asyncio.run()`. Locks are per event loop now.
10. The live check silently skipped the 30-day-window test with fewer than 3 articles; it now fails. Live-credit counting reads the
    compact `forecasts.jsonl` logs instead of every full record.

## B-45 screen validation and the decision change (2026-10-04)
On 50 real bundles (30 development, 20 confirmation) with 100 planted leaks (research/07), a single screen excluded far too many clean
questions (prompt v2: 37–67%; prompt v3: 10–30%). **Changed decision (proposed):** the screen is now `DualScreenConfig`: prompt v3,
GPT-5 mini (low reasoning, 16k tokens) and Gemini 2.5 Flash, and a question is excluded as a leak only if **both** flag it, and as
screen_failed if either fails (fail closed). Result: 7% and 0% of clean bundles excluded on the final runs (0–13% across reruns), no
planted leak missed (0/100), and the date bound held on all 50 dates. Also: verdict parsing takes the first JSON object, an API error
is retried once, and a verdict whose free-text reason is broken is recovered only if it says "leak" (a broken "clean" still fails
closed). The agreement rule was chosen after seeing the confirmation set, so it gets a fresh confirmation sample of about 20 questions
after the AskNews period resets on 30 October.
