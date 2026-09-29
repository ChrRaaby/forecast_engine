# 0006: M1 baseline configuration

- **Status:** Accepted (Christian, 2026-09-29: roster and research plan; built 2026-09-30)
- **Date:** 2026-09-30

## Context
M1 is the cheap baseline every later change is measured against (scenario B, research/03; ceiling ADR-0004). It follows
ADR-0001 (template), ADR-0003 (OpenRouter) and ADR-0005 (own core behind a thin adapter). Model IDs were checked against the
live OpenRouter list on 2026-09-29 (`tools/check_models.py`).

## Options considered
**Third forecaster** (OpenAI and Google budget tiers were clear picks):
1. **Anthropic Claude Haiku 4.5** ($1/$5 per M tokens, released 2025-10): keeps the three big labs. Cons: a year old (R-11),
   about 60% of the model bill.
2. **DeepSeek V4.1 Flash** ($0.30/$1.20, released 2026-09): recent, ~4× cheaper, still a different vendor and training pipeline.

**Research** (no AskNews keys yet):
1. Gemini grounded search on a free Google AI Studio key.
2. OpenRouter web search (paid, ~$0.02/question).
3. Wait for AskNews.

**Output parsing**: the template's second "parser" LLM call vs. deterministic regex parsing.

## Decision
- **Forecasters:** `openai/gpt-6-luna`, `google/gemini-3.5-flash-lite`, `deepseek/deepseek-v4.1-flash`; aggregated by the **median**
  (per option for multiple choice, per declared percentile for numeric). Reasoning effort "medium", max 16k tokens: the first dry run
  showed DeepSeek spending an 8k budget entirely on reasoning. At least 2 of 3 must succeed. Anthropic joins with Sonnet once credits arrive.
- **Prompts:** the template's prompts (c16d91f) verbatim, except "today" comes from the Clock.
- **Binary clip:** [0.01, 0.99], the template default. Tighter capping is experiment B-11.
- **Parsing:** regexes, no parser LLM. Free and reproducible. Ambiguous numeric values (magnitude words such as "million", "k") are
  rejected rather than guessed (R-17). Multiple-choice answers that copy the prompt's "Option_A" placeholders are mapped by position.
  Numeric ties are allowed and broken by 1e-6 of the question range at submission.
- **Research:** AskNews latest-news (1 call/question) when keys exist, plus Gemini grounded search. **The bot refuses to publish
  without research.**
- **Question types:** binary, multiple choice, numeric, discrete. Date and conditional questions are skipped (AIB doesn't use them).
- **Run rails:** dry-run by default (`--publish` to post), at most 20 questions and $2 per run, tournament cron gated by the repo variable
  `LIVE_ENABLED`.
- **Template files dropped:** `main_with_no_framework.py`, `integrations/`, the review-bot workflow and skill, and the Metaculus Cup
  workflow (out of scope for M1). Reference copies are in `upstream/`.

## Consequences
- Measured cost on 8 bot-testing-area questions without research: **$0.007/question** (range $0.004–0.010), about 70× under the ceiling.
  Research will add input tokens; re-measure once it works (B-10).
- **Research:** the free Gemini key returned "quota exceeded" for grounded search while plain calls worked. After Christian enabled
  billing on the Google project (2026-09-29), grounded search works. Gemini now costs money: the bot records a token-based estimate at
  list price, but **any per-search grounding fee is not in the records**; check it in the Google billing console after the first runs.
- Measured with research (config `m1-baseline-2026-09-30c`, same 8 questions): **$0.008/question** mean, of which Gemini research
  ~$0.0015 (token estimate).
- Regex parsing will occasionally drop a forecaster; parse failures are visible in the records, so their rate can be measured.
