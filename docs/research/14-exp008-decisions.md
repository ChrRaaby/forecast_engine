# 14: EXP-008 decisions (F-14): groundwork for Christian

_2026-11-01, Claude (cloud session). This memo collects facts, does the arithmetic and lays out the options for the six decisions
Christian parked in F-14 (`docs/backlog.md`). It decides nothing. The EXP-008 card (`docs/experiments/EXP-008-research-desk.md`)
is still a DRAFT, its **[Christian]** choices are unchanged, and nothing is switched on._

**How to read it.** Page 1 is the summary. Then there is one section per decision, each with the options, the facts and where they
come from, whose evidence it is, what is still unknown, and a **recommendation** (labelled as such). "Ours" means measured on our
bot. "Other teams" means someone else's results. Anything I could not check myself is marked **not re-checked**.

**Numbers.** All costs come from `tools/exp008_cost.py`, a small script that only does arithmetic (no network, no keys). Every
input is a named setting you can change from the command line, for example:

    python tools/exp008_cost.py --questions_per_week 80 --free_tier_unit prompt

Its search counts are checked against the real follow-up code in `tests/test_exp008_cost.py`.

---

## One-page summary

**What EXP-008 is.** In three extra unpublished "shadow" arms, the cheap ensemble forecasts with extra web searches added to the
normal research. In B, a critic lists what the brief is missing. In C, the critic looks for evidence against the brief's leading
conclusion. D is a control that makes generic searches. Arm A is the cheap shadow that already runs. Each search is a Gemini call
with Google Search grounding, and Google charges per search *query* the model runs. On our live research calls the model ran
**1.85 queries per call** (N = 276).

**Facts that drive the decisions:**

1. **Real money and booked money differ.** The code books Google's fee at list price ($0.014 per query) even while the free tier
   (5,000 a month) means we pay $0. So at setting (a) the records show about $34 a month for B+C+D, but the real bill is about
   **$12 a month**: the critic, three extra cheap forecasts and Gemini tokens. Google costs $0 while we stay inside the free tier.
2. **The live bot uses only 8–13% of the free tier** (about 400–650 queries a month). That is an estimate from the 276 measured
   calls; F-02 has to confirm it in the billing console.
3. **(b) and (c) both use up the free tier** at the planned volume (5 searches × 3 arms). At ~50 questions a week it runs out
   around **day 27** of the month, and at ~80 a week around **day 17**. After that every search costs real money (~$0.03),
   including the live bot's own research. Under (c) this is invisible: the records book $0 and the cap never stops anything.
   That is how (c) "quietly becomes (b)". The Google bill then runs **$9 a month (50 q/week) to $56 a month (80 q/week)**.
   Caveat: if the console counts the free tier per grounded *call* rather than per query (Google's pages word it both ways), all
   settings stay inside it.
4. **(a) doesn't test "up to five searches".** The cap allows about **1.25 searches per arm** on average. B, C and D end up
   matched because the cap binds equally on all three.
5. **The analysis will most likely be "inconclusive".** At a switch-on around 15 November, about **100–160** resolved yes/no
   questions (central ~117) arrive by 2027-01-31, so the date binds before N = 150. With these N, a true gain the size of the kill
   threshold (0.015 nats) ends inconclusive about **75–90%** of the time. Only a gain of ~0.05 nats or more is likely to be
   adopted outright. The start date matters more than the 150-or-date rule: each week of delay costs ~13 questions.
6. **Arm D searches more than B and C under (b)/(c).** D always runs 5 searches; B and C run as many as the critic lists (assumed 4,
   since nobody has measured it). Under (a) the cap matches them.
7. **Experiment spend counts against each run's $10 cost cap but not against the $300 season cap.** Under (b) it would block
   the last 2 of a 20-question run; with a frontier roster, 5–10 of 20. Google's fee is outside the OpenRouter key limit, so
   nothing in code stops a Google overrun.

**Decisions and recommendations** (each is a recommendation, not a decision):

| # | Decision | Recommendation |
|---|---|---|
| 1 | Cost setting | First do **F-02** (steps below). Then choose **(e): at most 3 searches per arm, fee counted, cap $0.15**. It isn't on the card: it changes two fields in `EXP008_ARMS`. It costs ~$18/month real and stays inside the free tier at ~50 q/week. It matches D to B/C and keeps the records honest. If F-02 shows less than ~3,600 free queries a month left after live use, choose (a). Not (b). (c) only if F-02 shows the free tier counts calls, not queries. |
| 2 | Roster | **Cheap now** (~$12–20/month real). On the frontier roster B+C+D would cost ~$95–140/month, and the run cap would block live questions in busy runs. For the confirmation, run the winner on the frontier roster as one arm (~$40/month), funded from the $180 frontier experiment budget if you agree. |
| 3 | Analysis point | **Keep "150 or 2027-01-31"** and switch on as early as possible. Expect N ≈ 100–120 and plan for "inconclusive". Moving the date to reach 150 changes the odds only a little. |
| 4 | Kill threshold | **Keep 0.015 nats** (same as EXP-005) but fix two wordings: when "narrow win" and "kill" overlap, kill wins (as in EXP-005), and the $0.20/q cost kill uses cost at list price. Note that at these N the kill only fires if an arm looks no better than zero. |
| 5 | Season-cap counting | **A separate EXP-008 line in `budget-ledger.md`** (e.g. $50 from the $100 PoC), not inside the $300, plus a Google Cloud budget alert. A code cap only if (b)/(c) is chosen. |
| 6 | D vs B/C searches | With (e) or (a): no code needed (with (e), matched while the critic lists ≥ 3 gaps; report searches/q per arm). With (b)/(c): a "D asks the critic how many" control (M3 below). It needs a new field in `FollowupConfig`, so it is described here and **not built**. |

---

## Decision 1: cost setting (a) / (b) / (c)

**What it decides.** How many follow-up searches each arm may make, and how Google's grounding fee is booked against the
per-question cap (`FollowupConfig.max_cost_usd_per_q`, `forecast_engine/config.py:45`).

**This can't be settled until F-02 is done.** The choice depends on how much of Google's free tier the project already uses and
whether the tier counts queries or calls. Only the billing console shows either.

### Options

| Option | What changes in `EXP008_ARMS` | Searches per arm (B / C / D) | On the card? |
|---|---|---|---|
| (a) cap $0.06, fee counted | nothing (today's default) | 1.25 / 1.25 / 1.25 | yes (default) |
| (b) cap $0.20, fee counted | `max_cost_usd_per_q=0.20` | 4 / 4 / 5 | yes |
| (c) fee booked at $0, cap $0.06 | `grounding_usd_per_query=0.0` | 4 / 4 / 5 | yes |
| (d) fee booked at $0, at most 3 searches | `grounding_usd_per_query=0.0, max_gaps=3` | 3 / 3 / 3 | **no**, added here |
| (e) fee counted, cap $0.15, at most 3 searches | `max_cost_usd_per_q=0.15, max_gaps=3` | 3 / 3 / 3 | **no**, added here |

"4 / 4" assumes the critic lists 4 gaps per question. That has never been measured (Unknowns). With 5 gaps, B/C make 5.

### Cost per question, per arm (cheap roster, from `tools/exp008_cost.py`, default inputs)

"Booked" is what the record and the cap count. "Real" is what we actually pay.

| Setting | Booked $/q B / C / D | Real $/q inside the free tier | Real $/q once the free tier is used up |
|---|---|---|---|
| (a) | $0.053 / $0.053 / $0.048 | $0.021 / $0.021 / $0.016 | $0.053 / $0.053 / $0.048 |
| (b) | $0.137 / $0.137 / $0.163 | $0.034 / $0.034 / $0.034 | $0.137 / $0.137 / $0.163 |
| (c) | $0.034 / $0.034 / $0.034 | $0.034 / $0.034 / $0.034 | $0.137 / $0.137 / $0.164 |
| (d) | $0.029 / $0.029 / $0.025 | $0.029 / $0.029 / $0.025 | $0.107 / $0.107 / $0.102 |
| (e) | $0.107 / $0.107 / $0.102 | $0.029 / $0.029 / $0.025 | $0.107 / $0.107 / $0.102 |

These include the forecast itself on the longer brief (~$0.01 plus ~$0.001 per search). The card's estimates (~$0.05, ~$0.18,
~$0.04) assumed 1, 5 and 5 searches; the script uses the cap rule and 4 gaps instead.

### Per week and month for B + C + D, and Google's free tier

At **~50 questions a week** (planning volume; see "Inputs"), free tier counted per **query**:

| Setting | Booked $/week | Booked $/month | Google queries/month: live + experiment | Free tier used (live alone) | Free tier runs out | Google bill/month | **Real $/month** | Questions per run before the $10 run cap |
|---|---|---|---|---|---|---|---|---|
| (a) | $7.71 | $33.51 | 402 + 1,506 | 38% (8%) | lasts | $0 | **$12.42** | 20 of 20 |
| (b) | $21.89 | $95.19 | 402 + 5,217 | 112% (8%) | day 27 | $8.67 | **$30.81** | 18 of 20 |
| (c) | $5.10 | $22.16 | 402 + 5,223 | 112% (8%) | day 27 | $8.74 | **$30.90** | 20 of 20 |
| (d) | $4.13 | $17.95 | 402 + 3,616 | 80% (8%) | lasts | $0 | **$17.95** | 20 of 20 |
| (e) | $15.77 | $68.57 | 402 + 3,616 | 80% (8%) | lasts | $0 | **$17.95** | 20 of 20 |

At **~80 questions a week** (the measured upper end), per **query**:

| Setting | Booked $/month | Google queries/month: live + experiment | Free tier used (live alone) | Runs out | Google bill/month | **Real $/month** |
|---|---|---|---|---|---|---|
| (a) | $53.61 | 643 + 2,409 | 61% (13%) | lasts | $0 | **$19.88** |
| (b) | $152.30 | 643 + 8,348 | 180% (13%) | day 17 | $55.87 | **$91.30** |
| (c) | $35.45 | 643 + 8,356 | 180% (13%) | day 17 | $55.98 | **$91.44** |
| (d) | $28.72 | 643 + 5,785 | 129% (13%) | day 24 | $19.99 | **$48.71** |
| (e) | $109.71 | 643 + 5,785 | 129% (13%) | day 24 | $19.99 | **$48.71** |

If the free tier counts **grounded calls** instead of queries (`--free_tier_unit prompt`), nothing runs out at either volume.
(b) and (c) at 80 q/week come closest, at 97%. "Real $/month" counts only the part of the Google bill the experiment causes.
Over the ~2.5 months to 2027-01-31, (a) costs ~$31 real, (e) ~$45, and (b)/(c) ~$77 at 50 q/week or ~$230 at 80 q/week.

**The key check for (c).** The live bot's research uses about 8–13% of the free tier (row "live alone"). (c) adds about 5,200
queries a month at 50 q/week, which takes the project to 112%. The tier then runs out around day 27 (day 17 at 80 q/week). From
then on, every search costs ~$0.03 real, as under (b). The records still show $0 for the fee, the per-question cap never binds
(fee $0 means a search costs only ~$0.004), and the OpenRouter key limit doesn't cover Google. So the only signal is Google's bill.

**Worked example, (c) at 50 questions a week.** In a 30-day month, the live bot runs ~220 research calls (~400 queries). The three
arms run ~220 × 13 searches × 1.85 ≈ 5,200 queries. The 5,000 free queries are used up around day 27. The remaining ~600 queries
cost 600 × $0.014 ≈ $8.70. The records show $0 of it.

### Inputs and where they come from

| Input | Value used | Source | Whose / how strong |
|---|---|---|---|
| Queries per grounded Gemini call | mean 1.85, median 2, max 3 (0: 2, 1: 55, 2: 202, 3: 17), N = 276 | PR #13 comment, 2026-10-26 ([link](https://github.com/ChrRaaby/forecast_engine/pull/13#issuecomment-6428318386)); `docs/log/2026-10-26.md` | Ours, measured on live research calls. Follow-up prompts are narrower and may run fewer queries (the comment's own caveat). With 1 query per search, (a) allows 3 searches and every column changes (`--followup_queries_dist 1:1`). |
| Grounding fee | $14 per 1,000 search queries | `config.py:46-49` (pricing page as read on 2026-10-26) | Google's list price. **Not re-checked**: `ai.google.dev` is blocked from this cloud session (2026-11-01). |
| Free tier | 5,000 a month, shared across Gemini 3 models | as above | **Not re-checked** (wording below) |
| Gemini tokens per search | ~300 in, ~1,500 out at $0.30 / $2.50 per M | card's estimate; prices `config.py:69-70` | Estimate |
| Critic | ~$0.0045 per call (gpt-5-mini, ~4–8k in, ~1–2k out) | card: ~$0.003–0.006 | Estimate |
| Gaps listed per question | 4 (capped at 5) | none: no measurement exists | **Assumption** |
| Forecast per arm (cheap) | ~$0.010 + ~$0.001 per search | ADR-0009, ADR-0010 ("~$0.01 for the cheap shadow") | Ours, measured |
| Questions per week | 50 (planning) / 80 (upper end) | 50: ADR-0010's "~800 questions per season" as the card spreads it. 80: 276 live Gemini research calls in the 24 days 2026-10-03..26 (≈ 11.5/day). That count includes wide-mode questions until 2026-10-08 and retried questions, so it's an upper end for tournament questions. | Ours. No newer weekly count is in the repo; the monitor (`tools/build_monitor.py`, PC) has the exact number. |
| Live research use of the tier | 1 Gemini call per live question × 1.85 queries | `research.py:105-117` (one grounded call per question) | Ours |
| Other grounded use on the project | 0 | unknown until F-02 | **Unknown** |

**Google's wording (not re-checked verbatim).** I couldn't open the pricing page. A web search on 2026-11-01 gave summaries of it.
The [Gemini API pricing page](https://ai.google.dev/gemini-api/docs/pricing) was summarised as *"5,000 prompts per month (free,
shared across Gemini 3), then $14 / 1,000 search queries"*. Google Cloud's page was summarised as *"5,000 search queries per
month at no charge, aggregated across all Gemini 3 models … $14 per 1,000 search queries"*. So the free tier is worded as
"prompts" in one place and "search queries" in the other, which is why the script has `free_tier_unit`. A [developer-forum
thread](https://discuss.ai.google.dev/t/grounding-with-google-search-5-000-free-prompts-month-is-the-quota-scoped-per-project-or-per-billing-account/134866)
asks whether the 5,000 are per project or per billing account; I found no answer. Older Gemini 2.x models were billed per grounded
prompt at different rates. I also assume `gemini-3.5-flash-lite` counts as a "Gemini 3" model.

### F-02: finding the actual grounding charges and free-tier use (step by step)

_From Google's public documentation as I know it. **Not re-checked in Christian's console**, and menu names may differ slightly._

1. **Find the project behind the bot's key.** Open [Google AI Studio](https://aistudio.google.com) with the account that created
   `GEMINI_API_KEY`. Go to **Get API key** (or **API keys**). Each key is listed with its Google Cloud **project name and number**.
   Note them.
2. **See the billing account.** In AI Studio, open **Usage & Billing** (or **Billing**) for that project. It shows whether the
   project is on the paid tier and which billing account pays. Or open
   [console.cloud.google.com/billing](https://console.cloud.google.com/billing) and pick the account linked to the project.
3. **Open the cost report.** Go to Billing → **Reports**. Set the time range to **October 2026** (and then "this month"). Filter
   **Projects** = the bot's project and **Services** = *Gemini API* (it may be listed as *Generative Language API*).
4. **Group by SKU.** Set **Group by → SKU**. Look for SKUs that mention **"Grounding"** or **"Google Search"**. For each, note the
   **usage amount and its unit** (search queries? requests/prompts?), the **cost**, and the **credits / discounts** column. Usage
   with a $0 cost means the free tier covered it.
5. **Read the unit.** If usage is counted in queries and October shows roughly 500–700, the free tier counts queries
   (`--free_tier_unit query`). If it shows roughly 300–350 (one per research call), it counts calls (`--free_tier_unit prompt`).
6. **Compare with what the bot did.** Expect about 20 queries a day (≈ 11 research calls × 1.85). Much more than that means
   something else uses the same project or billing account; enter it as `--other_queries_per_month`.
7. **Check whether other projects share the allowance.** In Reports, group by **Project** for the whole billing account. If other
   projects also show grounding use, the 5,000 may be shared (open question above).
8. **Optional: quotas.** Console → **APIs & Services → Generative Language API → Quotas & system limits**. Search for
   "grounding" or "search" to see any per-day or per-month limit and current use.
9. **Set an alert.** Billing → **Budgets & alerts** → create a budget scoped to the bot's project (e.g. $5 a month) with alerts at
   50 / 90 / 100%. Per Google's docs a budget only sends email alerts; it doesn't stop spending.
10. **Write down** the SKU name(s), the unit, October's usage and cost, and whether the allowance is per project, in F-02 in
    `docs/backlog.md`. Then rerun `python tools/exp008_cost.py` with those numbers.

### Unknowns

- How many queries a narrow follow-up search runs. The first follow-up records will show it.
- How many gaps the critic lists.
- The free tier's unit and scope, and any other use on the project (F-02).
- The exact live volume: the monitor has it.

### Recommendation

**Recommendation:** do F-02 first. Then:

- If the console shows the free tier counts **queries**, and live plus other use leaves **≥ ~3,600 free queries a month**,
  choose **(e)**: at most 3 searches per arm, fee counted at list price, cap $0.15.
  - (e) costs about $18 a month real at ~50 q/week and stays inside the tier. At ~80 q/week it overruns around day 24 (~$20).
  - It tests "a few targeted searches", not one.
  - It matches arm D to B/C (Decision 6) without code.
  - The records keep showing the full list-price cost. The cap of $0.15 always fits 3 searches, even at 3 queries each
    ($0.142), so it acts only as a safety rail.
- If less than ~3,600 free queries are left, choose **(a)**. It is cheapest and safe at any volume, but tests ~1 search.
- **(b)** costs ~3× (e) and has the same overrun risk; I see no reason to prefer it.
- **(c)** is only sensible if F-02 shows the tier counts **calls**. Even then I prefer (e), which keeps the records honest.

---

## Decision 2: roster (cheap now, frontier for the confirmation?)

### Options

| Option | What runs | $/q per arm, real, inside the free tier | B+C+D real $/month (50 q/week) | Run cap |
|---|---|---|---|---|
| Cheap now (card's proposal) | arms on the cheap ensemble, paired with the cheap shadow A | (a) ~$0.02 · (e) ~$0.03 | (a) $12 · (e) $18 | 20 of 20 questions per run |
| Frontier now | arms on the five frontier members, paired with the live forecast | (a) ~$0.15 · (e) ~$0.18 | (a) **$96** · (e) **$117** | (a) 15 · (e) 11 of 20 |
| Cheap now, winner confirmed on frontier | one frontier arm after a win, paired with live | (e) ~$0.18 | ~$39 for one arm | 20 of 20 |

Frontier figures come from `tools/exp008_cost.py --forecast_usd_base 0.12 --forecast_usd_per_search 0.015`. They use ADR-0010's
measured $0.10–0.14/q for the five members (4 replayed questions, ours) plus ~1.5k extra input tokens per search per member at
$2/M (list prices in `config.py`). On the frontier roster arm A costs nothing extra, because it is the live forecast itself.

### Facts

| Fact | Source | Whose / strength |
|---|---|---|
| Frontier ensemble costs $0.10–0.14/q; cheap shadow ~$0.01/q | ADR-0010 "Measured cost" | Ours, 4 questions |
| Research experiments may run on cheap models; method experiments run on frontier models | ADR-0009 amendment 2026-10-07 | Christian's decision (judgement, no data) |
| "A method that helps a cheap model may not help a strong one" | ADR-0009 amendment; EXP-005 card line 14 | Judgement, no data |
| We assume scaffolding effects transfer to newer models; B-34 checks it | `docs/evaluation-protocol.md:20-21`; B-34 (Todo) | Assumption, untested |
| "Automated prompt engineering gains did not replicate on strong models" | `docs/research/02-llm-forecasting-literature.md:34` | Other teams, from a secondary summary marked "to be verified against the primary sources"; about prompts, not research |
| Removing search degraded Brier 3.6× (agentic search matters) | playbook tracker R-03 | Other teams, rated Strong; doesn't say whether this differs between cheap and strong models |
| Frontier experiment budget: $180, $177.36 left on 2026-10-08 | ADR-0009 amendment; `docs/experiments/budget-ledger.md` | Ours; not re-checked since |

**Does a research effect on cheap models carry over to frontier models?** Nothing in the repo or its literature notes measures
this for research changes. The only related item is about prompts (above). It could go either way: stronger models may already
know more (less use for extra facts) or use the facts better. I did no new literature search in this session.

### Unknowns
- Whether a cheap-roster effect carries over.
- The cheap roster's per-question SD (F-13), which sets how many questions are needed.

### Recommendation
**Recommendation: cheap now.** Frontier B+C+D costs ~$95–140 a month real (≈ $240–350 to 2027-01-31). That is more than the
whole $100 PoC. In busy runs the run cap would also block 5–10 live questions per 20-question run (Decision 5). If an arm wins,
confirm it on the frontier roster as **one** arm paired with the live forecast (~$40 a month). The ADR-0009 amendment's $180
frontier experiment budget is a natural source, if you agree; today it is earmarked for method experiments like EXP-005.

---

## Decision 3: analysis point (150 resolved yes/no questions or 2027-01-31)

### When would 150 arrive?

| Fact | Source | Whose |
|---|---|---|
| 39 resolved yes/no live records in the first month (resolved 2026-10-06..21); 70 resolved questions of all types | `docs/log/2026-10-26.md:23, 27` | Ours |
| 35 of those yes/no were MiniBench | backlog B-48 | Ours |
| MiniBench resolves in ~2 weeks | `docs/ideas.md` I-14 | Ours (rough) |
| The first two weeks of Fall were deliberately slow | `docs/log/2026-10-03.md:6-7` | Metaculus announcement |

So the rate of resolved yes/no questions is roughly **11–18 a week**: 39 over the 3.6 weeks since 2026-10-01, or over the 2.1 weeks
in which they resolved. Questions forecast after switch-on start resolving about **2 weeks** later. Assuming switch-on on
**2026-11-15** (an assumption):

| Resolved yes/no per week | N by 2027-01-31 | Date N reaches 150 |
|---|---|---|
| 11 | ~99 | 2027-03-04 |
| 13 (central) | ~117 | 2027-02-18 |
| 18 | ~162 | 2027-01-26 |

With switch-on on 2026-12-01 instead: ~74 / ~87 / ~121 by 2027-01-31. **The date will most likely bind first, with N around
100–120.** The card reports N < 100 as a screen. The questions that resolve by January are mostly short MiniBench questions, so
the result mostly describes those.

### How big a gain can that detect?

The formula is the protocol's (§5): MDE ≈ (z for the CI level + z for 80% power) × SD / √N. With m = 2 candidate arms (B, C;
D doesn't count) the card uses a 97.5% CI, so the factor is **3.08** (2.80 for one arm). No log-score SD has been measured yet
(F-13 will measure it), so the SDs below are **assumptions**:
- 0.12 nats: the protocol's illustrative Brier SD of 0.06, doubled. Near 50%, log-score differences are about twice Brier
  differences.
- 0.20 nats: a pessimistic case, because research changes move a few questions a lot (card).

| SD (assumption) | MDE at N = 100 | N = 150 | N = 250 |
|---|---|---|---|
| 0.12 nats | 0.037 | 0.030 | 0.023 |
| 0.20 nats | 0.062 | 0.050 | 0.039 |

For reference, the card's Brier numbers (SD 0.06: 0.019 at N = 100, 0.015 at N = 150) come out of the same formula; a test checks
this.

### How likely is "inconclusive"?

Chance of each outcome of the card's rule for arm B or C vs A, at N = 120. This uses a normal approximation and ignores question
clusters and the second test vs D, both of which make it worse.

| Assumed true gain | SD 0.12: adopt / narrow / kill / inconclusive | SD 0.20: adopt / narrow / kill / inconclusive |
|---|---|---|
| 0 (does nothing) | 0% / 1% / 19% / **80%** | 0% / 1% / 8% / **91%** |
| +0.015 (the kill threshold) | 1% / 18% / 1% / **80%** | 1% / 7% / 1% / **91%** |
| +0.03 | 19% / 50% / 0% / 31% | 8% / 20% / 0% / 72% |
| +0.05 | 83% / 16% / 0% / 1% | 37% / 32% / 0% / 31% |

At N = 150 and SD 0.12, the "inconclusive" chances for 0 and +0.015 drop only to ~75%.

### Options

| Option | What it changes |
|---|---|
| Keep "150 or 2027-01-31" (card) | Answer by end of January. N ≈ 100–120, probably inconclusive; a rerun on fresh questions would follow. |
| 150 only, no date | Waits until ~mid-February to March; the odds above improve a little. |
| Later date (e.g. 2027-02-28) | +~50 questions; same small improvement. |

### Recommendation
**Recommendation:** keep the card's rule, switch on as early as possible, and write down now that "inconclusive" is the expected
result unless the gain is large (≥ ~0.04 nats). Waiting longer for N = 150 buys little. The start date matters more: every week
of delay costs ~13 questions.

---

## Decision 4: kill threshold (0.015 nats)

**In plain words.** The log score is the natural log of the probability you gave to what happened. 0.015 nats per question is
about the gain from moving a forecast from **69% to 70% on a question that resolved YES** (ln 0.70 − ln 0.69 = 0.0144). It is
about **2.2 Metaculus baseline points per question** (100 × 0.015 / ln 2). An arm whose whole CI lies below +0.015 is dropped
("real, but too small to be worth it").

**Where it came from.** It was agreed with Christian for EXP-005 on 2026-10-08 (F-12 in `docs/backlog.md`). It replaced "MDE/2",
because the screen's noise would have overstated the MDE. The EXP-005 card (line 125) calls it "the smallest effect worth the
protocol's extra calls and complexity". The protocol (§5) asks for a fixed value judgement written before the run.

### Does it make sense for a research change that adds cost?

| Point | Fact / arithmetic |
|---|---|
| What the change would cost if adopted on the live frontier bot | (e): ~$0.02/q for critic + search tokens inside the free tier, plus ~$0.045/q of extra frontier input, so ~$0.06/q (~$50 over 800 questions). Once the free tier is used up, add ~$0.08/q of grounding fee: ~$0.14/q (~$110). |
| What 0.015 buys | ~2 baseline points per question |
| How the threshold interacts with power | At N ≈ 120 and SD 0.12 the 97.5% CI is about ±0.025 wide, wider than the threshold itself. The kill only fires if the observed gain is ≤ about −0.01, i.e. the arm looks no better than zero. An outright adopt needs an observed gain of ≥ ~0.04, because a CI whose lower end is below 0.015 is a "narrow win" on the card. |
| Overlap in the card's wording | "Narrow win" (CI above 0, lower end below 0.015) and "kill" (CI entirely below 0.015) overlap when the CI lies between 0 and 0.015. The card doesn't say which wins; EXP-005 says kill. At these N the CI is wider than 0.015, so this can't happen in practice, but the rule text should say it. |
| Cost kill ("follow-up cost > $0.20/q") | Under (c)/(d) the records book the fee at $0, so the booked cost would understate. The records store the query counts, so cost at list price can be computed. |

### Options

| Option | Effect |
|---|---|
| Keep 0.015 | Same yardstick as EXP-005. In practice it drops only arms that look useless. |
| Raise to ~0.03 | Closer to what N ≈ 120 can see; would also drop small real gains (e.g. a true +0.015 arm). |
| Lower to 0 | Kill only on clear harm. Almost never fires; most results end in a rerun. |

### Recommendation
**Recommendation:** keep **0.015 nats** for comparability with EXP-005. Add two clarifications before the card is frozen:
(i) if "narrow win" and "kill" both hold, **kill** wins (as in EXP-005); (ii) the $0.20/q cost kill uses **cost at list price**
(tokens + queries × $0.014), whatever is booked. Value is not the problem with 0.015; power is. Say so in the result.

---

## Decision 5: does experiment spend count against the $300 season cap?

### What counts today (code as of `main` f3817c1)

| Spend | Run cost cap ($10 tournament, `config.py:149`; $2 otherwise) | $300 season cap (`config.py:128`) |
|---|---|---|
| Live frontier forecast incl. research tokens, also failed ones | **yes**: `_charge` adds to `spent_usd` (`main.py:265-266`, called at 209 and 215) | **yes**, only when the live config is the frontier one: `main.py:267-268` |
| Cheap shadow (arm A) and EXP-008 arms, incl. follow-up critic + searches | **yes**: added straight to `spent_usd` in `_run_shadows` (`main.py:287, 289, 292`) | **no**: shadows never call `season.add` (`spend.py:5`: "Shadow forecasts (B-62) don't count") |
| Google grounding fee of the live research call | **no**: not booked anywhere (`config.py:67-68`) | no |
| Google grounding fee of follow-up searches | booked at list price, or $0 under (c)/(d) (`research_followup.py:244`), so it counts to the run cap as booked | no |

**How the run cap works.** It is checked only **before starting** each question (`main.py:200-201`). Up to 3 questions run at once
(`main.py:179`), so it can overshoot by about one batch. A skipped question isn't counted as a failure, so the next run (~10
minutes later) picks it up. It is delayed, not lost, unless it closes in between.

**The season total.** It lives in `state/season_spend.json` (`spend.py:18`) via the Actions cache. If the cache is lost it restarts
at $0.

**The $100 PoC budget is not enforced in code.** The OpenRouter key limit ($20, F-10) stops OpenRouter calls only. **Google bills
separately, so nothing in code stops a Google overrun.**

### Options

| Option | What changes | Code? | Consequence |
|---|---|---|---|
| Keep as is | Nothing. Experiment spend counts to the run cap only and is tracked against the $100 PoC by hand. | none | No running total for EXP-008. A busy run under (b) starts 18 of 20 questions (frontier roster: 10–15). |
| Count inside the $300 | `_run_shadows` also adds the arms' cost to the season total | `main.py` (live-path code; separate PR) | The experiment competes with tournament forecasting. If the $300 runs out, live falls back to the cheap config. Booked cost overstates real cost by ~3–4× inside the free tier, so (a) ≈ $34 a month booked would use the cap far faster than real money. ADR-0009 deliberately kept the budgets apart. |
| Separate experiment line in `docs/experiments/budget-ledger.md` | A row "EXP-008 (cheap roster, from the $100 PoC): budget $X" with monthly rows of real spend taken from the monitor | none (by hand) | Visible and consistent with how the $180 frontier budget is tracked; not enforced. |
| Separate line + code cap | A small state file like `season_spend.json` for experiment spend; arms are skipped once it's used up | `main.py` + `spend.py` (separate PR, with tests) | Enforced, but more code on the live path. Only worth it under (b)/(c). |

Independent of the choice: the experiment raises **OpenRouter** spend by ~$9–10 a month (critic + three extra cheap forecasts),
which brings F-10 (raise the key limit) forward.

### Recommendation
**Recommendation:** a **separate EXP-008 line in the budget ledger**, not inside the $300. Fund it from the $100 PoC: e.g. $50 covers (e) at ~50 q/week to
2027-01-31 (~$45 real). At ~80 q/week it would need ~$120, so check the real volume first. Record monthly real spend: OpenRouter from the monitor, Google from the billing
report. Add the **Google Cloud budget alert** (F-02 step 9) as the only stop on Google spend. Build a code cap only if (b) or (c)
is chosen. Leave the run-cap counting as it is: at the cheap roster under (a)/(e) all 20 questions of a run still fit.

---

## Decision 6: control arm D vs B/C search count

**What happens today.** Arm D always builds `max_gaps` generic queries (`research_followup.py:165-166`). B and C search once per gap
the critic lists, at most `max_gaps` (`research_followup.py:67`: "List at most {max_gaps} items"). The per-question cap is the only
other limit (`research_followup.py:190-191`).

**Worked example.** Setting (c), `max_gaps` = 5, fee booked at $0, so the cap never binds:

| Question | Critic lists | B searches | D searches |
|---|---|---|---|
| 1: brief already thorough | 2 gaps | 2 | 5 |
| 2: brief one-sided | 5 gaps | 5 | 5 |
| 3: typical | 4 gaps | 4 | 5 |
| **Average** | | **3.7** | **5** |

D searches ~35% more than B here. Under (a) the cap stops every arm after about 1–3 searches whatever the critic lists (1.25 on
average), so they match.

**Why it biases the comparison.** D exists to answer "does B win only because it searched more?" (protocol §5: a winner must beat
A **and** D). If D searches more, the B-vs-D test no longer compares targeted with generic searching at equal effort:
- If extra searches help, D gets a head start, and B can fail to beat D even when targeting helps.
- If extra generic text hurts (longer, repetitive briefs), D is handicapped, and B can beat D for the wrong reason.

The direction is unknown. Either way the control no longer controls for what it should. The mismatch is also not random:
B searches *less* exactly where the brief is already complete.

### Ways to match them

| Option | How | Matches | Trade-offs | Where the code would change |
|---|---|---|---|---|
| M0 Let the cap bind | Use setting (a) | On average (1.25 each) | Tests ~1 search only | none |
| M1 Copy B's count | D runs as many searches as B made on the same question | Exactly, per question, for B | C may differ (which arm does D copy?). D depends on B's record, so if B fails, D has no count. Couples the arms. | `main.py _run_shadows` (pass B's count to D) |
| M2 Fixed count | Every arm makes exactly k searches; the critic is asked for *exactly* k | Exactly for B, C and D | The critic must pad when fewer gaps matter, which dilutes B/C a little. Cost and free-tier use become fully predictable. | critic prompt + a new `FollowupConfig` field (`config.py`) |
| M2′ At most k = 3 for all | `max_gaps=3` in `EXP008_ARMS` (options (d)/(e)) | Matched whenever the critic lists ≥ 3 gaps | Mismatch stays when it lists 1–2. Needs only `EXP008_ARMS`. | `EXP008_ARMS` only |
| M3 D asks the critic how many | D makes the same critic call as B, throws the gap text away, and runs that many generic searches | B's count per question in distribution (same critic, same brief), and the critic call itself, so calls/q match too | +1 critic call for D (~$0.0045/q). C's count may differ. Two critic calls can disagree a little on the same brief. | a new `FollowupConfig` field + ~10 lines in `research_followup.py` |
| M4 Match the arm average | D's `max_gaps` = B/C's average count | On average only | The average is known only after data arrive, and changing D mid-run splits it into two eras. Per-question mismatch remains. | `EXP008_ARMS` only (D's `max_gaps`) |

**Why I built nothing.** The task allowed a switched-off matching option only if it fits entirely in `research_followup.py` and
`EXP008_ARMS`. The best options need more:
- M3 and M2 need a new field in `FollowupConfig` (`config.py`). That also changes the three EXP-008 arms' config hashes; harmless,
  since they have never run, but outside the allowed scope.
- M1 needs `main.py`.

M2′ and M4 need no new code: they are values in `EXP008_ARMS`, and choosing them is your decision (Decision 1), so I left
`EXP008_ARMS` unchanged.

A sketch of M3 for a later change: add a field to `FollowupConfig`, e.g. `control_count_from: str | None = None` (None = today's
behaviour). In `_run`, when `critic_mode == "control"` and the field is set, first make the critic call in that mode (recorded like
any critic call) and use `len(gaps)` instead of `max_gaps` for the number of generic queries.

### Recommendation
**Recommendation:**
- With **(e)** (or (d)): use M2′ as it comes, and report searches/q per arm (already a secondary metric on the card). If the critic
  often lists fewer than 3 gaps, switch to M3 in a later card.
- With **(a)**: nothing to do (M0).
- With **(b)** or **(c)**: build **M3** first, as a small switched-off change with tests, before switch-on.

---

## Sources

- Repo files are cited inline (file:line, as of `main` f3817c1, 2026-11-01).
- PR #13 description and measurement comment: <https://github.com/ChrRaaby/forecast_engine/pull/13> (read 2026-11-01).
- Gemini API pricing: <https://ai.google.dev/gemini-api/docs/pricing>. **Not re-checked**: blocked from this session on 2026-11-01;
  wording taken from search-result summaries that day.
- Forum question on free-tier scope (per project or per billing account, unanswered):
  <https://discuss.ai.google.dev/t/grounding-with-google-search-5-000-free-prompts-month-is-the-quota-scoped-per-project-or-per-billing-account/134866>
  (2026-11-01).
- Google Cloud Billing reports and budgets, used for the F-02 steps from general knowledge of the console. **Not re-checked.**
