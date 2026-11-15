# 2026-11-15: number questions, part P (EXP-015) and part S

Christian's go (2026-11-14) on the number-questions brief: part P (range wording) first, then part S (spiky counts). Part H
(history baseline) is not in this build. Nothing live changes; no money spent in the build.

Done (PR 1, part P)
- B-97 / EXP-015: prompt variant `range-calibrated-2026-11-14` (only the numeric prompt's range sentence differs), registered,
  archived and fingerprinted (code-2026-11-15b). Tests: binary and multiple-choice prompts byte-identical to live; the numeric
  prompt differs only by that sentence.
- `tools/run_number_check.py`: number mode of the prompt check (resolved numeric/discrete published records, both arms fresh on
  the cheap roster, DeepSeek pinned, scored on the CDF the live builder would submit, cluster bootstrap 10,000 / seed 1015).
- `evals.exp010 --fetch-scaling` stored 54 more question axes ($0), so all 111 resolved number questions now have one.
- Dry run: N 111 (53 numeric, 58 discrete), est. $2.74, cap $5. Card frozen in the PR.

Next
- Christian: merge PR 1, then "go" in the thread for the scored run (~$2.74).
- Part S in its own PR (S2 grid count first, then S1 per-value prompt for discrete questions).
