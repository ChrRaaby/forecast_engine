# 2026-11-15: number questions, part P (EXP-015) and part S

Christian's go (2026-11-14) on the number-questions brief: part P (range wording) first, then part S (spiky counts). Part H
(history baseline) is not in this build. Nothing live changes; no money spent in the build.

Done (PR 1, part P)
- B-97 / EXP-015: prompt variant `range-calibrated-2026-11-14` (only the numeric prompt's range sentence differs), registered,
  archived and fingerprinted (code-2026-11-15b). Tests: binary and multiple-choice prompts byte-identical to live; the numeric
  prompt differs only by that sentence.
- `tools/run_number_check.py`: number mode of the prompt check (resolved numeric/discrete published records, both arms fresh on
  the cheap roster, DeepSeek pinned, scored on the CDF the live builder would submit, cluster bootstrap 10,000 / seed 1015).
- `evals.exp010 --fetch-scaling` stored 54 more question axes ($0), so every resolved number question now has one.
- Dry run: N 102 tournament questions (49 numeric, 53 discrete), est. $2.52, cap $5; wide-mode records left out (card author).
  Card frozen in the PR.

Next
- Christian: merge PR 1, then "go" in the thread for the scored run (~$2.52).
- Christian: merge PR 2 after PR 1, then "go" for EXP-016 (~$0.54).

Done (PR 2, part S)
- S2 snap-to-grid: counted first, from the question texts: about 1 of 53 resolved numeric questions (AWS growth, a whole-percent
  figure) has a published grid step >= 1/100 of its range; prices, rates and counts are published in small steps. Dropped by the
  card author (2026-11-15). forecasting-tools' CDF limits read for the record: per-bucket cap 0.2 x 200/N x 0.95, min step 0.01/N,
  open-bound mass >= 0.001.
- B-98 / EXP-016 (S1): variant `per-value-discrete-2026-11-15` (value list prompt, "value: probability" parser that must add up
  to 90-110%, member mean, 0.5% floor, CDF from the values); `core.forecast` uses a variant's own answer path where it applies
  (code-2026-11-15c, live forecasts unchanged). Number tool gains `--types`, `--max-values`, `--level`, probability on the true
  value. Dry run N 22, est. $0.54, cap $3. Card frozen in PR 2.
