# B-95: Research-noise test (a measurement, not an experiment)

_Card written BEFORE running, 2026-11-12. Christian approved the test (recommendation 7 of the non-determinism report,
2026-11-12); the spend needs his go in the PC thread with the estimate below. Tool: `tools/run_research_noise.py`._

## In plain words

Our noise numbers so far (F-13, B-79, the four-run reanalysis) all reused **frozen** research, so they only measure the
models' own randomness. Live, the research is fetched fresh each time: AskNews returns a slightly different set of articles
and the Gemini brief is written anew. Nobody has measured how much that moves our forecasts.

The test asks the same question three times, on unresolved questions:

```
 research fetch 1 ──► cheap roster answers (S1)
                 └──► cheap roster answers again (S2)     S2 vs S1 = model randomness only
 research fetch 2 ──► cheap roster answers (R2)           R2 vs S1 = model randomness + research randomness
```

Example: S1 says 30%, S2 says 32%, R2 says 40%. The models alone moved 2 points; fetching the research again moved it
10 points, so about 8 points came from the research.

## Card

- **Question:** how much larger is the forecast movement when research is fetched again (R2 vs S1) than when only the
  models answer again on the same research (S2 vs S1)?
- **Questions:** 30 unresolved binary tournament questions (open, or closed and waiting) scheduled to resolve more than
  3 days after the run, seeded random sample (seed 1095) of the eligible ones; question text from each one's latest live
  record. 37 were eligible on 2026-11-12 (dry run).
- **Roster:** cheap (GPT-6 Luna, Gemini 3.5 Flash-Lite, DeepSeek V4.1 Flash), median of 3, live prompt, polarity check off.
  Caveat: the frontier roster wobbles about half as much as the cheap one on fixed research (F-13, n = 28), so its
  research share may differ.
- **Research:** the live pipeline (`gather_research`: AskNews latest news, 1 call; Gemini grounded search, 1 call), fetched
  twice back to back (seconds apart). This measures provider randomness, not new news arriving; the live bot's real
  exposure (a run hours later) is at least this large.
- **Measures:** mean |Δp| of the aggregate (points) for S2 − S1 and R2 − S1, their paired difference with a bootstrap 95% CI
  (10,000 reps), the SD of Δlogit for both and the research-only SD (√(var(R2 − S1) − var(S2 − S1))), and the share of
  AskNews article URLs the two fetches have in common.
- **Reading rule (written before the run):**
  - CI of the extra movement includes 0, or its point estimate is below 1 point: research noise is **minor** next to model
    noise; nothing to do. The protocol's noise table stays as is.
  - Extra movement at least 1 point with the CI above 0: research noise is **material**. Then (a) the protocol notes that
    live-vs-replay comparisons carry this extra noise, and (b) Claude proposes, under a new card, whether fetching research
    twice and merging (or a fixed research snapshot per question) is worth testing. No live change from this test.
  - Certainty: N = 30, one run, cheap roster only: **low to moderate** whatever the result.
- **Cost estimate:** forecasting $1.10 (estimate × 1.5 safety; expected ~$0.75), hard cap $2.00; plus 60 AskNews calls from
  the monthly plan and 60 Gemini grounded searches (Gemini tokens at list price ~$0.05; the grounding fee was inside the free
  tier in October, F-02). AskNews note: B-80 arm D already accepts going over the plan (decided 2026-11-12); check the
  remaining credits first (`evals/asknews_budget.py`, which over-counts ~6× until B-90 is fixed).
- **Later, descriptive only:** when these questions resolve, the three forecasts can be scored (does research variation hurt
  or help?). n = 30 makes that anecdotal.

## Results

_Pending (PC run)._
