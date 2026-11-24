# 2026-11-24: researcher pilot (B-102), build

Christian's "start the pilot build" (2026-11-23). Nothing live changes; $0 spent.

Done
- Step 0 (card): vendor search through OpenRouter, browser on the PC (3 of 3 JavaScript-only pages readable), link classifier found and
  moved to `evals/source_links.py`.
- `tools/researcher_pilot.py` with readers R0 to R3, block validator, safe page opener (own proxy), question draw, sheet and report.
  100 offline tests. ADR-0014 (Playwright and pypdf stay out of the lock file).
- Trial draw (data repo `experiments/B-102/`): 183 pending tournament questions, 104 about a number; groups 4 / 15 / 17 candidates, so
  the JavaScript group is one short of 5 today.

Next
- Christian: merge, and when to run (early December suggested, several drawn questions have no figure before 28 to 30 November).
- Untested until the paid run: the real answers of the Gemini loop and the OpenRouter `/responses` call.
