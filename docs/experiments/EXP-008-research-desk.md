# EXP-008: Research desk (gap-fill), card v2

> **DRAFT, not pre-registered: needs Christian's OK.** Nothing runs until Christian approves this card and the one-line switch
> below. Items marked **[Christian]** are open choices; the card is only final once each one has an answer.

_Card written BEFORE running. Results section filled by the report generator. Backlog B-64; tooling B-17 (built as switched-off
shadow arms, `forecast_engine/research_followup.py`)._

- **Date / author:** v1 2026-10-07, Claude (I-19; research/12). v2 2026-10-26, Claude (cloud), rewritten for the protocol change of
  2026-10-19 (several arms, compute-matched control, tiers, rerun rule). Changes from v1 at the bottom.
- **Hypothesis:** our worst misses come from all members sharing one incomplete briefing (nostreambot's finding, R-03, R-06; other
  teams' evidence, not ours). A critic that lists what the brief is missing, followed by one targeted search per gap, gives the members
  a more complete brief and improves accuracy (arm B). A critic aimed at evidence *against* the brief's leading conclusion helps
  most on one-sided briefs (arm C). Neither has been tested on our bot.
- **Change vs. baseline:** members, forecasting prompt and aggregation are unchanged; all arms forecast on the live question's
  research (AskNews latest + Gemini briefing, captured once). Only a follow-up section added after it differs:

  | Arm | Shadow name | What runs on top of the live research | Config hash |
  |---|---|---|---|
  | A baseline | `cheap` (already running, B-62) | nothing | `c18ebdd3d1ef` (= `DEFAULT_CONFIG`) |
  | B gaps | `exp008-b-gaps` | critic lists ≤ 5 gaps (missing facts, stakeholders' views, base-rate data, contrary evidence) → one Gemini grounded search per gap | see `EXP008_ARMS` |
  | C against | `exp008-c-against` | critic names the brief's leading conclusion and lists ≤ 5 searches for evidence against it → one search each | see `EXP008_ARMS` |
  | D control | `exp008-d-control` | no critic; the same number of searches with generic, question-independent instructions (the standard research request in other words) | see `EXP008_ARMS` |

  All three new arms: `FollowupConfig` in `forecast_engine/config.py` (critic `openai/gpt-5-mini`, reasoning low; searches with the
  live Gemini model; 1 round; `max_gaps` 5; hard cap `max_cost_usd_per_q` $0.06 per question including Google's grounding fee at
  list price, see Cost). The section is labelled identically in B, C and D ("Additional research"; each entry names what was
  searched for), so arms differ only in what was searched. No AskNews credits are used.
- **Roster:** the cheap M1 ensemble (GPT-6 Luna, Gemini 3.5 Flash-Lite, DeepSeek V4.1 Flash), the same as the existing cheap
  shadow, so arm A costs nothing extra and every arm is paired on the same question and research. ADR-0009's amendment allows
  research experiments on cheap models. **[Christian]** A research gain on cheap members may be smaller on the frontier roster
  (strong models may find less use for extra facts, or more); running the arms on the frontier roster would cost ~$0.12/q per arm
  more (ADR-0010 measured cost). Recommendation: cheap now; the Tier 2 confirmation (below) runs the winner on the frontier roster.
- **Question set:** every FutureEval and MiniBench question the live bot forecasts after the switch-on date, as unpublished shadow
  records (`data/forecasts/<run>-shadow-exp008-*`). New searches can't be made as of a past date, so this can't be a backtest or a
  replay. All types are forecast; the primary analysis is binary only. A question enters the analysis only if all four arms have a
  record for it (pairing; a failed arm on a question is counted as a failure, reported per arm). Clusters (`cluster_id` in the report
  table): one per Metaculus post, so the sub-questions of a question group form one cluster.
- **Evaluation model(s) + release dates:** the cheap live roster at forecast time; no leakage question arises (everything runs live).
- **Primary metric:** binary log score (Metaculus baseline score), paired per question vs. arm A. Secondary (descriptive only):
  Brier, calibration; searches/q, critic calls/q, sections added, share of questions where the cap stopped searching; $/q per arm;
  member spread (B-47) when available; whether members cite the follow-up section (B-46 trace) when available.
- **Arms:** m = 2 candidate arms (B, C); D is the compute-matched control and doesn't count towards m. Each candidate is judged at
  CI level 1 − 0.05/2 = **97.5%** (protocol §5). Calls/q vs. A: B and C +1 critic call + up to 5 searches; D + up to 5 searches;
  at the default cap all three make about 1 search (Cost). A candidate is adopted only if it beats **both** A and D.
  - **Arm E (B-17 multi-round, critic sees the enlarged brief and searches again) is not on the card.** The only prior is that
    nostreambot uses two gap-fill passes (research/12), with no measured effect of the second pass; a third candidate would raise
    every arm's bar (m = 3 → 98.3% CI, ~+5% MDE over m = 2), and at the $0.06 cap a second round can't run anyway. If B wins, a
    later card can test `rounds=2` against B. The code supports it (`FollowupConfig(rounds=2)`).
- **Expected effect & MDE:** priors: B small gain; C possibly larger on one-sided briefs; D ≈ A (extra generic searches mostly
  repeat the briefing). MDE ≈ 3.1 × SD(diff) / √N at the 97.5% level with 80% power. With the protocol's illustrative SD of 0.06
  (Brier) that is ≈ 0.015 at N = 150 and ≈ 0.019 at N = 100; the log-score SD is measured on the first analysis and the MDE
  restated. Research changes may have a larger SD than prompt changes (different facts move members a lot on a few questions).
- **N and timing:** **[Christian]** proposed single analysis when **150 resolved binary questions** have records in all four
  arms, or on **2027-01-31**, whichever comes first; no looks at scores before that (the report generator is not run on these arms
  earlier). Questions resolve slowly (39 resolved binary live records in the whole first month, 2026-10-26), so this likely takes
  the rest of the season; if N < 100 at the date, the result is reported as a screen (inconclusive unless the CI already excludes 0).
- **Tier (protocol §6.2):** **Tier 2, confirm before live**: it adds calls and cost per question and changes what the members read.
- **Rerun:** first run of EXP-008 (v1 never ran). Narrow win or inconclusive → keep A; **one** rerun card written before it runs,
  same arms and rule, using only questions whose actual resolve time is after the first analysis date
  (`python -m evals.report ... --run-kind rerun --resolved-after <first analysis date>`). Still inconclusive → dropped.
- **Repeats k:** k = 1 (one live shadow forecast per arm per question).
- **Cost estimate** (per question, per arm, new spend only; arm A already runs):

  | Part | Estimate | Basis |
  |---|---|---|
  | Critic (B, C) | ~$0.003–0.006 | ~4–8k tokens of brief in, ~1–2k out incl. reasoning, gpt-5-mini list price |
  | One search, tokens | ~$0.004 | ~300 in, ~1.5k out at the live Gemini list price in `config.py` |
  | One search, Google grounding fee | ~$0.028 at list price | $14 per 1,000 search queries for Gemini 3.x after 5,000 free a month per project ([pricing](https://ai.google.dev/gemini-api/docs/pricing), checked 2026-10-26); one grounded call runs ~2 queries (assumption: check `search_queries` in live records) |
  | Forecasting on the longer brief | ~$0.012 | the cheap ensemble costs ~$0.01/q (ADR-0009) plus a few k extra input tokens |

  The step counts the grounding fee at list price (ignoring the free tier) so the cap and the records never understate cost. That
  makes the default $0.06 cap stop after **about 1 search** (~$0.035 per arm), not 5. Per arm per question and per week for B + C + D
  at ~50 tournament questions a week (ADR-0010's ~800 per season; check the actual count in the monitor):

  | Setting | Searches/q | $/q per arm | $/week, 3 arms |
  |---|---|---|---|
  | (a) default: cap $0.06, fee counted | ~1 | ~$0.05 | ~$7 |
  | (b) cap raised to ~$0.20, fee counted | 5 | ~$0.18 | ~$27 |
  | (c) fee set to $0 (inside the free tier), cap $0.06 | 5 | ~$0.04 | ~$6 |

  **[Christian]** pick one before switching on. Setting (a) tests "one targeted search" rather than "up to five"; (b) is what v1
  meant but costs ~4× more and may exceed the free tier (3 arms × 5 searches × ~2 queries × ~200 questions/month ≈ 6,000 queries
  plus live use, vs 5,000 free); (c) is right only while the project stays inside the free tier, which F-02 (billing console) has
  to confirm. Recommendation: check F-02 and `search_queries` per call first; then (c) if the free tier holds, else (a) and accept
  that the arms test one search. All of it is paid from the $100 PoC budget (cheap configs), not the $300 season cap; shadow spend
  counts towards each run's cost cap ($10) as today.
- **Decision rule:** analysis by `python -m evals.report` with `--baseline A --arms B --arms C --control D --tier 2` and the
  thresholds below as a `--rule-json` rule written before the analysis; primary binary log score.
  - **Adopt candidate** (goes to confirmation, not live): its 97.5% cluster-bootstrap CI vs A excludes 0 in its favour, it also
    beats D (97.5% CI vs D excludes 0), no worse calibration or failure rate than A, and measured follow-up cost ≤ $0.20/q (so a
    frontier config with it stays well inside the $0.50/q cap). If both B and C qualify, the one with the larger mean gain vs A.
  - **Kill:** **[Christian]** proposed smallest effect of interest **0.015 nats** of log score per question (EXP-005's threshold):
    an arm whose CI vs A lies entirely below +0.015 is dropped, not rerun. Also dropped: measured follow-up cost > $0.20/q.
  - **Narrow win** (CI excludes 0 but its lower end is below 0.015) or **inconclusive** → keep A, one rerun (above).
  - **Confirmation (Tier 2):** the adopted arm keeps running in shadow, **on the frontier roster** **[Christian]**, paired with the
    live frontier forecast, on questions resolving after the decision; it must beat live with a one-sided 95% CI above 0 (rule
    written on its own card before it starts). Only then a live config change: new `CONFIG_VERSION`, Christian's OK.

## How to switch it on (after approval)
In `forecast_engine/config.py`, replace the `SHADOW_CONFIGS` line with:

    SHADOW_CONFIGS: dict[str, BotConfig] = {"cheap": DEFAULT_CONFIG, **EXP008_ARMS}

Before that: apply the cost setting chosen above in `EXP008_ARMS` (one field in `FollowupConfig`), run
`poetry run python tools/check_models.py` (checks the critic model), and note the switch-on date here.

## Changes from v1 (2026-10-07)
- Added arm D, the compute-matched control (protocol §5, 2026-10-19); adoption needs a win over A and D.
- Bonferroni level for m = 2 candidates (97.5%); multi-round arm E considered and left out (reason above).
- Tier 2, rerun rule, kill threshold, fixed analysis point and N added.
- Cost corrected: v1's +$0.03–0.06/q left out Google's grounding fee; with it at list price, 5 searches cost ~$0.17/q per arm.
- Roster made explicit (cheap ensemble; arm A = the existing cheap shadow) instead of "the live roster".

## Results (generated)
## Decision
