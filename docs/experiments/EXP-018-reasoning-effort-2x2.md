# EXP-018: thinking harder × better research (reasoning effort 2×2, FAST lane)

_**DRAFT 2026-11-26, not frozen.** Merging this draft does not freeze it. It is frozen by a dated "Frozen" line below, after the pilot
and before the first scored run. Results section filled by the analysis tool. From the card Christian approved on 2026-11-16 ("lets
go with that experiment"; project files, `proposals/reasoning-effort-2x2-card-and-brief.md`); changes against it are listed at the
end._

- **Frozen:** no
- **Date / author:** 2026-11-26, Claude (PC thread), for Christian.

## In plain words
Our forecasting models are asked to think at "medium" effort. We test whether "high" helps, and whether it helps more with better
research, by running four versions on the same FAST lane questions:

```
                     today's research (A)      better research (EXP-013 writer, C)
 medium effort       A  cheap                  C  exp013-c-writer
 high effort         B  effort-high            D  writer-effort-high

 Does thinking harder help?                 ½·[(B − A) + (D − C)]      (the one decision)
 Does it help MORE with better research?    (D − C) − (B − A)          (described, not decided)
```

## Card
- **Hypothesis:** (H1) high effort improves the cheap roster's forecasts; (H2) the gain is larger with the writer's research.
  - Evidence for H1, from other teams: in Metaculus's spring 2026 results the higher-effort version beat the lower one in 8 of 8
    same-model pairs (https://www.lesswrong.com/posts/wZBbDqzfBjYG58CxK, read 2026-11-14). That is the organisers' observation,
    with no effect size, on frontier-class models.
  - Our own evidence: none. H2 is Christian's reasoning, with no evidence either way; the opposite (more gain when research is thin)
    is also plausible.
- **Change vs. baseline:** only the three forecaster calls of B and D change: `reasoning_effort` "medium" → "high" and `max_tokens`
  16,000 → 32,000. Research, the polarity check and helper calls are untouched (`fastlane/arms.py`, `high_effort`; tested).
  - The larger `max_tokens` is part of the treatment. The limit covers thinking plus answer, and DeepSeek already reached 16,000 on
    3 of 210 medium-effort answers on the tournament cheap shadow (14–26 Nov, finish reason "length").
  - Arm hashes are written in at freeze.
- **Question set:** every FAST lane question picked while the run is on, resolved by the look, with a valid forecast from all four
  arms. Same pool, clusters and void rule as EXP-017. A and C are EXP-017's arms on the same questions and the same research, so
  they are paid once (Christian, 2026-11-26: run together if ready).
- **Evaluation models:** cheap roster (GPT-6 Luna, Gemini 3.5 Flash-Lite, DeepSeek V4.1 Flash, pinned to Wafer as in EXP-017).
  Questions are open when forecast (T1).
- **Primary metric (one test, m = 1, 95% CI):** per question ½·[(B − A) + (D − C)] in Metaculus baseline score, all types (protocol §4
  default), cluster bootstrap (10,000 reps, seed 1018).
- **Secondary (descriptive, no decision):**
  - the interaction (D − C) − (B − A) with its CI; B − A and D − C alone;
  - per model: each model's own answers scored as if it were the only forecaster;
  - splits by source, by type and by days to resolution;
  - everything again without questions flagged "odds scrubbed" or "research incomplete";
  - per arm: share of empty, cut-off ("length") or unparseable answers; output tokens, seconds and dollars per question.
- **Expected effect & MDE (planning figures):**
  - Noise of one paired difference: about 18 points for binary and about 24 for number questions (as EXP-017). The primary contrast
    averages two differences, about 15 points per question in a 50/50 mix.
  - At 280 questions the MDE is ≈ **2.5 points**, or ≈ **3.5** with an assumed design effect of 2. The interaction is about twice as
    noisy: ≈ 5–7 points.
  - So a real gain of ordinary size ("thinking harder helps") is visible; for "helps more with better research" expect a direction,
    not a verdict.
  - Prior: judgement, a small gain at most. Cheap models may not use the extra room well, and a model that thinks longer can also
    talk itself out of a good answer.
- **Calls:** B and D make the same number of calls as A and C. More thinking is the treatment, so there is no compute-matched control;
  cost is shown beside the score.
- **Tier:** screen (FAST pool rules). Nothing goes live from this run.
- **Looks:** one, together with EXP-017's (280 complete questions with all four arms, or 2027-01-31). If the primary mean is positive
  but its CI spans 0, one more look at 560 complete questions, if the lane runs that long. No score is read in between.
- **Decision rule:**
  - CI above 0: write a card for a **frontier tournament shadow** at high effort, with the measured cost per question. Christian
    decides that spend.
  - CI wholly below 0: killed, noted in the choices ledger (B-52).
  - Otherwise inconclusive: nothing changes. The second look applies if the mean is positive.
  - A model that ignores the setting (no more output tokens at high in the pilot) stays in, and the per-model table says so.
  - EXP-017's verdict on the lane applies on top: if the A/A or sensitivity check fails, this result is not read either.
- **Cost (measured 2026-11-26 on 210 cheap-shadow answers per model, costs and tokens only, no scores read):**
  - At medium, forecasting costs about $0.009 a question for the three models: Luna $0.001, Gemini $0.004, DeepSeek $0.004. DeepSeek
    already writes the most (median 3,800 output tokens, p90 9,200).
  - If high doubles that, B and D add about $0.04 a question together: **about $11 for 280, $12 for 320**. If it triples, about
    $16 for 280.
  - Hard cap **$25** on this card's own line, inside the lane's $80. With EXP-017's $45 that leaves about $10 of the lane's $80 for
    the later prompt cards.
  - No extra AskNews credits or Google searches, because the research is shared.
- **Pilot before freezing (needs Christian's "go", about $0.50, no scores):** ten open FAST questions, arms A and B only. Per model:
  output tokens at medium and at high, cost, seconds, finish reason, parse result.
  - **Pass:** every model answers and parses on at least 9 of 10 at high, and no call takes longer than 5 minutes.
  - **Model fails:** report to Christian before freezing. A repeat with that model's `max_tokens` raised once (48,000) needs a
    small arm change first; it is not built.

## To build before the pilot (code, no spend): built 2026-11-26
1. ✓ **Joint run** (`--experiment EXP-017,EXP-018`; `fastlane/experiments.py` `plan`): every arm runs once; B's and D's calls are
   paid from EXP-018's line, the rest and the shared research from EXP-017's. Each card keeps its own cap; at $25 only B and D stop
   (`SpendGuard.extra_caps`). Picks record every experiment they serve.
2. ✓ **The contrast** (`fastlane/analyze.py`, `--look --experiment EXP-018`): ½·[(B − A) + (D − C)] with its 95% CI and the card's
   verdict line; the interaction, B − A and D − C, each by source and without flagged questions; the per-model table (each model's
   own answers, high − medium); the per-arm table (cut off, failed, unparsed, output tokens, seconds, $). Locked until the look,
   like EXP-017's.
3. ✓ **Pilot command:** `python -m fastlane.run --pilot-effort --go` (also needs `FASTLANE_ON=1`): 10 open questions (6 Polymarket, 4
   code-made), arms A and B, kept apart from every experiment's pool in `data/fastlane/pilot-effort/`, paid from EXP-018's line. It
   writes the table above and the pass rule; it scores nothing. In GitHub Actions it is a manual workflow mode that also needs the
   switch file `fastlane/PILOT_EFFORT` in the data repo (created only on Christian's "go").
4. ✓ **Thinking tokens:** not recorded separately (that would change the live code fingerprint). Output tokens, which include the
   thinking, and cost are used.
5. ✓ **Tests** (`tests/test_fastlane_exp018.py`, offline): effort and `max_tokens` reach B's and D's forecaster calls and nothing
   else; one writer brief per question for C and D; the ledger split and the cap of a joined card; the contrast arithmetic on a small
   set; a made-up real gain found, and no gain not called a win; the lock; the pilot's pass and fail.

## Changes against the 16 Nov draft
- **Metric:** the draft wanted binary log score, with number questions reported separately. This card uses the baseline score over
  all types, the protocol default since 2026-11-11, which is also EXP-017's metric. Source and type splits are still reported.
- **Arm A** is the base research, not the live package. The live bot moved to the full EXP-013 package on 2026-11-23 (Christian,
  2026-11-24).
- **Costs** are measured instead of assumed. The cap ($25) and the pilot are unchanged.
- **The fail-safe on the lane** (EXP-017's A/A and sensitivity checks) is new.

## Results (generated)
## Decision
