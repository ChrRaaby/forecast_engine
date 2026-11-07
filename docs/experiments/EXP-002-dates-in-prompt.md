# EXP-002: Question dates and explicit YES wording in the forecasting prompt

> **v2.1 (2026-11-07, before any run): run on the CHEAP roster to save cost** (Christian: ~$22 on Roster A is too steep for a
> recurring prompt check). Lines marked v2.1 below replace the Roster A ones.
>
> **v2 (2026-11-07): pre-registration for the replay, written before any run. EXP-002 is a FIX with a no-harm check, not a test
> that must show a gain (Christian's decision, 2026-11-07).** Built in `tools/run_exp002_replay.py` with the
> frozen prompt variant `dates-yes-2026-11-07` (ADR-0012). Nothing has been run or spent. The screen needs Christian's OK to spend.
> The v2 section below replaces the v1 lines where they differ; v1 is kept unchanged under it for the record.

_Card written BEFORE running. Results section filled by the report generator._

## v2 pre-registration (2026-11-07, Claude, with Christian)

- **Fix, not a bet (Christian, 2026-11-07):** both changes repair known defects: answers giving P(NO) (2/111 in the v1 audit; the
  live polarity guard catches them, this removes the cause), and a prompt that asks for "the time left" without giving the end date.
  A fix doesn't need to prove a score gain, which ~75 questions can't show anyway; it must fix the defect and do no clear harm.
  Prompt *bets* (judge the research, base rate, weighed scenarios) are I-22, for a separate multi-arm run at ~150 questions.
- **Why v2:** v1 planned a DEV-manifest run on the M2 harness. The live-record replay (B-38, B-32 guards, B-75 prompt versions)
  now does the same job more cheaply and with real questions, so the test runs there.
- **Change vs. baseline (exact):** (v2.1) both arms are `DEFAULT_CONFIG` (the cheap roster M1, which runs live as the unpublished
  shadow; `--config cheap`) with the polarity check off; the only difference is `prompt_version`: arm A = `template-2026-09-26` (live), arm B = `dates-yes-2026-11-07`
  (`forecast_engine/prompt_variants/dates_yes_2026_11_07.py`, frozen copy `evals/frozen_prompts_dates_yes_2026_11_07.py`). Arm B is
  the live prompt plus:
  1. after "Today is …": *"This question is still open. It is scheduled to resolve on YYYY-MM-DD."* from the snapshot's
     `scheduled_resolution_time` (no line if it is missing; all question types);
  2. binary only, the answer line ends *"…as: "Probability: ZZ%", 0-100, where ZZ is the probability that this question resolves YES"*.
  **Differences from v1:** the close date is left out, because stored records hold only the open and scheduled resolution times;
  the YES wording sits after the format instead of inside it, so the answer format itself doesn't change.
- **Same-compute control:** arm B makes the same calls as arm A (one per member, no extra calls), so arm A is the
  compute-matched control. **Both arms are replayed now**, at the same time per question (T11); the old live answers are not used.
- **Question set:** resolved binary questions (yes/no) in `data/outcomes/outcomes.jsonl` with a live record in `data/runs/`; one
  record per question (the earliest non-shadow one); records refused by the release-date check (T1) or either replay guard
  (research dated after as_of; stored inputs that don't rebuild the live prompt) are counted and dropped. All eligible questions at
  the time of the screen (about 71 on 2026-11-02, more after the next archive sync). Clusters: Metaculus posts. DEV/HOLDOUT: n/a.
- **Leakage (T5 and the B-77 finding):** the only new information is the scheduled resolution date, which is public from the day a
  question opens and is read from the snapshot stored at as_of (never the actual resolve time). The replay calls only the
  forecasting models on frozen research: no research step, no follow-up search (that runs only in main.py), so nothing sees
  today's web.
- **Evaluation models + release dates:** (v2.1) cheap roster M1 (GPT-6 Luna, Gemini 3.5 Flash-Lite, DeepSeek V4.1 Flash), each
  checked per record by `evals.model_release` (T1). **Limit:** the live published roster is Roster A, and a prompt can act
  differently on stronger models; this run screens. It suits the fix metric, since both known wrong-sided answers came from Gemini
  Flash-Lite. Roster A is checked on live results, not by a separate paid replay (see below).
- **Primary metric:** binary log score of the aggregate (median of the members, the live rule), paired by question, cluster
  bootstrap (`evals.report`).
- **Fix metric:** polarity audit: the live auditor model reads every member answer in both arms after the run and
  counts answers whose number refers to NO or another event (expected: arm B ≈ 0; v1 audit: 2/111 in the cheap roster); it
  changes no forecast. Also reported: Brier, calibration (reliability), failure rate.
- **Arms:** m = 1 candidate (B), so 95% CIs. **Tier 1** (a fix that is cheap and easy to undo: goes live with the old prompt as
  the unpublished shadow, so the comparison keeps running on fresh questions and live can switch back).
- **Expected effect & certainty:** score effect small, ≈0.008–0.010 Brier (v1), far below what N ≈ 75 can detect (MDE ≈ 0.019 Brier
  with the protocol's illustrative SD; the real SD is unmeasured until the F-13 A/A). **What this run can show:** whether the fix
  removes wrong-sided answers, and whether the new prompt is *clearly* worse. **What it can't show:** a small gain, or a small
  harm, or how Roster A reacts. Those are what the Tier 1 shadow is for (v2.1): after going live, an unpublished cheap-roster shadow
  keeps old and new prompt side by side on fresh questions (cheap; set up in the go-live PR).
- **No rerun before going live** (a fix, Tier 1). At ~150 resolved questions (with the frontier verdict) the shadow comparison and
  live Roster A results are reviewed; if the new prompt is then clearly worse (same test), live switches back.
- **Reusable baseline (v2.1, Christian 2026-11-07):** arm A's replayed records are kept (`data/experiments/EXP-002/<run>/records/A`)
  as the cheap-roster baseline for later prompt checks on the same questions, so a later check pays only for its new arm. Caveat
  to state on any result that reuses it: the arms were then not run at the same moment (T11), and providers drift over time.
- **Repeats k:** 1.
- **Cost estimate:** (v2.1) Roster A would cost ≈ $22 for the 88 eligible questions (dry run 2026-11-07: $0.123/question/arm incl.
  audit); too steep for a recurring check. The cheap roster's exact estimate comes from `--dry-run --config cheap`; the run goes
  ahead without a further question if that estimate is ≤ $5 (`--max-cost 5`, hard cap). One run. From the $180 experiment budget.
- **Decision rule: no-harm check** (`no_harm()` in `tools/run_exp002_replay.py`, fixed before any run). Arm B goes live (Tier 1)
  only if ALL hold:
  1. the score is not clearly worse: the 95% CI of the log-score difference B − A is not entirely below 0;
  2. calibration (reliability) is not worse by more than 0.005;
  3. the failure rate (member forecasts with no usable number) is not up by more than 2 points;
  4. the polarity audit ran and wrong-sided answers (NO or OTHER) are not more common in B than in A.
  Any condition failing → don't ship; a changed prompt would be a new variant with its own card. Going live itself (prompts.py,
  LIVE, CONFIG_VERSION, shadow config) is a separate PR after Christian's OK.

## v1 (2026-10-03), kept for the record

- **Date / author:** 2026-10-03, Claude (from B-09 / B-09b)
- **Hypothesis:** stating the question's close and scheduled resolution dates explicitly ("This question is still open; it closes on
  X and resolves on Y") reduces open-vs-resolved confusion and time-horizon errors (R-18), improving log score mainly on binary
  questions whose research describes events that might already have happened.
- **Change vs. baseline:** prompts gain one line with close and scheduled-resolution dates, rendered from the question snapshot (no new
  date sources; the Clock still supplies "today"). **Added 2026-10-05:** the binary answer line says what the number is: "Probability: ZZ%
  (the probability that this question resolves YES)". A polarity audit found 2 of 111 live binary answers giving P(NO) (posts 45898,
  45925); a polarity guard now excludes such members live, and this wording is the real fix. Secondary metric: guard exclusion rate
  (expected to drop to ~0). `prompt_version` bump; everything else equal.
- **Question set:** DEV manifest (TBD in B-27) plus live-replay records (B-38). Binary primary; MC/numeric secondary.
- **Evaluation model(s) + release dates:** the evaluation roster from B-27 (models released by ~2025-12-01).
- **Primary metric:** binary log score (Metaculus baseline score).
- **Expected effect & MDE:** small, likely below the MDE at N≈400 (≈0.008–0.010 Brier); worth running only bundled with other
  low-cost prompt fixes, or as a check that it does no harm.
- **Repeats k:** 1 for screening, 3 if promising.
- **Cost estimate:** ~$5 (reasoning-only replay on cached research).
- **Decision rule:** adopt if the paired CI on the primary metric excludes 0 in favour of the change and calibration doesn't worsen;
  inconclusive → keep the simpler prompt (protocol §6). Also check T5: the leakage test must allow exactly these dates and no others.

## Results (generated)
## Decision
