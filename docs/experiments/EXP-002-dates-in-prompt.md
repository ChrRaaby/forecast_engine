# EXP-002: Question dates and explicit YES wording in the forecasting prompt

> **v2 (2026-11-07): pre-registration for the replay, written before any run.** Built in `tools/run_exp002_replay.py` with the
> frozen prompt variant `dates-yes-2026-11-07` (ADR-0012). Nothing has been run or spent. The screen needs Christian's OK to spend.
> The v2 section below replaces the v1 lines where they differ; v1 is kept unchanged under it for the record.

_Card written BEFORE running. Results section filled by the report generator._

## v2 pre-registration (2026-11-07, Claude, with Christian)

- **Why v2:** v1 planned a DEV-manifest run on the M2 harness. The live-record replay (B-38, B-32 guards, B-75 prompt versions)
  now does the same job more cheaply and with real questions, so the test runs there.
- **Change vs. baseline (exact):** both arms are `TOURNAMENT_CONFIG` (Roster A, the live published roster) with the polarity check
  off; the only difference is `prompt_version`: arm A = `template-2026-09-26` (live), arm B = `dates-yes-2026-11-07`
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
- **Evaluation models + release dates:** Roster A (GPT-6.1 Sol, Claude Sonnet 5.5, Gemini 3.1 Pro, Grok 4.7, Qwen3.8 Max), each
  checked per record by `evals.model_release` (T1).
- **Primary metric:** binary log score of the aggregate (median of the members, the live rule), paired by question, cluster
  bootstrap (`evals.report`).
- **Secondary metrics:** (1) polarity audit: the live auditor model reads every member answer in both arms after the run and
  counts answers whose number refers to NO or another event (expected: arm B ≈ 0; v1 audit: 2/111 in the cheap roster); it
  changes no forecast. (2) Brier, calibration (reliability), failure rate.
- **Arms:** m = 1 candidate (B), so 95% CIs. **Tier 2** (a new prompt: confirm before live, protocol §6.2).
- **Expected effect & MDE (certainty: low):** small, ≈0.008–0.010 Brier (v1). The real SD of paired differences isn't measured yet
  (F-13 A/A pending); with the protocol's illustrative SD (0.06 Brier) the MDE at N ≈ 75 is ≈ 0.019 Brier, about twice the expected
  effect. **The most likely screen outcome is "inconclusive"**, which by §6.3 leads to the one deciding rerun. The report prints the
  measured and cluster-aware MDE.
- **Rerun (§6.3):** if the screen is inconclusive or a narrow win, one rerun with the same arms, metric and rule on **only
  questions resolved after the screen's date** (`--resolved-after <screen date> --run-kind rerun`), waiting for at least as many
  such questions as the screen had (≈ mid-December at ~14 resolved binary per week, rough). The rerun decides.
- **Repeats k:** 1.
- **Cost estimate:** ≈ $0.10 per question per arm for Roster A (rough, from live costs) → ≈ $15 for 75 questions, plus < $1 for the
  audit. The tool prints the exact estimate from the archive's per-call costs (`--dry-run`) and refuses above `--max-cost`
  (default $20, hard cap). Same again for the rerun. From the $180 experiment budget.
- **Decision rule (`report.EXP002_RULE`, `--rule exp002`):** adopt if the 95% CI of the log-score difference (B − A) excludes 0 in
  B's favour, reliability isn't worse by more than 0.005 and the failure rate isn't up by more than 2 points; kill if the CI's
  upper end is below 0.015 nats (the fixed smallest effect worth having, as EXP-005/F-12) or the point estimate is ≤ 0; adopt and
  kill both true → kill; otherwise inconclusive → keep the live prompt and run the rerun. The polarity audit is reported, not part
  of the rule. Nothing goes live from this card: a win is Tier 2, so it needs the rerun or a shadow run on fresh questions first.

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
