# EXP-013: Research v2 (B-80)

_Card written BEFORE running: **frozen (pre-registered) 2026-11-12**, at Christian's request, before any EXP-013 arm was switched
on or scored. Draft v1/v2 2026-11-11 (project review), Christian's choices of 2026-11-11 and 2026-11-12 written in. Backlog B-80;
idea I-23; review https://claude.ai/artifact/RmzNXxuCP9Wk39yv3gSkTv. Runs beside EXP-008 (Christian, 2026-11-11); EXP-008's
arms, card and analysis are not touched. Code: `forecast_engine/research_v2.py`, `EXP013_ARMS` in `forecast_engine/config.py`
(PR #35, 7c911cc, merged switched off). Results section filled by the analysis._

- **Date / author:** 2026-11-11, Claude (cloud), from the whole-project review.
- **Hypothesis:** the bot is limited by its research brief, not by its reasoning (I-23). Our evidence, exploratory: frontier and
  cheap rosters score the same on the same brief (+0.019 nats, n = 38 binary, CI −0.046 to +0.085); 4 of 12 miss cards are tagged
  "a findable fact was missed"; 195 of 245 frontier questions link a resolution source that the bot never opens; the Gemini
  Flash-Lite brief stated an untraceable figure on 46421 (−96 points); every AskNews article was under ~2 days old and the query is
  the whole question sentence. Other teams: R-03 (strong), R-06 (moderate). Three separate fixes, each its own arm.
- **Change vs. baseline:** members (cheap roster), forecasting prompt and aggregation unchanged. Only the research the members
  read differs. Unpublished shadow arms on every live tournament question, like EXP-008.

  | Arm | Shadow name | What differs from the live research | Extra calls per question |
  |---|---|---|---|
  | A baseline | `cheap` (already running) | nothing | 0 |
  | B source | `exp013-b-source` | Up to 2 links from the question's resolution criteria / fine print are fetched (plain HTTP GET). A cheap model extracts "current value relevant to the question, its date, a short quote" or "NOT FOUND / not retrievable". That block goes at the top of the brief, labelled "Resolution source, fetched <time>". Rest of the brief as live | 1 cheap extraction call + ≤ 2 page fetches |
  | C writer | `exp013-c-writer` | The Gemini Flash-Lite brief is replaced by a brief from a stronger search model, with rules: a link and a date for every number; "not found" instead of an estimate; state the latest value of the resolution source if found. AskNews part as live | 0 (one call replaced by a dearer one) |
  | E frontier + source | `exp013-e-frontier-source` | The **live frontier roster** with arm B's resolution-source block, compared with the published live forecast (its own baseline). Added 2026-11-11 (Christian) so a win for B can go live without a second confirmation round | 5 frontier forecasts + B's extraction (shared with B) |
  | D news | `exp013-d-news` | AskNews is queried with keywords written by a cheap model instead of the full question sentence, and runs the historical ("news knowledge") search (last 365 days) next to "latest news"; historical articles are tagged as such. Gemini brief as live. **Full arm D on every question (Christian, 2026-11-12)** | 1 cheap keyword call + 2 AskNews calls (~6 extra credits) |

  Build details (fixed at freeze): B/E extractor `openai/gpt-5-mini` (reasoning low), one call for all fetched pages, quote
  checked against the page text (a quote not found on the page turns the block into NOT FOUND); public addresses only,
  same-host redirects (≤ 3), 2 MB read cap, 40,000 characters kept and stored in the record. B and E share one fetch +
  extraction per question. C: `gemini-3.8-flash`, thinking "low", records Google queries per call and flags brief links not
  among the grounding sources (kept, marked for review). D: keyword writer `openai/gpt-5-mini` (reasoning low), 8 + 8 articles.
  Any failed step falls back to the live research for that part and the error is recorded; replays refuse live-web steps.

  Combinations are not arms. If more than one arm wins, the combination is what goes to the Tier 2 confirmation.
- **Model for arm C (decided).** Two options (corrected 2026-11-11 after Christian's question: Google grounding *can* be
  used; the first draft's "must not" was too strong, it is a cost and design choice):
  1. **A stronger Gemini model with Google Search grounding** (e.g. the Pro tier). Smallest build (the provider code exists)
     and the cleanest test: only the writer and its rules change, the search engine stays the same. Cost: about 2 Google
     queries per question on top of today's ~14, so roughly +450–520 a month, total ~3,500–4,300 of the 5,000 free in a
     normal month. A busy month would pass 5,000 and cost $0.014 per query over it (1,000 over = $14). Unchecked: whether a
     Pro-tier model runs more queries per call than Flash-Lite, and whether the free tier covers it (F-02 only saw the
     Flash-Lite SKU). Measure both in a 5-question pilot before the freeze.
  2. **A search-enabled model through OpenRouter** (no Google queries). Adds a second, independent search engine, which is
     what R-06 / R-16 recommend, but then a win cannot be split into "better writer" and "different engine". Names and prices
     not checked from here; the PC lists candidates with `tools/check_models.py`.
  **Decided by Christian 2026-11-11: option 1; model gemini-3.8-flash decided 2026-11-12 after the pilot** (stronger Gemini model with Google Search grounding), capped at $0.05 per
  question at list price; the exact model and the pilot's query count are written here before the freeze.
- **Question set:** every FutureEval and MiniBench question the live bot forecasts after switch-on. Live only (pages and
  searches cannot be repeated as of a past date), so no leakage question arises. A question enters the analysis only if arms
  A–D all have a record (a failed arm is counted as a failure, reported per arm). Clusters: one per Metaculus post.
- **Evaluation model(s):** the cheap live roster at forecast time.
- **Primary metric:** Metaculus baseline score per question across **all question types** (protocol §4, 2026-11-11), paired
  vs arm A. Always shown next to it: the same per type; binary log score and Brier; calibration; failure rate; $/q per arm.
  Descriptive only: share of questions where arm B retrieved a value, share "not retrievable" (PDF, script-only dashboards,
  blocked sites), share of member answers that cite the new block.
- **Arms:** m = 3 cheap candidates (B, C, D), CI level 1 − 0.05/3 = **98.3%** at the final look (99.5% at the early look); arm E is
  B's frontier test with its own rule (Decision rule). **No compute-matched control (decided by Christian
  2026-11-11):** no arm adds forecaster answers to average, B's extra call only reads one page, C replaces one call, D adds one
  news search. EXP-008's control arm D (two generic extra searches) runs on the same questions and is reported as a
  descriptive reference for "more search of any kind". Alternative: add a control with one extra generic Gemini search
  (costs Google queries).
- **Expected effect & MDE:** priors: B a clear gain on "what will this report say" questions and little elsewhere; C small
  to medium; D small. The paired SD on the all-type score is not known yet; it is measured at the analysis and the MDE
  restated there. Rough guide from F-13 (binary, cheap roster, replay noise only): about 0.03 nats at 150 questions; live
  arms with different research will be noisier, the larger all-type N helps.
- **N and timing (decided by Christian 2026-11-11: speed matters, one planned early look):** two looks, no others.
  Note (checked 2026-11-11, Metaculus API): FutureEval stops opening questions for forecasting on **2027-01-06**; after that only
  MiniBench (~45 questions every two weeks) feeds the arms, so the last weeks to 31 Jan add fewer questions.
  - **Early look at 150 resolved questions** of any type with records in all arms (expected about the first week of January
    if switched on in late November; estimate).
  - **Final look at 300 resolved questions or on 2027-01-31**, whichever comes first. If N < 150 at the date, the result is
    reported as a screen.
  No scores of these arms are looked at outside these two looks.
- **First-week check (no scores):** fetch success rate and failure reasons for arm B, extra seconds per question, real $/q
  per arm, AskNews calls used, any shadow failures. Stop an arm if it fails often or runs past its budget pace.
- **Tier:** C and D Tier 2 (confirm before live). B via arm E: live on the no-harm rule with the old setup as shadow
  (Tier 1 style), Christian's decision 2026-11-11 for this card.
- **Rerun:** first run. Narrow win or inconclusive → keep A, one rerun on questions resolved after the analysis date.
- **Repeats k:** 1.
- **Cost estimate (new spend per question, estimates, unmeasured):** B ~$0.015 (forecast ~$0.012 + extraction ~$0.003);
  C ~$0.023 (forecast + the 3.8 Flash brief, pilot ~$0.011 tokens, capped); D ~$0.013 plus **~6 extra AskNews credits**. At 50–60 questions a
  week: about $3–5 a week, $28–50 to 31 January for B, C and D. Arm E: ~$0.12/q, $57–68 to 31 January. **Budget (Christian OK with the
  spend, 2026-11-11):** B, C, D on a separate **$60 line, real spend counts** (as F-20); arm E from the $180 frontier
  experiment budget ($169.60 left). Whole-project estimate to 31 January: $200–260 real.
  **AskNews (checked 2026-11-11):** plan 500 credits per period (30 Oct–29 Nov), live uses ~1 credit/q (~210 a period). Full arm D
  costs ~7 credits/q (keyword latest 1 + historical 5, assumed), ~1,470 a period, so it runs over the plan: overage ~$19 a month
  at $0.02/credit, roughly **$40–45 to 31 Jan**, on its own AskNews line (Christian accepted the overage, 2026-11-12). The real
  credit cost of one historical search is checked on the AskNews usage page before switch-on; if it differs, only this
  estimate changes. `evals/asknews_budget.py` over-counts (shadow records, B-90).
- **Decision rule** (written into `--rule-json` before the first look; decided by Christian 2026-11-11):
  - **Arm B / E, the resolution-source block: no-harm rule** (protocol §8.3 style, because it adds one checked fact, costs
    about a third of a cent and is switched off in one line). At either look it **goes live** if arm E's mean paired
    difference vs the published live forecast is above 0, the lower end of its 95% cluster-bootstrap CI is above −2
    baseline-score points per question, and its failure rate is not worse. The cheap arm B is reported beside it as supporting
    evidence. Live change = new `CONFIG_VERSION` with Christian's OK; the previous frontier setup then runs on as the shadow
    (roles swapped, same cost) until the final look, and live switches back if the final look puts the block's CI entirely
    below 0.
  - **Arms C and D: full bar.** Early look: adopt only if the **99.5%** CI vs A excludes 0 in the arm's favour. Final look:
    **98.3%** CI. Also needed: no worse failure rate or calibration than A, measured extra cost ≤ $0.10/q. An adopted C or D
    goes to a Tier 2 confirmation as one frontier shadow arm (own card) before any live change.
  - **Kill (C, D), approved by Christian 2026-11-11:** smallest effect of interest **2 baseline-score points per question** (binary: about
    0.014 nats); at the final look an arm whose CI lies entirely below +2 is dropped, not rerun. If "narrow win" and "kill"
    both hold, kill wins. No kill at the early look.
  - **Narrow win or inconclusive at the final look:** keep A, one rerun on questions resolved after it.
  - **If B goes live before the final look:** the cheap arms A, C and D keep reading the research **without** the block, so
    their comparison stays the same to the end (build requirement: the block is a separate, labelled section that the shadow
    runner can leave out).

## Preconditions before switch-on
1. **B-86 merged** (publish the live forecast before the shadows), so the new arms cannot delay a live forecast. Done 2026-11-12 (PR #33).
2. Fetch safety for arm B: only http(s) GET of links that appear in the question text; 20 s timeout; size cap; no cookies or
   credentials; the page text is treated as data, and only the extractor's short block (value, date, quote ≤ 300 characters)
   reaches the members, never the raw page. The fetched text is stored in the shadow record (private data repo) so the
   forecast replays.
3. Arm C model chosen and piloted (done 2026-11-12: gemini-3.8-flash; Christian confirms grounding free-tier coverage in the billing console); AskNews credit budget checked (done 2026-11-11; real historical-search price still to read off the usage page); arms B and D add no Google queries; model IDs checked (done 2026-11-12 on the PC: all 26 checks OK, incl. openai/gpt-5-mini, gemini-3.8-flash with thinking, the 5 frontier models).
4. Offline tests; code version registered; live configs, hashes and `CONFIG_VERSION` unchanged (shadow arms only). Done 2026-11-12 (PR #35: 593 tests, 717/717 live records replay).
5. This card frozen with Christian's choices (done 2026-11-12), then the one-line switch-on in `SHADOW_CONFIGS` (`**EXP013_ARMS`) with Christian's OK.

## Choices
Decided by Christian 2026-11-11: runs beside EXP-008; arm C = stronger Gemini model with Google grounding, cap $0.05/q; no
control arm; frontier arm E from day one; one early look at 150 and a final look at 300 or 2027-01-31; no-harm rule for the
source block; budget as above; all arms start together; kill threshold for C and D = 2
baseline-score points.

Arm C model decided by Christian 2026-11-12: **gemini-3.8-flash**, thinking level "low" (see "Arm C pilot" below).

Arm D decided by Christian 2026-11-12: **full arm D** (keywords + historical search on every question), AskNews overage accepted.
Arm C cap decided by Christian 2026-11-12: **tokens only** (see below).

Still open: nothing on this card. Before switch-on (checks, not design): AskNews historical-search price,
Google billing console (grounding SKU, free tier).

## Arm C pilot (2026-11-12, before freeze; numbers only, no forecasts or scores)
Tool `tools/exp013_armc_pilot.py` (commits 2e92791, 4a63a65). Same 5 live questions per model (46586, 46587, 46582, 46722,
46585; binary ×2, discrete, multiple choice, numeric), the live research prompt plus arm C's strict rules, thinking "low".
Runs armC-pilot-20261112T212008Z and armC-pilot-20261112T221816Z (data repo). Spend $0.42 tokens ($0.88 if every query billed).

| | 3.5 Flash | **3.8 Flash** | 4 Hydrogen | 3.1 Pro |
|---|---|---|---|---|
| Token $/call mean / max | 0.010 / 0.017 | 0.011 / 0.015 | 0.026 / 0.033 | 0.037 / 0.070 |
| Google queries/call | 1.6 | 1.6 | 2.0 | 1.4 |
| Calls with 0 queries | 2 | 1 | 2 | 1 |
| Seconds/call | 7.5 | 12.9 | 14.4 | 27 |
| Fact lines with link / date | 77% / 81% | 70% / 76% | 67% / 86% | 38% / 45% |
| Google redirect links | 0% | 0% | 30% | 21% |

3.8 Flash costed at its 2027 list price ($1.50/$7.50 per M; $0.75/$3.75 until 2026-12-31). Certainty low: n = 5 per model,
cost and rule-following only, not fact accuracy. Queries: arm C adds ~365 a month; the archive shows ~660 grounded queries per
30 days but earlier billing figures were ~3,100–3,800, so the archive under-counts; either way under 5,000 (billing console to
confirm). **Cap rule (decided by Christian 2026-11-12):** the $0.05/q cap counts tokens at list price; grounding queries are recorded per call and counted
against the free tier, not the cap (at $0.014 each, 1 of 5 3.8 Flash calls would pass $0.05). **Build requirement:** record
queries per call and flag links in the brief that are not among the grounding sources (a call with no search may cite links
from memory).

## Results (generated)
## Decision
