# EXP-016: Spiky counts, per-value answers on small discrete questions (part S1 of the number-questions plan)

_Card written BEFORE running: **frozen = merged before the run** (this PR). Decision rule chosen by Christian 2026-11-14 (fast
rule b). Backlog B-98. Code: prompt variant `forecast_engine/prompt_variants/per_value_discrete_2026_11_15.py` (prompt, parser,
aggregation, CDF), answer-path hook in `forecast_engine/core.py` / `prompt_versions.answer_path`, scored with
`tools/run_number_check.py` (EXP-015's number mode, with `--types` and `--max-values`). Results section filled after the run._

- **Date / author:** 2026-11-15, Claude (PC thread), from Christian's brief of 2026-11-14; S2 dropped and m = 1 set by the card's
  author on 2026-11-15, before any run.
- **Hypothesis:** on a discrete question with few possible values (a count of storms, regions, goals), the answer often sits on
  one or two values. Six percentiles spread over a smooth CDF can't put a spike there; asking for a probability per value can.
  So per-value answers score better on these questions. Evidence: ours, exploratory, from the brief (24 near-exact medians
  averaged −2.0 peer); no direct test yet. Counter-evidence: a spike on the wrong value is punished hard (log scoring), and
  EXP-010 found narrowing after the fact lost.
- **Change vs. baseline:** arm A = live prompt `dates-yes-live-2026-11-07` (six percentiles, median of members, live CDF builder).
  Arm S1 = variant `per-value-discrete-2026-11-15`, on discrete questions with ≤ 25 possible values only:
  - the prompt lists the possible values (and "Below X" / "Above Y" lines when a bound is open) and asks for one
    "value: XX%" line each; the rest of the question text, research, dates and the (a)–(f) steps are the live prompt's. The
    percentile formatting instructions, the bound sentences and the "humble, wide 90/10" sentence are not in it (they are about
    percentiles; the value list replaces the bounds);
  - parser: the last line per value wins; values without a line get 0; the lines must add up to 90–110% (else a parse error,
    never stretched); each member is normalised to 1;
  - aggregation: mean across members, then a 0.5% floor per value (mixing with a uniform, so every value is ≥ 0.5% and the total
    is 1);
  - CDF: built from the per-value probabilities (values + 1 points; closed bound = no mass beyond it). Every CDF is checked
    against Metaculus's limits (`evals/exp010.cdf_problems` with forecasting-tools' per-bucket cap: 0.2 × 200 / N × 0.95; at most
    25 values the cap never binds; minimum step 0.01/N; open-bound mass ≥ 0.001 — the 0.5% floor covers these).
  - Possible values are read from the question snapshot (step = 2 × (nominal lower − lower bound)) and checked against the
    stored axis; a question where they disagree is dropped and counted (none in the dry run).
  - Questions with more than 25 values, and all other types, get the live prompt and today's parsing in the variant.
- **Question set:** every resolved discrete question with a published live tournament record (as EXP-015), ≤ 25 possible values,
  frozen research, one record per question. Dry run 2026-11-15: **N = 22, 22 clusters (one per post)**, manifest sha256
  `c71c9f654456c0e0`; 31 resolved discrete questions have more than 25 values and are left out. The brief expected ~40; it is 22.
  Questions resolving before the run join (picked at run time; manifest stored with the report). DEV split.
- **Evaluation models + release dates:** cheap roster `DEFAULT_CONFIG` (GPT-6 Luna, Gemini 3.5 Flash-Lite, DeepSeek V4.1 Flash),
  DeepSeek pinned `--pin-hosts deepseek/deepseek-v4.1-flash=wafer` (same rule as EXP-015 if the host refuses); release dates
  checked by `evals/model_release.py` at pick time (T1): 0 dropped.
- **Primary metric:** Metaculus baseline score of the CDF each arm would submit, paired S1 − A. Secondary: probability put on the
  true value (pmf of the outcome's bucket), share of questions where an arm gave the true value < 2%, parse failures per arm,
  $ per arm. A question where an arm produces no valid CDF is left out of the pair and counted.
- **Expected effect & MDE:** judgement: large when it works (a spike on the right value scores far above a smooth CDF), large
  losses when the spike is wrong, so SD(Δ) is high; assume ≈ 25 points. MDE ≈ 2.8 × 25 / √22 ≈ 15 points. This run can only
  see a big effect; anything smaller is undetectable at this N, which the decision rule allows for.
- **Arms:** m = 1 (S2 snap-to-grid dropped: only about 1 of 53 resolved numeric questions has a published grid step ≥ 1/100 of
  its range, judged from the question texts on 2026-11-15), so a **95% CI**, cluster bootstrap 10,000 reps, seed 1015. Both arms
  3 forecasting calls per question.
- **Tier (protocol §6.2):** 1 (confirm while live), with the 60-question live check below.
- **Rerun:** first run.
- **Repeats k:** 1; both arms fresh at the same moment.
- **Cost estimate:** dry run: 44 record replays × $0.0082 × 1.5 = **$0.54** (132 cheap calls); hard cap **$3** (`--max-cost 3`).
- **Decision rule (fast rule b, Christian 2026-11-14):**
  - **Go** if mean Δ > 0 and the 95% CI's lower end is above −2 baseline points: per-value answers go to the live frontier bot
    on discrete questions with ≤ 25 values, today's answer format kept as a shadow arm; **reversed** if the first 60 live such
    questions show a loss by the same rule. Going live needs its own build first (the live submission path does not read a
    per-value aggregate today), with Christian's OK.
  - **Kill** if the 95% CI lies wholly below 0.
  - Otherwise **inconclusive**: S1 runs on as an unpublished shadow arm on new discrete questions.
  - No paid frontier replay.

Command (after merge and Christian's "go" in the thread, from the main checkout on the PC):

    poetry run python tools/run_number_check.py --exp EXP-016 --variant per-value-discrete-2026-11-15 --types discrete \
        --max-values 25 --pin-hosts deepseek/deepseek-v4.1-flash=wafer --max-cost 3

## Results (generated)

_Not run yet._

## Decision

_Not run yet._
