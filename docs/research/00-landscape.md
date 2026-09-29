# 00: Landscape scan (2026-09-28)

**Question:** What exists today for LLM-based judgmental forecasting, and what do we know about the
bots we want to emulate?

## Findings
- **Metaculus Cup, Summer 2026:** an AI won for the first time. The winner was *laertes*, built by Jeffrey Liang, and bots
  also took 2nd and 5th. There were 885 forecasters and 58 questions. Liang reports <150 hours and "a couple of thousand
  dollars" on compute and data. The bot was also leading an AI-only Metaculus competition at the time.
  [Economist article, provided by Christian; [Metaculus announcement](https://www.metaculus.com/notebooks/45734/announcing-the-metaculus-cup-summer-2026-winners/);
  [Metaculus on X](https://x.com/metaculus/status/2100642327526052022); [ExoBrain summary](https://www.exobrain.co.uk/insights/ai-wins-at-forecasting)]
- **laertes internals are not public.** No write-up of its models or architecture has been found yet. Leads:
  [Liang's Metaculus profile](https://www.metaculus.com/accounts/profile/279973/questions/) and his LinkedIn (Laertes Capital LLC).
- **Techniques mentioned for top bots (Economist):** combining LLMs from different providers to research
  different parts of a question and debate the answer; paywalled or esoteric datasets (Mantic); scoring pundits' track
  records (Preseen); **backtesting with "memory-wiped" snapshots of past data** to iterate on prompts and inputs.
- **Metaculus AI Forecasting Benchmark (AIB):** recurring bot-only tournaments with a ~$58k prize pool per season
  (Spring/Summer/Fall 2026), scored with log-score-based metrics. [AIB](https://www.metaculus.com/aib/)
- **Starter kit:** [Metaculus/metac-bot-template](https://github.com/Metaculus/metac-bot-template) is written in Python 3.11+ with Poetry and
  uses the `forecasting-tools` package. It runs on GitHub Actions (every 20 min for AIB, every 2 days for the Cup). LLMs
  are accessed through OpenRouter, OpenAI, Anthropic or Perplexity, and search through AskNews or Exa. OpenRouter offers free credits
  on application.
- **Worked example:** Matthew Granade's "MWG" bot used GPT-4o, Perplexity and AskNews, with ~12 analytical sub-questions, at least 3 runs per question,
  a variance check and a meta-synthesis step. It cost under $1k for ~500 questions per quarter.
  [Faint Signals](https://faintsignals.substack.com/p/building-an-ai-prediction-bot)
- **Commercial players:** Mantic (UK, [raised $25M](https://techstartups.com/2026/09/18/british-ai-startup-mantic-raises-25m-to-build-superhuman-ai-forecasting-after-metaculus-win/)),
  FutureSearch, Preseen.

## Conclusion (provisional)
Starting from the Metaculus template is a cheap way to get M1 live fast. The differentiation will come from
research quality, ensembling/aggregation, calibration, and a proper backtesting loop.

## Next research questions
1. Deep dive on top AIB bots' published methods (Metaculus write-ups, papers, forum posts).
2. Academic literature on LLM forecasting (ensembling, extremizing, retrieval, known failure modes).
3. Leakage-free backtesting: datasets of resolved questions, date-restricted news search, model cutoffs.
4. Cost model: tokens per question × questions per season × model prices.
5. Current and upcoming tournament calendar and rules (Fall 2026 AIB and the [Metaculus Cup Fall 2026](https://www.metaculus.com/tournament/metaculus-cup-fall-2026/)).
