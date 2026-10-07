# A "research community" for the bot → experiment proposals

_Approved by Christian 2026-10-07 (I-19). Proposals only; nothing here is run or live. Backlog: B-62, B-63, EXP-008 = B-64, EXP-009 = B-65._
B-62 on, EXP-008 on (checked against main b8a6c21). On approval the PC adds it as `docs/research/12-research-community.md`._

## What a forecasting community gives a human, translated to the bot
In the book, a community helps in four ways. Each maps to a different mechanism for us, with very different evidence:

| What the community gives | Bot equivalent | Evidence / risk | Verdict |
|---|---|---|---|
| **Someone spots what you missed** | A **research desk**: a critic reads the assembled research, lists missing facts and missing perspectives, and targeted searches fill the gaps | Strongest. Research quality is the failure point (nostreambot's worst misses: all models agreeing on one flawed briefing; agentic search R-03; 2+ sources R-06). nostreambot uses two gap-fill passes | **Test first (EXP-008)** |
| **Hearing other people's views and updating** | A **Delphi round**: each member forecasts alone, then sees the others' anonymised reasoning and numbers and may revise once | Mixed. nostreambot's LLM "stacker" that rewrote disagreements was no better than the median. Main risk is **herding**: members converge and the ensemble loses its diversity bonus | **Test, with a herding check (EXP-009)** |
| **Other communities' judgements** | Prediction-market prices, and public Metaculus discussion on main-site questions | Cheap and strong, but it's piggybacking: the protocol (T7, R-23) bans it in backtests unless timestamped as-of | **Already planned as B-13**; no new item |
| **The group learns from past questions** | A **lessons library**: post-mortems on resolved misses (using B-46 graphs) become short dated lessons that later forecasts can retrieve | Plausible, untested. Leakage trap: a lesson may only be used for questions opened after the lesson's date | **Later (B-63)**; needs outcomes first |

Human members (you joining the discussion on disputed questions) are out: Metaculus's AI benchmark tournaments require fully
automated bots. That should be confirmed in the rules before anyone relies on it, but either way it wouldn't be testable as a method.

## EXP-008 Research desk (gap-fill)
- **Arm A:** baseline research (AskNews for tournaments + Gemini briefing), as today.
- **Arm B:** after the research is assembled, a cheap critic call returns up to 5 gaps (missing facts, missing stakeholders' views,
  missing base-rate data, contrary evidence). Each gap becomes one targeted search (Gemini grounded search; no extra AskNews credits).
  The answers are appended to the brief as a separately labelled section.
- **Arm C:** as B, but the critic is told to look specifically for evidence *against* the brief's leading conclusion (a "devil's advocate" researcher).
- Members and the prompt stay the same, so only the research changes. Primary: binary log score. Secondary: B-47 diversity metrics, share of
  B-46 evidence nodes that trace to the gap-fill section (did the members actually use it?), cost.
- **Data constraint:** research changes can't be replayed on frozen bundles, because the new searches must be as-of. Live research
  can't be re-run in the past either. So this is tested as a **shadow run** on live questions: arms B/C run unpublished alongside the
  live bot, and get scored as questions resolve. That's slower (weeks) but leakage-free. Cost: +1 critic call + ~5 searches per
  question, roughly +$0.03–0.06/q in shadow; inside the $0.50 cap if adopted.
- Prior: B small gain, C possibly larger on questions where the brief is one-sided. Model label: **Opus 5.5 · PC**.

## EXP-009 Delphi round
- **Arm A:** median of the 3 independent forecasts (today).
- **Arm B:** each member sees the other two members' rationales (anonymised, numbers included) and gives a revised forecast; median of revisions.
- **Arm C:** as B, but members see only the others' *arguments*, not their numbers (reduces pure anchoring on the others' numbers).
- Runs on frozen research by replay (B-38), so it's cheap: +3 calls per question, ~+$0.01–0.02/q.
- **Herding check:** B-47's member spread and error correlation before vs after the round. Adopt only if the log score improves
  **and** the round doesn't simply collapse members onto one number. Prior: small or zero gain (nostreambot), C ≥ B.
- Run after EXP-004: if arm D there (different research per member) wins, the Delphi round becomes more interesting, because members
  then genuinely bring different information to the "discussion". Model label: **Opus 5.5 · PC**.

## How this fits what's planned
- **EXP-004 arm D** (each member gets different research) is the "independent researchers" half; **EXP-008** is the "shared desk
  that fills gaps" half. They pull in opposite directions (diversity vs completeness), so both are worth knowing. Run order:
  EXP-006 → EXP-004 → EXP-009; EXP-008 runs as a shadow in parallel because it needs live questions, not replay.
- **B-47** diversity metrics are needed for EXP-009's herding check. No change to B-47, just a new user of it.
- **B-46** graphs measure whether members used the gap-fill evidence (EXP-008) and feed the lessons library (B-63).
- **B-13** (market prices) stays as is: it's the "other communities" part.

## New items
- **I-19** Research community (Superforecasting, Christian 2026-10-07): this note.
- **B-62** Shadow-run mode: run an unpublished config alongside live on the same questions, stored separately; prerequisite for
  EXP-008 and also the last step of EXP-005's path to live. Opus 5.5 · PC.
- **B-64 = EXP-008** research desk (shadow; needs B-62). Opus 5.5 · PC.
- **B-65 = EXP-009** Delphi round (replay; needs B-38, B-31, B-47; after EXP-004). Opus 5.5 · PC.
- **B-63** Lessons library from post-mortems, dated, as-of filtered (needs B-38 outcomes + B-46); parked until ~50 resolved misses exist. Sonnet 5.5 · PC.
