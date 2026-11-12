# 2026-11-12: acting on the non-determinism report

Christian approved all five open recommendations of the non-determinism report
(https://claude.ai/artifact/E5GFK7R8iii4J1HsM3AaQS) and the F-18 recheck with two same-moment arms (F-18 stays with the
coordinator, ~23 Nov).

Done
- B-91 (on main, 16fc4f3): protocol §5 noise table, "read k off the table", prefer designs that share model answers; §8 note
  on the four-run reanalysis; template asks for the smallest effect of interest and k.
- B-92 / EXP-014: card pre-registered on main before any pooled forecast was computed; screen run in the cloud ($0):
  N 88, +0.41 baseline points/q, CI −2.32 to +3.38, inconclusive (low certainty). Rerun F-21 at ≥ 80 fresh questions.
- B-93: `--pin-hosts MODEL=HOST` for `tools/run_prompt_check.py` and `tools/run_aa_replay.py` (OpenRouter `provider.order`,
  fallbacks off), `evals/hosts.py` refuses the report if a pinned model ran elsewhere or if a pinned arm meets an unpinned
  stored baseline. `OpenRouterClient(pinned_hosts=...)`; the live bot passes none (code-2026-11-12, forecasts unchanged).
- B-94: `tools/check_served_models.py`, offline alert for the weekly report. On today's data: no warning; DeepSeek drew 11 new
  hosts in the last week (normal lottery); Claude Sonnet 5.5 has only ever been served by "Claude Platform on AWS" (a move to
  another host would warn). OpenRouter returns the requested model name, so silent vendor updates are not visible.
- B-95: card + `tools/run_research_noise.py`; dry run 30 of 37 eligible questions, est. $1.10 + 60 AskNews calls. Waits for
  Christian's go on the PC.

Next
- Christian: review the PR (B-92 tool, B-93, B-94, B-95 tool); go for the B-95 PC run.
- Weekly report routine: add the B-94 step once merged.
