# EXP-017: FAST lane calibration (does the lane tell us what the tournament tells us?)

_**DRAFT 2026-11-24, not frozen.** Merging this draft does not freeze it. It is frozen by a dated "Frozen" line below, added after
the third dry-run list and Christian's answers to the open choices, and before the first model call. Results section filled by the
analysis tool. Starting point: Christian's decisions and the draft card of 2026-11-15 (project files,
`proposals/fast-lane-card-and-build-brief.md`) and the two design reviews of 2026-11-16._

- **Frozen:** no
- **Date / author:** 2026-11-24, Claude (PC thread), for Christian. Choices decided and prerequisites built 2026-11-26.

## In plain words
The lane asks unpublished questions that resolve in 3–7 days, so an idea gets an answer in weeks instead of months. Before we trust
it, we run versions whose tournament result we will also get, and check that both point the same way. We also check that the lane
sees nothing when nothing changed (two identical arms) and sees something when a lot changed (an arm with no research). Nothing in
this card can change the live bot.

## Card
- **Question it answers:** can a FAST result be used as a screen, i.e. does the lane (1) stay quiet on identical arms, (2) detect a
  large change, and (3) agree in sign with the tournament where the tournament gives a clear answer?
- **Change vs. baseline (config hashes at `frontier-2026-11-24`, checked 2026-11-24):**

  | Arm | Config | Hash | Role |
  |---|---|---|---|
  | A | `cheap`: cheap roster, live prompt, base research (one AskNews + one Gemini search) | `714ff99803b1` | baseline |
  | B | `cheap-old-prompt`: A with the pre-EXP-002 prompt | `cd5e67c675c1` | candidate |
  | C | `exp013-c-writer`: A with the EXP-013 brief writer | `aabd61c581b0` | candidate |
  | A2 | `cheap-aa`: A again, same research, second set of calls | as A | control |
  | N | `no-research`: A with an empty research bundle | as A, research emptied | control |

  A, B and C are the configs running as tournament shadows, so the comparison is like with like. A is **not** the live bot's
  research (full EXP-013 package since 2026-11-23): results compare arms with each other (Christian, 2026-11-24).
  EXP-013's news arm is left out: 6 more AskNews credits a question, about 1,680 for 280 questions, above the lane's 400-credit cap.
- **Question set:** every FAST question picked while the run is on. Source A: Polymarket, non-sports, non-crypto, at least $5k
  liquidity, priced 10–90% when picked, not about market odds, at most two per theme and run. Source B: code-made number questions
  (temperature, earthquakes, BTC/ETH, USD rates). Target **280** questions resolved with a valid forecast from every arm.
  Picking stops at 320 picked or on 2027-01-20. Never pooled with tournament questions. DEV only.
- **Evaluation models + release dates:** cheap roster (GPT-6 Luna, Gemini 3.5 Flash-Lite, DeepSeek V4.1 Flash). Questions open when
  forecast, so T1 holds by construction.
- **Primary metric:** Metaculus baseline score per question, all types, paired against A (protocol §4). Number questions are scored
  on the CDF the live builder would submit. Source A and source B are always shown separately beside it.
- **Secondary (descriptive, no decision):** binary log score on source A; days to resolution (3–4, 5–7); per family; source A against
  the market's price at pick time (stored apart, never shown to a model); calibration; failures and $ per arm; all results again
  without questions flagged "odds scrubbed" or "research incomplete".
- **Expected effect & MDE (planning figures, before any FAST score exists):**
  - Noise per question: binary about 18 points (F-13: 0.126 nats × 144), number questions about 24 points (derived from EXP-015's
    published CI, N 102). Assumed mix: 22.
  - 280 independent questions, 97.5% CI: MDE ≈ **4 points**. Our questions are not independent; with an assumed design effect of 2 it
    is ≈ **6 points**. The real figure is reported as 2.8 × the bootstrap SE.
  - **Arm B will almost surely read "no difference":** EXP-002's screen measured +0.0083 nats (≈ 1 point) for the new prompt. Arm C's
    size is unknown; its scores are blind until EXP-013's look. **So the agreement test alone is likely inconclusive.** That is why
    A2 and N are proposed: they say something about the lane whatever B and C do.
  - N: a large loss is expected (judgement; we have no tournament no-research arm to compare with).
- **Arms and statistics:** m = 2 candidates (B, C), so **97.5% CIs**; A2 and N are controls and don't count towards m. Forecasting calls
  are equal in every arm; the writer's extra research call in C is the treatment, its cost shown beside the score. Cluster
  bootstrap, 10,000 reps, seed 1017. Clusters, fixed now:
  - source A: markets that share a theme tag (the picker's tags, broad site sections excluded) and close in the same ISO week;
  - source B: family × ISO week of the close; temperature by city × ISO week.
- **Completeness:** a question enters the main analysis only if A, B, C, A2 and N each have a valid forecast. Voided questions are out
  and counted. A question AskNews could not be bought for is skipped before any arm runs, and counted.
- **Tier:** none (no live decision). **Rerun:** none; a changed lane gets a new card. **Repeats k:** 1.
- **Looks:** **one**, at 280 complete resolved questions or on 2027-01-31, whichever is first. Below 140: no verdict, counts only. No
  score is read before, and `--report` prints counts only. The look does not touch EXP-013's or EXP-002's own rules, which stay as
  registered; nothing here may be used to amend them.
- **Decision rule (what the lane may claim):**
  1. **A/A:** the 95% CI of A2 − A includes 0. If not: the lane or its statistics are wrong; stop and fix, nothing else is read.
  2. **Sensitivity:** the 95% CI of N − A lies wholly below 0. If not: the lane cannot see even a large change at this N; it stays off.
  3. **Agreement:** for each of B and C whose tournament comparison with `cheap` has a CI that excludes 0 at its own registered look,
     the sign of the FAST mean difference is the same. An inconclusive tournament result counts neither way.
  - 1 and 2 pass, and 3 agrees for at least one arm with no disagreement: **"agrees so far"**; Christian decides whether FAST screens
    get more weight.
  - 1 and 2 pass, 3 has no clear tournament result: **"works, agreement untested"**; FAST stays screens only.
  - Any disagreement in 3: **"does not agree"**; FAST screens are not used until the cause is found.
  - With two arms this is a weak test and the report says so. Without A2 and N (choice 1), only step 3 remains.
- **When the agreement part can be read:** EXP-013's early look and EXP-002's review are both expected in January 2027 at the earliest
  (estimates). The A/A and sensitivity parts don't wait for them.
- **Cost estimate** (measured on 273 shadow records 14–24 Nov, costs only, no scores read):

  | Per question | Real | Booked (Google's fee at list price) |
  |---|---|---|
  | Forecasts A, B, C (incl. polarity check) | $0.032 | $0.032 |
  | Controls A2, N (choice 1; N assumed like A) | $0.021 | $0.021 |
  | Research tokens: base Gemini + writer | $0.012 | $0.012 |
  | Google searches, about 4.8 | $0 inside the 5,000 free a month | $0.068 |
  | AskNews, 1 credit | $0.009 | $0.009 |
  | **Total** | **$0.074** | **$0.141** |

  280 questions ≈ **$21 real / $40 booked**; 320 ≈ $24 / $45. Without the controls about $6 less. Hard cap for this card **$45
  booked**, inside the lane's $80 (real spend, Christian 2026-11-15; the ledger books list price, so it stops early, never late).
  The ledger does not include AskNews (about $3 for 320). About 1,540 Google searches on top of about 2,000 a month already used
  (a lower bound). The experiments key was at $83 of its $250 limit on 2026-11-24 and on course to reach it about 2027-01-15 without
  this run.

## Must be done before "Frozen" (none of it spends)
Items 1–6 built 2026-11-26 (same PR as this card; `fastlane/arms.py`, `fastlane/experiments.py`, `fastlane/analyze.py`, tests in
`tests/test_fastlane_exp017.py`):
1. ✓ Arm table `fastlane/arms.py`: A2 = `cheap-aa`, N = `no-research`; no tournament list contains them and the live fingerprint
   is unchanged (tests).
2. ✓ Analysis tool (`python -m fastlane.run --look`): paired baseline score, the clusters above; locked before the look (counts only);
   tested by re-scoring (identical numbers twice), a made-up A/A set and a made-up large loss.
3. ✓ Picker: one market per event for the whole lane; every tag kept.
4. ✓ Ledger: the writer's search fee is counted once.
5. ✓ EXP-017's $45 cap (on top of the lane's $80), picking stops (320 picked or after 2027-01-20) and the look are in
   `fastlane/experiments.py`; a run serves one experiment.
6. ✓ DeepSeek pinned to **Wafer** in lane runs, the host EXP-015 and EXP-016 used. Note: Wafer serves it quantized (mxfp4,
   OpenRouter's endpoint list 2026-11-26), not the unquantized host protocol §5 asks for; chosen for comparability with those runs.
   The tournament shadows B and C are compared with are not pinned.
7. Dry-run lists: 24 Nov (×2, 15 minutes apart: 17 of 18 shared, counts once), 25 Nov (9 new), 26 Nov (10 new), on until 1 Dec
   (Christian 2026-11-26). Over three days 23 different Polymarket questions from 22 events, about 7 new a day, plus 6 code-made, so
   about 90 a week and **about three weeks to 280** (estimate). Mix: elections (Spain, Taiwan, Victoria, Gagauzia, Quito, Maribor),
   share and commodity price levels, Musk's tweet count, Trump's meetings, Hormuz ship traffic, the FAA ground stop. Final count
   and mix written here when the listing ends.
8. Christian: secrets in the data repo, an AskNews reading (the 23 Nov one expires on 29 Nov), the cron-job.org trigger.

## Choices (decided by Christian 2026-11-26: "i approve the recommendation for each of the three choices")
1. **Controls A2 and N: yes** (about $6).
2. **Protocol text: added when this card is frozen** (the §3 "FAST pool" text of the 15 Nov draft).
3. **Reasoning-effort arms in the same run: yes, if its arms and pilot are ready when this freezes**; otherwise this run does not
   wait. Its arms exist (`effort-high`, `writer-effort-high` in `fastlane/arms.py`); its card, pilot and contrast are not built.

## Later cards that fit this lane (not part of this one)
Each gets its own card, its own m and its own look, and is compared with arm A on the same questions and research (no extra AskNews
credits):
- Reasoning effort × research 2×2 (approved 2026-11-16): high-effort versions of A and C.
- Light thinking steps (I-22): three sentences added to today's prompt. Similar past questions (B-56): up to five resolved questions
  plus the Yes-rate, strict resolved-before-forecast filter. Moved here from the 150-question replay by Christian, 2026-11-24.
- Numbers desk part H, on source B.

Several cards reading one question stream share its luck: a good or bad fortnight hits all of them. Every win is still only a screen.

## Results (generated)
## Decision
