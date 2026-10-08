"""All model IDs and tunable settings live here (ADR-0003: one file for model IDs).

Model IDs are checked against the live provider lists by `python tools/check_models.py`; never edit them from memory.
Any change to this file that alters forecasts must bump CONFIG_VERSION (evaluation protocol §6.5).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace

CONFIG_VERSION = "frontier-2026-10-08"


@dataclass(frozen=True)
class ForecasterSpec:
    model: str  # OpenRouter model id
    # max_tokens covers reasoning + answer. The first dry run showed DeepSeek spending all 8k tokens on reasoning and
    # returning no answer, so reasoning effort is capped and the budget raised.
    max_tokens: int = 16000
    reasoning_effort: str = "medium"  # OpenRouter unified reasoning parameter


@dataclass(frozen=True)
class ResearchConfig:
    # Tried in order; each available provider contributes items. Providers without credentials are skipped.
    providers: tuple[str, ...] = ("asknews_latest", "gemini_grounded")
    gemini_model: str = "gemini-3.5-flash-lite"  # Google AI Studio model id; grounding needs billing on the project
    # Google doesn't return a cost, so we estimate from tokens at list price (same as OpenRouter's listing for this
    # model, checked 2026-09-29). Any per-search grounding fee is NOT included; check the Google billing console.
    gemini_usd_per_m_in: float = 0.30
    gemini_usd_per_m_out: float = 2.50
    asknews_n_articles: int = 8
    # The bot grant is rate-limited (the first CI run got a 429 with 3 questions in parallel). The template waits 12 s.
    asknews_min_interval_s: float = 12.0


@dataclass(frozen=True)
class BotConfig:
    # Scenario B (research/03), ADR-0006: three cheap forecasters from different vendors, median.
    forecasters: tuple[ForecasterSpec, ...] = (
        ForecasterSpec("openai/gpt-6-luna"),
        ForecasterSpec("google/gemini-3.5-flash-lite"),
        ForecasterSpec("deepseek/deepseek-v4.1-flash"),
    )
    aggregation: str = "median"
    # Template default: keep binary forecasts in [0.01, 0.99]. Tighter capping is experiment B-11, not a default.
    binary_clip: tuple[float, float] = (0.01, 0.99)
    min_successful_forecasters: int = 2
    # Binary polarity check (2026-10-05): an auditor reads each binary member's text; members answering for NO are excluded.
    # Not a forecaster, so it doesn't share the live models' rate limits. None turns the check off.
    polarity_check_model: str | None = "openai/gpt-5-mini"
    research: ResearchConfig = field(default_factory=ResearchConfig)
    prompt_version: str = "template-2026-09-26"  # prompts ported from metac-bot-template c16d91f

    def to_dict(self) -> dict:
        return asdict(self)

    def config_hash(self) -> str:
        blob = json.dumps(self.to_dict(), sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:12]


DEFAULT_CONFIG = BotConfig()  # the cheap M1 ensemble: experiments, test runs, and the shadow on tournament questions

# ADR-0009 / ADR-0010: FutureEval and MiniBench questions use a frontier ensemble from five vendors (incl. Grok and a Chinese
# model, Christian). Roster confirmed by Christian 2026-10-07 (option A); list prices on OpenRouter that day, per M tokens in/out:
# GPT-6.1 Sol $2/$10, Claude Sonnet 5.5 $2/$10, Gemini 3.1 Pro $2/$12, Grok 4.7 $2/$6, Qwen3.8 Max $2/$6. Gemini 3.1 Pro is
# Google's newest Pro-tier model but dates from Feb 2026. Median of five, at least three must succeed.
TOURNAMENT_CONFIG = replace(
    DEFAULT_CONFIG,
    forecasters=(
        ForecasterSpec("openai/gpt-6.1-sol"),
        ForecasterSpec("anthropic/claude-sonnet-5.5"),
        ForecasterSpec("google/gemini-3.1-pro-preview"),
        ForecasterSpec("x-ai/grok-4.7"),
        ForecasterSpec("qwen/qwen3.8-max-0902"),
    ),
    min_successful_forecasters=3,
)
# Fall 2026 tournament budget for the frontier ensemble (ADR-0009), enforced in code (forecast_engine/spend.py). Once reached,
# tournament questions fall back to DEFAULT_CONFIG instead of going silent.
TOURNAMENT_SEASON = "fall-2026"
TOURNAMENT_SEASON_CAP_USD = 300.0
# Shadow-run mode (B-62): configs run unpublished on every tournament question with the live question's research, stored as
# separate records and scored as they resolve. The cheap ensemble gives the paired live comparison ADR-0009 asks for; experiments
# (EXP-005, EXP-008) add their arm here. Shadow failures never affect the live forecast.
SHADOW_CONFIGS: dict[str, BotConfig] = {"cheap": DEFAULT_CONFIG}
# Wide mode (Cup + main site, ADR-0007) uses Gemini search only, to keep AskNews credits (Pro plan, ~600/month) for tournament
# questions and backtest archive searches (Christian, 2026-10-03).
WIDE_CONFIG = replace(DEFAULT_CONFIG, research=replace(DEFAULT_CONFIG.research, providers=("gemini_grounded",)))

# Run-level safety rails (live adapter only).
DEFAULT_MAX_QUESTIONS_PER_RUN = 20
DEFAULT_MAX_RUN_COST_USD = 2.00
# Frontier forecasts cost ~$0.3/question, so a 20-question tournament run needs more headroom; the season cap is the real limit.
TOURNAMENT_MAX_RUN_COST_USD = 10.00
# Wide mode (B-40): Metaculus Cup + a few main-site questions per run, to unlock outcomes for evaluation.
DEFAULT_MAIN_SITE_PER_RUN = 2
MAIN_SITE_HORIZON_DAYS = 90  # only questions scheduled to resolve within this many days (fast feedback)
