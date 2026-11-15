# EXP-015: Number-range wording (part P of the number-questions plan)

_Card written BEFORE running: **frozen = merged before the run** (this PR). Decision rule chosen by Christian 2026-11-14 (fast
rule b). Backlog B-97. Code: prompt variant `forecast_engine/prompt_variants/range_calibrated_2026_11_14.py`, tool
`tools/run_number_check.py` (the number mode of the prompt check). Results section filled after the scored run._

- **Date / author:** 2026-11-15, Claude (PC thread), from Christian's brief of 2026-11-14.
- **Hypothesis:** today's number prompt tells the models that "good forecasters are humble and set wide 90/10 confidence
  intervals", so their 10–90 ranges are too wide, most of all when the value is already nearly known. A prompt that asks for a
  range holding the true value about 8 times in 10 (and a narrow one when the value is nearly known) scores better.
  Evidence (ours, exploratory, already seen): 6 of 75 resolved answers fell outside the 10–90 range (about 15 expected if
  calibrated); 24 near-exact medians averaged −2.0 peer. Counter-evidence: EXP-010 arm A (uniform narrowing applied after the
  fact) lost (mean Δ −2.43, 97.5% CI −6.09 to +0.30, killed 2026-11-03). Narrowing done by the model per question is a different
  lever from narrowing everything by the same factor, but the EXP-010 result says "narrower" is not free.
- **Change vs. baseline:** only `numeric_prompt` differs (numeric and discrete questions), by one sentence.
  - Removed: "You remind yourself that good forecasters are humble and set wide 90/10 confidence intervals to account for unknown
    unknowns."
  - Added (verbatim): "Your 10th to 90th percentile range should contain the true value about 8 times in 10 on questions like
    this one. A range wider than the evidence supports scores worse than a well-sized one, and so does one that is too narrow.
    If the value is already nearly known, give a narrow range."
  - Arm A = live prompt `dates-yes-live-2026-11-07`; arm B = variant `range-calibrated-2026-11-14`. Binary and multiple-choice
    prompts render byte-identical to the live prompt (tests/test_prompt_variant_range.py). Same members, aggregation (median),
    CDF builder, research. Polarity check off in both arms (it only touches binary questions).
- **Question set:** every resolved numeric/discrete question with a published live record whose stored research replays; one
  record per question (the earliest published one); frozen research. Dry run 2026-11-15 on the PC archive:
  **N = 111 (53 numeric, 58 discrete), 111 clusters (one per Metaculus post)**, manifest sha256 `49fd402dbf0856f7`.
  Records: 102 tournament, 9 wide-mode (both published live); era by roster: 80 frontier, 31 cheap; as_of 2026-10-03 to
  2026-11-03; 49 numeric/discrete questions not resolved yet were left out; no record failed the T1 release-date check or the
  replay guards. Axes: all 111 from `data/outcomes/scaling.jsonl` (54 rows fetched 2026-11-15 with
  `evals.exp010 --fetch-scaling`, $0); none has `submitted.axis` yet (B-71 records are not resolved). Questions resolving
  between this dry run and the scored run join the set (the tool picks every eligible record at run time); the run's
  manifest is stored with its report. DEV split (no HOLDOUT is touched).
- **Evaluation models + release dates:** cheap roster `DEFAULT_CONFIG`: `openai/gpt-6-luna`, `google/gemini-3.5-flash-lite`,
  `deepseek/deepseek-v4.1-flash`; release dates re-checked with `evals/model_release.py` at freeze (the tool refuses a record
  made before a member's release, T1): 0 records dropped. DeepSeek pinned to one OpenRouter host (B-93):
  `--pin-hosts deepseek/deepseek-v4.1-flash=wafer` (the host that served most of its named calls in the archive). If that host
  refuses the calls, the run stops at the host check with no report; it is rerun with another host and the change is written
  here as an amendment before any score is read.
- **Primary metric:** Metaculus baseline score (points, higher is better) of the CDF the live builder would submit
  (`main.to_prediction` → `get_cdf()`, scored by `evals/exp010.score_cdf` → `evals/scoring.py`), paired B − A on questions where
  both arms produce a CDF. Secondary (reported, not decisive): numeric and discrete separately; share of answers outside each
  arm's 10–90 range (PIT of the submitted CDF < 0.1 or > 0.9, on the paired questions); median ratio of 10–90 widths B/A on the
  question's axis; parse failures and call failures per arm; $ per arm. A question where an arm produces no CDF is left out of
  the pair and counted per arm.
- **Expected effect & MDE:** expected Δ small and positive (+1 to +3 points, judgement). The protocol §5 noise table covers
  binary log score only, so k is not read off it. EXP-010 post-processing had SD(Δ) ≈ 13–14 points; a fresh replay of two
  prompts adds model noise, so assume SD(Δ) ≈ 20: MDE ≈ 2.8 × 20 / √111 ≈ 5 points. A true effect of +2 is therefore
  undetectable at this N in the strict sense; the decision rule below is built for that (it does not ask for a CI above 0).
- **Arms:** m = 1 candidate arm, 95% CI (cluster bootstrap, 10,000 reps, seed 1015). Both arms make 3 forecasting calls per
  question; no compute-matched control needed.
- **Tier (protocol §6.2):** 1 (confirm while live): the change is one prompt sentence, reversible at once, and the live check
  below is the confirmation.
- **Rerun:** first run. Inconclusive → one rerun on questions resolved after the run date.
- **Repeats k:** 1. Both arms run fresh at the same moment (no reused baseline, so no T11 drift caveat).
- **Cost estimate:** dry run: 222 record replays × $0.0082 × 1.5 safety = **$2.74** (666 cheap calls); hard cap **$5**
  (`--max-cost 5`). Spend goes to the budget ledger automatically.
- **Decision rule (Christian 2026-11-14, fast rule b):**
  - **Go live** on the frontier bot if mean Δ > 0 and the lower end of the 95% CI is above −2 baseline points; today's prompt is
    kept as an unpublished shadow arm. **Reversed** if the first 60 live number questions show a loss by the same rule (shadow
    vs live, paired).
  - **Kill** if the 95% CI lies wholly below 0.
  - Otherwise **inconclusive**: keep today's prompt; one rerun on questions resolved after the run date.
  - No paid frontier replay.
  - Note (judgement): this rule accepts some risk on purpose. If the true effect is 0 and SD(Δ) ≈ 20, about 1 run in 5 would
    still meet "mean > 0 and lower end above −2"; the 60-question live check is what catches that.

Command (after merge and Christian's "go" in the thread, from the main checkout on the PC):

    poetry run python tools/run_number_check.py --exp EXP-015 --variant range-calibrated-2026-11-14 \
        --pin-hosts deepseek/deepseek-v4.1-flash=wafer --max-cost 5

## Results (generated)

_Not run yet._

## Decision

_Not run yet._
