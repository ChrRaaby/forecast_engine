# 02: LLM forecasting literature (reading list, 2026-09-28)

**Question:** What does the evidence say actually makes an LLM forecasting bot good, and what traps should we avoid?

## Start here (practical, bot-builder oriented)
1. **AI Forecasting in 2026: What 11 Analyses Say** (EA Forum / LessWrong). Synthesises Metaculus' own benchmark
   analyses. https://forum.effectivealtruism.org/posts/Spyz3wESZu2eeqhDj/ai-forecasting-in-2026-what-11-analyses-say
2. **Metaculus: Bot Advice Fall 2025**. What the tournament winners did. https://www.metaculus.com/notebooks/43357/bot-advice-fall-2025/
3. **AIA Forecaster: Technical Report** (2025). A strong system with ablations: agentic search, a multi-agent supervisor
   and calibration. https://arxiv.org/abs/2511.07678

## Foundational papers
4. Halawi et al., **Approaching Human-Level Forecasting with Language Models** (NeurIPS 2024). Retrieval + reasoning
   + aggregation, near crowd level. Code is available. https://arxiv.org/abs/2402.18563 · https://github.com/dannyallover/llm_forecasting
5. Schoenegger et al., **Wisdom of the Silicon Crowd** (Science Advances 2024). An ensemble of 12 LLMs rivals a human crowd.
   https://www.science.org/doi/10.1126/sciadv.adp1528
6. Karger et al., **ForecastBench** + FRI's parity analysis. A dynamic, leakage-resistant benchmark.
   https://www.forecastbench.org/ · https://forecastingresearch.substack.com/p/ai-models-have-likely-reached-parity

## Evaluation traps (read before building M2)
7. Paleka et al., **Pitfalls in Evaluating Language Model Forecasters** (ICLR 2026). Covers temporal leakage, and why
   backtests flatter bots. https://arxiv.org/abs/2506.00723

## Later / optional (training-based, beyond the PoC)
8. Turtel et al., **LLMs Can Teach Themselves to Better Predict the Future** (2025) and **Outcome-based RL to Predict
   the Future** (2025). Fine-tuning on resolved outcomes, relevant if we ever use the 4090 for training.
   https://arxiv.org/abs/2502.05253 · https://arxiv.org/html/2505.17989

## Findings so far (from #1, to be verified against the primary sources)
- **Frontier model choice is the biggest single lever.** Simple one-shot frontier bots have placed top-5.
- **Ensembling:** most Fall 2025 winners aggregated multiple forecasts, typically 3–7 diverse models.
- **Iterative/agentic search** clearly beats one-shot retrieval. Research *breadth* matters more than search provider.
- **Post-hoc calibration** (Platt scaling) gives small but consistent Brier gains. **Capping extremes** correlates with winning.
- **Automated prompt engineering** gains did not replicate on strong models.
- **Pros vs. bots:** pros led clearly through 2025 (9–20 peer-score points). In **Spring 2026 the pro lead shrank to 1.25
  points and was no longer statistically significant** (95% CI −2.4 to 4.9). Source: https://github.com/Metaculus/metaculus/pull/5205

## Implications for forecast_engine
- The biggest levers (frontier reasoning models × ensemble × agentic search) are also the most expensive,
  which is in direct tension with the $100 PoC budget. **A cost model per question is needed before M1.**
- Calibration and capping are cheap wins. Build them into the pipeline from day one.
- Treat backtest results with suspicion (#7). A held-out set and date-restricted retrieval are non-negotiable.
