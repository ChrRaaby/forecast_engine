# 0013: The published tournament forecast uses the full research package (EXP-013 arm F)

- **Status:** Proposed (accepted when Christian merges the go-live PR)
- **Date:** 2026-11-24

## Context
EXP-013 ("research v2", B-80) has run as shadows since 2026-11-14. Christian decided on 2026-11-14 ("1 week shadow, then live",
card amendment before switch-on) that the full package goes live after a one-week **plumbing check with no scores looked at**, with
the previous live setup kept running as a shadow so the comparison continues with the roles swapped. The check (2026-11-23, 87
questions per arm) found no step failures and no missing records; on 2026-11-23 Christian agreed to build the go-live.

**This is a deliberate exception to the evaluation-first rule** (CLAUDE.md): the check shows that the pipes work, not that forecasts
improve. What limits the risk: the old setup runs on as a shadow on every question, a failing step falls back to today's research,
and the switch-back rule below was fixed before any score was looked at.

## Options considered
1. **Full package live, previous setup as a shadow** (chosen): the experiment continues unchanged in substance (same two setups on
   the same questions); if research helps, live gains now.
2. **Wait for the 150-question look:** safest for the score, but live stays on the lighter research for weeks.
3. **Put the new research under every shadow too:** breaks the running comparisons (EXP-008, EXP-002, arms B-D, Carbon).

## Decision
- The published tournament config is the frontier roster on the base research **plus** the resolution-source block (including its
  NOT FOUND line), the stronger brief and the keyword + historical AskNews searches: exactly arm F as tested (config hash
  `d7d19c6f8464`, pinned in `tests/test_go_live.py`). `CONFIG_VERSION` becomes `frontier-2026-11-24`.
- Every shadow keeps reading the base research. The previous live setup runs on as `exp013-frontier-old` (hash `f3c50c57f8d1`).
  Arm F stops, because it is live. `challenger-gemini-4-carbon` compares with `exp013-frontier-old` from now on.
- The three paid steps run side by side and once per question; the live forecast pays them (live key) and arms B, C and D reuse
  them, so AskNews use stays at 7 credits per question.
- A step that fails or passes its time limit (90 s; news 240 s, because it queues for AskNews's 12 s spacing) leaves the base text.
  A NOT FOUND block alone does not count as research: the bot then refuses to publish, as before.
- Shadows run side by side (at most 6 at once over the run) and outside the live slots, so they no longer hold up the next question.
- **Decision rule** (card, 2026-11-14): at the 150 look and at the final look, published live vs `exp013-frontier-old` on the same
  questions, sign "full research minus old". Keep the full package live if the mean paired difference is above 0, the lower end of
  its 95% cluster-bootstrap CI is above −2 baseline-score points per question, and its failure rate is not worse. Switch back if
  either look fails that, or if the final look puts the CI entirely below 0. No other looks at scores.

## Consequences
- Expected (from the 87 recorded questions, offline): about +25 s before the post (median; p90 27 s, max 53 s); the shadow block per
  question falls from a median 8 min to about 2.5 min.
- Cost: live about $0.17 for the forecasts plus ~$0.02 for the steps per question, on the live key (the Gemini brief is not billed
  through OpenRouter). The record's and the comment's "cost" also count Google's search fee at list price, which Google has not
  charged so far, so they overstate it.
- Published records get longer research; the comment that carries it is private, as before.
- Switching back is one line in `config.py` (`TOURNAMENT_CONFIG = TOURNAMENT_BASE_CONFIG`) plus a new `CONFIG_VERSION`.
- If the season budget runs out, the cheap fallback reads the base research, as before.
