# Claude Code session prompt: M1 baseline bot

Paste everything below the line into Claude Code, started in the `forecast_engine` folder.

---

We're starting milestone M1 of forecast_engine: a cheap baseline forecasting bot running live on Metaculus.
Before doing anything, read `CLAUDE.md`, `docs/evaluation-protocol.md` (skim), `docs/spec.md`, `docs/backlog.md`, `docs/playbook-tracker.md`, `docs/ideas.md`, `docs/research/03-cost-model.md`
and `docs/research/04-nostreambot.md`. Then propose a plan and wait for my approval before changing files.

## Goal
A bot based on the official Metaculus template (https://github.com/Metaculus/metac-bot-template) that forecasts
the Fall 2026 FutureEval tournament + MiniBench from GitHub Actions, logging its cost per question. Hard ceiling:
$0.50 per question (project cap, decided 2026-09-29). Scenario B is expected to cost ~$0.05–0.10 per question, which is
what the temporary $20 OpenRouter limit can carry until the Metaculus grant arrives. This is a baseline to measure against, so keep it simple and close to upstream.

## Scope
1. **Repo setup.** This folder isn't a git repo yet and already has our docs; keep them. Bring in the template's
   code (not its git history) at the root, and add the template as an `upstream` remote/reference so we can pull
   updates later. Merge `.gitignore` files; `.env` must never be committed. Keep the template's tooling
   (Poetry, Python 3.11+) for now.
2. **GitHub.** Help me create a **public** GitHub repo named `forecast_engine` and push (decided: public, for unlimited
   Actions minutes). Because it's public, check before the first push that no secrets are tracked:
   `.env` is gitignored and `.env.template` contains placeholders only. Tell me which repository secrets to add
   (METACULUS_TOKEN, OPENROUTER_API_KEY, optional ASKNEWS_*).
   The Metaculus bot account is **CrystalBallMcGee-bot**. Its token is already in the local `.env` as `METACULUS_TOKEN`.
3. **Configuration (cost scenario B).** Three cheap forecasters from *different vendors* (OpenAI / Google / Anthropic
   budget tiers), aggregated by the **median**. For research: AskNews **latest-news** search only (1 call/question, never archive in live; the grant is
   1,000 calls/month and archive calls are reserved for backtests) when the keys are present, plus Gemini grounded search
   (free quota) or the cheapest template-supported option for background. See `docs/research/03-cost-model.md` addendum (b).
   Log AskNews calls per forecast. **Verify model IDs against a live OpenRouter model list;** don't
   write them from memory. Keep all model IDs in one place.
4. **Cost & provenance logging.** For every forecast, append a record (question id, type, models, tokens in/out per
   call, $ cost, research provider, config version, timestamp) to a JSONL file that's uploaded as a
   workflow artifact. Add a `CONFIG_VERSION` constant and put it in the published comment too.
   Also persist, per forecast, the full research bundle and each model's rationale (e.g. one JSON file per question
   under an artifact/`data/forecasts/` path, gitignored or uploaded as an artifact). We'll structure these later
   (I-12), and they're impossible to recover after the fact.
5. **Safety rails.** Dry-run by default locally (no publishing). A per-run cap on the number of questions. Remind me to set
   a spend limit on the OpenRouter key.
6. **Scheduling.** Keep the template's workflows. Add a second cron entry so a dropped firing isn't fatal
   (nostreambot measured GitHub cron delivering only ~22% of firings). Note the external-dispatcher option in the
   docs, but don't set it up yet.
7. **Tests.** Add a few offline tests (no network) for the cost logger and the median aggregation. Record the
   build/test/run commands in `CLAUDE.md` → Conventions.
8. **Docs.** ADRs 0001–0005 already exist (template, hosting, OpenRouter, cost ceiling, core/adapter split); follow them,
   and write 0006 "M1 baseline configuration" (the verified model roster and research setup). Update `docs/spec.md` (M1 status;
   correct §7 Architecture if the build differs from it) and add anything
   you learn about real token usage per question to `docs/research/03-cost-model.md`.
   Keep `docs/backlog.md` (B-04…B-07) and `docs/playbook-tracker.md` current as items land, and write a session log.

9. **Evaluation-ready architecture** (the M2 harness depends on it; see `docs/evaluation-protocol.md` §7). Keep the core
   as one entry point, roughly `forecast(question_snapshot, as_of, research_bundle, config)`, with research gathering separate
   from reasoning. Take "today" from a single injectable clock (never hard-coded or read ad hoc). Log the exact prompts, model
   IDs, parameters and request timestamps with each forecast. Don't build the harness itself; just don't paint us into a corner.

## Out of scope for M1
Backtesting harness (M2), prediction-market/resolution-source research, calibration, local 4090 models, Jev,
stacking/debate. If you think something out of scope is needed, stop and argue for it. Don't just add it.

## Definition of done
- `--mode test_questions` runs locally in dry-run and on GitHub Actions against the bot-testing-area,
  and forecasts are visible on the bot's Metaculus profile.
- The tournament workflow is enabled and has forecast at least one real Fall 2026 AIB or MiniBench question.
- A cost log exists showing the actual $ per question (ceiling $0.50; scenario B expected ~$0.05–0.10).
- Docs and ADRs are updated. The spec's M1 row is marked done, with measured numbers.

## Working style
Be critical, per CLAUDE.md. Work in small commits. Ask me before anything that costs money beyond a handful of test
questions, and before anything irreversible (publishing forecasts to the real tournament, making the repo public).
