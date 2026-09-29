# forecast_engine: Spec

_Living document. Last updated: 2026-09-30_

## 1. Problem & motivation
LLM-based "judgmental forecasting" bots now match or beat good human forecasters on open-ended
questions about real-world events (geopolitics, economics, science, tech). In September 2026 a bot
won the Metaculus Cup for the first time: *laertes*, built by a solo developer (Jeffrey Liang) in
under 150 hours for a couple of thousand dollars, beating startups that had raised $15m+
(The Economist, Sept 2026; see `docs/research/00-landscape.md`).

forecast_engine is a private project to build a bot in that class: given a question with a resolution
date, it researches the question, reasons about it, and outputs a calibrated probability or
distribution with an explanation.

## 2. Goals
- Build an end-to-end forecasting bot that takes a question, researches it, forecasts, and explains
  its reasoning.
- Measure it honestly: scored against real resolutions and compared with baselines (a single LLM call, the community forecast).
- Compete in public Metaculus tournaments (AI Forecasting Benchmark and/or Metaculus Cup).
- Stretch: a leaderboard finish that's competitive with the solo-developer benchmark set by laertes.
- Learn modern agentic/LLM engineering along the way.

## 3. Non-goals (v1)
- Training or fine-tuning our own foundation model.
- Trading real money on prediction markets. That's a possible later phase, gated on proven
  calibration plus a legal/tax check.
- Long-horizon (multi-year) forecasting.
- A polished public product or UI.

## 4. Users & use cases
| User | Use case | What "good" looks like |
|---|---|---|
| Christian | Run the bot on tournament questions automatically | Forecasts submitted on schedule, no babysitting |
| Christian | Ask the bot an ad-hoc question | A probability plus a readable rationale and sources |
| Christian | Improve the bot | Change a prompt/model/component, re-score on past questions, see if it helped |

## 5. Forecasting scope
- **Question types:** binary, numeric (continuous CDF), multiple-choice, following Metaculus formats
- **Horizon:** days to ~4 months, matching Metaculus Cup/AIB question windows
- **Pipeline (initial hypothesis):** question parsing → research (news/search APIs, data lookups)
  → several independent forecasts (multiple LLMs/prompts) → aggregation (+ optional debate/critique)
  → calibration/extremization → submit, with a stored rationale
- **Data sources:** news/search APIs (e.g. AskNews, Exa, Perplexity), Metaculus API; later
  specialist datasets
- **Evaluation:** log score / Brier for binary, CRPS or Metaculus score for continuous; compared against
  a single-call baseline and the community prediction. Backtesting on resolved questions with
  **leakage control**: the research must be date-restricted, and the model's training cutoff must predate the question.
- **Budget (target):** similar to laertes, around 150 hours and ≤ ~$2k (to confirm)

## 6. Requirements
### Functional
- _TBD_
### Non-functional
- **Context:** private project, unrelated to UFST. No on-prem or data-sovereignty constraints; cloud services and the latest tools are fair game.
- **PoC budget:** $100 of API spend (LLMs + search) plus free credits. Pro subscriptions (ChatGPT, Gemini,
  Claude) are used for *development and research only*, because they don't provide API access for the bot. (I-07)
- **Spend discipline:** no scaling of spend until the PoC gate is passed. (I-06, I-08)
- **Local compute:** RTX 4090 (24 GB) available for development, backtesting and local open-weight models. (I-04)
- **Hosting:** GitHub Actions for the PoC (as in the Metaculus template). GCP dropped for now. (I-05)
- **Time:** ~10 h/week. (I-09)
- **Code hosting:** public GitHub repo `forecast_engine` (unlimited Actions minutes; prompts are public). Secrets only in `.env` (gitignored) and GitHub Actions secrets.
- **Metaculus bot account:** CrystalBallMcGee-bot

### Principles
- **Evaluation-first (governing rule):** no change reaches the live bot without evidence under `docs/evaluation-protocol.md`. The harness is built to be bullet-proof in method and implementation, and it gets priority over new features. (I-13)
- Measure calibration, not just accuracy, and score every change against the backtest. (I-01)
- Post-hoc calibration (Platt etc.) is an *experiment*, not a default: nostreambot found it unstable across eras. (research/04)
- Research quality and diversity is the main failure point: the worst misses happen when all models share one flawed briefing. (research/04)
- Tag every forecast with a config version and log its cost. (research/04)
- Prefer ensembles of diverse forecasters over a single "clever" one ("foxes over hedgehogs"). (I-01)
- Start from base rates and update on evidence. (I-01)
- Beware overfitting the backtest: keep a held-out question set. (I-01)

## 7. Architecture
_Agreed 2026-09-29; follows ADRs 0001–0005._

**Shape:** the Metaculus template (ADR-0001) runs on GitHub Actions (ADR-0002) and calls models through OpenRouter (ADR-0003).
Our own forecasting core sits behind a thin template adapter, so the M2 harness can replay exactly what the live bot does (ADR-0005).

```
 GitHub Actions cron (x2) / workflow_dispatch
        │
        ▼
 Template adapter (ForecastBot subclass)      live only: fetch questions, dry-run default, per-run question cap, publish
        │  QuestionSnapshot + Clock(as_of)
        ▼
 gather_research(snapshot, as_of, research_config) ─► ResearchBundle (dated items)
        │                                             live: AskNews latest + Gemini grounding
        │                                             backtest (M2): AskNews archive only, as-of guard, cache
        ▼
 forecast(snapshot, as_of, bundle, config) ─► ForecastRecord
        │   N forecasters (different vendors) → parse → median → post-process
        ▼
 Records: forecasts.jsonl + one JSON per question (prompts, outputs, research, tokens, $, CONFIG_VERSION)
        │   uploaded as workflow artifacts
        ▼
 evals/ (M2): question-set manifests · runner · scorer · reports  ── imports the core, never the adapter
```

| Component | Responsibility | Built in | Notes |
|---|---|---|---|
| Template adapter | Fetch questions, call the core, publish forecast + comment | M1 | Only place that talks to Metaculus for writes |
| `Clock` | Single source of "today" | M1 | No other code reads the system clock (protocol T5) |
| Research layer | Provider calls behind one interface, returns dated items | M1 (live), M2 (as-of guard, cache, leakage screen) | AskNews call budget guard (grant: 1,000 calls/month) |
| Forecasting core | Prompting, parsing, aggregation (median), post-processing | M1 | Pure function of its inputs + config |
| Config | Model roster, prompts, params, `CONFIG_VERSION` | M1 | One file for model IDs, verified against live OpenRouter list |
| Records | Append-only forecast log + research bundles | M1 | Cost and provenance per forecast (B-05) |
| `evals/` | Sets, runner, scorer, reports | M2 | Per `docs/evaluation-protocol.md` §7 |

**Decisions:** [0001 template](decisions/0001-start-from-metaculus-template.md) ·
[0002 hosting](decisions/0002-hosting-github-actions-public-repo.md) · [0003 OpenRouter](decisions/0003-llm-access-via-openrouter.md) ·
[0004 cost ceiling](decisions/0004-cost-ceiling-per-question.md) · [0005 core/adapter split](decisions/0005-evaluation-ready-architecture.md) · [0006 M1 configuration](decisions/0006-m1-baseline-configuration.md)

## 8. Milestones
| # | Milestone | Outcome / definition of done | Status |
|---|---|---|---|
| M0 | Research & spec | Spec sections 1–7 filled, key ADRs written | Done 2026-09-29 (§7 + ADRs 0001–0005) |
| M1 | Baseline bot | Template-level bot forecasting live on MiniBench + Fall 2026 AIB (scenario B, research/03); token usage logged | In progress: built + dry-run 2026-09-30 ($0.007/q without research); needs a research source and the GitHub repo |
| M2 | Evaluation harness | Per `docs/evaluation-protocol.md`: frozen DEV/HOLDOUT sets, as-of retrieval + leakage controls with automated checks, tested scorer, runner + reports, harness validation (A/A, leakage injection), EXP-001 baseline | Not started |
| **Gate** | **PoC go/no-go** | ≥100 resolved binary Qs post model cutoff: beats single-call baseline on Brier/log, within agreed margin of community forecast, passing config ≤ $0.50/question live, PoC within $100 + credits. Otherwise stop or rethink | Agreed |
| M3 | Better bot | Research agent, multi-model ensemble, aggregation/calibration; beats M1 in backtest | Not started |
| M4 | Compete | Running unattended in a live tournament, with monitoring and cost tracking | Not started |

## 9. Open questions
- [ ] Primary target: Metaculus AIB (bot-only, prize pool), Metaculus Cup (vs. humans), or both?
- [ ] Margin vs. community forecast for the PoC gate (set after M2 baseline) (I-08)
- [ ] Apply for Metaculus-arranged LLM credits + AskNews bot access
- [ ] Training cutoffs of candidate models vs. backtest question dates
- [ ] Does AskNews support date-restricted historical search?
- [ ] What's known about how laertes works specifically? (general top-bot methods: see research/02)
- [ ] Backtesting: how to get leakage-free historical questions and date-restricted news?
- [ ] uv vs. Poetry (Python 3.11+/Poetry for now, ADR-0001; template-vs-scratch answered in ADR-0001)
- [ ] Later: prediction-market trading from Denmark — which markets are legally accessible, and what are the tax implications?

## 10. Research log
| Date | Question | Notes | Conclusion |
|---|---|---|---|
| 2026-09-28 | Personal or work? | Private project, unrelated to UFST | Cloud-first; use the latest hosted tools |
| 2026-09-28 | What to build? | Inspired by laertes (Economist, Sept 2026) | LLM judgmental-forecasting bot for Metaculus-style questions |
| 2026-09-29 | Architecture | ADRs 0001–0005 | Template + thin adapter around our own pure core; OpenRouter; GH Actions; $0.50/q ceiling |
| 2026-09-29 | Evaluation rigour | `docs/evaluation-protocol.md` | Evaluation-first rule; threat model T1–T13; backtests decide scaffolding, live decides model choice |
| 2026-09-28 | Lessons from nostreambot | research/04 | Don't fork; borrow median-of-3, market prices, leakage screen, ops fixes |
| 2026-09-28 | Cost model | research/03 | Winning designs cost $0.30–0.80/q; $0.10 gate unrealistic; live early with cheap config |
| 2026-09-28 | Initial landscape scan | See `docs/research/00-landscape.md` | Template + tournaments exist; laertes internals not public |
