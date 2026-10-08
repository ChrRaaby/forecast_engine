# 13: Structured-reasoning protocol pilot (EXP-005), 10 disputed questions (2026-10-07)

_Unscored: none of these questions had resolved. For Christian's review before the screen. Page:
https://claude.ai/artifact/RjozGmR7ponUZJrrfZdJyd (built by `tools/build_structured_view.py`)._

**Question:** does the protocol (`structured-v1`: base rate → scenarios → drivers as odds factors → reconcile, arithmetic in code)
run cleanly on a frontier and a cheap model, and what do the base rates look like?

**Setup.** `tools/run_structured_pilot.py`: the research/11 records (q43330 replaced by q46128: it was a dry run without research,
made on gpt-6.1-sol's release day, so the release check refuses it), frozen research and as_of. Arms B (one call) and C (blind
base-rate call, then the protocol from that p0). Models: `openai/gpt-6.1-sol` (frontier, ADR-0009 roster, listed 2026-09-29) and
`openai/gpt-6-luna` (today's cheap member), reasoning effort medium. 40 answers, 60 calls.

**Cost.** **$0.666** (hard cap $5): gpt-6.1-sol $0.62 (≈ $0.03 per call), gpt-6-luna $0.046 (≈ $0.0012 per call). Ledger:
`docs/experiments/budget-ledger.md` ($179.33 of $180 left).

## What we saw (facts)
1. **Clean runs.** 40/40 answers parsed and validated; zero validation problems (scenario sums, multiplier scale, weights), on the
   cheap model too. No driver hit the ×10 cap.
2. **Base rates are almost all "from memory".** 82 of 91 reference classes cite the model's memory, 9 cite the question text, **none**
   cite a research item. They can't be checked from what the bot had. 10 classes sit at exactly 0% or 100% (8 from the frontier
   model): small remembered sets like "2 of 2 Andean subsidy removals were reversed" (q46107, blind call: one class 100%, the other
   0%, p0 = 50%).
3. **In one call (arm B) the base rate drifts toward the answer.** Blind base rates (C) differ a lot from in-context ones (B): q46076
   gpt-6.1-sol p0 78% (B) vs 1.1% (C); q46083 50% vs 4.7%; q46082 76% vs 27%. The mean move from p0 to the final number (log-odds) is
   0.54 in B vs 1.28 in C for the frontier model. B's "outside view" has largely absorbed the news: the risk the card names.
4. **The blind call can miss the setup.** Without background, the frontier model gave q46076 ("≥ 3 women judges by Oct 17") a 1.1%
   base rate; the background says two sit already and a third was recommended. The ×10 driver cap then can't reach a sensible number,
   the routes disagree and the model's own number (58%) carries the forecast. A blind base rate needs the status quo, or the
   protocol has to treat "where things stand now" separately from "how often things like this happen".
5. **Routes mostly agree; the model's own number ≈ the code number.** Frontier: 19/20 agree; cheap: 13/20. Mean |code − model| ≤ 0.011:
   models compute the average themselves. The pre-registered "code vs model number" comparison will have little contrast.
6. **The protocol changes forecasts materially.** Mean |final − live median| 0.13–0.19. On q46122 (Texas appeal notice) all four
   answers say 71–74% vs live 25%; on q46113 (Rada vote) 13–37% vs live 55%. Both resolve by Oct 17, so the screen will soon have a
   first hint, but 10 questions decide nothing.
7. **Frontier vs cheap.** Same structure quality on the surface (similar numbers of classes and drivers, both 100% compliant).
   gpt-6.1-sol uses event rates more often (9 vs 4 classes), states fits/misfits in more detail and agrees across routes more often;
   gpt-6-luna's blind base rates are lower on average. Cost differs ~25×.

## Judgement
- The protocol works mechanically; the base-rate step doesn't yet deliver a *checkable* outside view. Before the screen I'd change
  two things (each a counted variant, decided now, before any scored run):
  1. **Counts, not bare proportions:** a proportion must come as k of n with what was counted; code shrinks it ((k+1)/(n+2)) so
     "2 of 2" becomes 75%, not 100%, and flags n < 5.
  2. **Blind call gets the status quo:** give it the question's background (not the research), or add an explicit "current state"
     line separate from the reference classes. Otherwise arm C tests "base rate without knowing the setup", which nobody would do.
- Arm B's drift toward the news (finding 3) is exactly why arm C exists; keep both arms in the screen.
- The cost is well inside the budget: ≈ $0.03 per frontier call puts the screen (50 records × 2 members × 4 calls) near $12 and a
  3-member confirmation (100 × 3 × 3 calls × 3 repeats) near $80; five members ≈ $135.

## Outcome (2026-10-07)
Christian approved both fixes before any scored run. They are protocol `structured-v2` (`forecast_engine/structured.py`) and
variant 2 on the EXP-005 card. The pilot was not re-run; its page notes that it shows v1.
