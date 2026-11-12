"""All model IDs and tunable settings live here (ADR-0003: one file for model IDs).

Model IDs are checked against the live provider lists by `python tools/check_models.py`; never edit them from memory.
Any change to this file that alters forecasts must bump CONFIG_VERSION (evaluation protocol §6.5).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace

CONFIG_VERSION = "frontier-2026-11-07"  # EXP-002 live: dates + "resolves YES" prompt (was frontier-2026-10-08)


@dataclass(frozen=True)
class ForecasterSpec:
    model: str  # OpenRouter model id
    # max_tokens covers reasoning + answer. The first dry run showed DeepSeek spending all 8k tokens on reasoning and
    # returning no answer, so reasoning effort is capped and the budget raised.
    max_tokens: int = 16000
    reasoning_effort: str = "medium"  # OpenRouter unified reasoning parameter


FOLLOWUP_MODES = ("gaps", "against", "control")


@dataclass(frozen=True)
class FollowupConfig:
    """B-17 / EXP-008: research follow-up step (forecast_engine/research_followup.py). Shadow arms only; nothing live sets it.

    gaps: a critic lists what the brief is missing (facts, stakeholders, base-rate data, contrary evidence); one Gemini grounded
    search per gap. against: the critic lists searches for evidence against the brief's leading conclusion. control: no critic,
    the same number of searches with generic, question-independent instructions (compute-matched control, protocol §5).
    rounds >= 2 is B-17's iterative version: the critic sees the enlarged brief each round and stops by returning no gaps.
    """

    critic_mode: str = "gaps"
    critic_model: str = "openai/gpt-5-mini"  # OpenRouter id; checked by tools/check_models.py
    critic_max_tokens: int = 4000
    critic_reasoning_effort: str = "low"
    rounds: int = 1
    max_gaps: int = 5  # per round; in control mode, the number of generic searches per round
    # Hard cap on the step's own spend per question (critic + searches). A search is not started if the spend so far plus the
    # expected cost of one more search would pass it; the stop is recorded.
    max_cost_usd_per_q: float = 0.06
    # Google bills grounding per search query the model runs: $14 per 1,000 for Gemini 3.x after 5,000 free a month shared
    # across the project (ai.google.dev pricing page, checked 2026-10-26). Counted at list price, ignoring the free tier, so
    # records and the cap never understate the cost. The live research call does not count this fee (F-02).
    grounding_usd_per_query: float = 0.014
    # Expected cost of one search before any has been made in this question (afterwards: the dearest one so far).
    # About 1.5k output tokens at list price + 2 grounding queries.
    search_cost_prior_usd: float = 0.032
    prompt_version: str = "followup-2026-10-26"

    def __post_init__(self) -> None:
        if self.critic_mode not in FOLLOWUP_MODES:
            raise ValueError(f"critic_mode must be one of {FOLLOWUP_MODES}, not {self.critic_mode!r}")
        if self.rounds < 1 or self.max_gaps < 1 or self.max_cost_usd_per_q <= 0:
            raise ValueError("rounds and max_gaps must be >= 1 and max_cost_usd_per_q > 0")


@dataclass(frozen=True)
class SourceBlockConfig:
    """EXP-013 arms B and E (B-80): fetch the page(s) the question resolves on and put a short, labelled block at the top of the
    brief. Fetch safety: only plain http(s) GETs of links written in the question's resolution criteria / fine print; no cookies,
    no credentials, public addresses only, same-host redirects only, timeout and size cap. The page text is data: only the
    extractor's short block (value, its date, a verified quote <= quote_max_chars, or NOT FOUND) reaches the forecasters."""

    max_links: int = 2
    timeout_s: float = 20.0
    max_bytes: int = 2_000_000  # read at most this much of a response
    max_text_chars: int = 40_000  # page text kept (stored in the record and shown to the extractor)
    max_redirects: int = 3
    # one cheap call per question, all fetched pages together. GPT-6 Luna (in the cheap roster): $0.10/$0.50 per M, no expiry on
    # OpenRouter; replaced gpt-5-mini (expires 2026-12-11) before switch-on (Christian, 2026-11-12)
    extractor_model: str = "openai/gpt-6-luna"
    extractor_max_tokens: int = 3000
    extractor_reasoning_effort: str = "low"
    quote_max_chars: int = 300
    prompt_version: str = "exp013-source-2026-11-12"


@dataclass(frozen=True)
class BriefWriterConfig:
    """EXP-013 arm C (B-80): a stronger Gemini with Google grounding writes the brief instead of the live Flash-Lite call, under
    strict rules (a link and a date for every number, NOT FOUND instead of an estimate, the resolution source first). Model and
    thinking level chosen by Christian after the arm C pilot (tools/exp013_armc_pilot.py), 2026-11-12: 1.6 Google queries per
    call, ~$0.011 of tokens per call, ~13 s."""

    model: str = "gemini-3.8-flash"  # Google AI Studio id (Christian, 2026-11-12, after the pilot)
    thinking_level: str | None = "low"  # Gemini 3 thinkingLevel; "minimal" is not supported on 3.8 Flash
    # Recorded at the standard list price ($1.50/$7.50 per M from 2027-01-01). Until 2026-12-31 Google's intro price is
    # $0.75/$3.75, so records overstate token cost by 2x until then (safe side; no date switch).
    usd_per_m_in: float = 1.50
    usd_per_m_out: float = 7.50  # output incl. thinking
    grounding_usd_per_query: float = 0.014  # counted at list price, free tier ignored (as research_followup)
    # Cap on the call's cost per question at list price. One request can't be stopped midway, so the cap bounds the output
    # tokens (maxOutputTokens) up front; grounding queries are not under our control and are checked after the call (an
    # over-cap call is flagged in the record; the brief is still used).
    max_cost_usd_per_q: float = 0.05
    # Grounding queries assumed when sizing maxOutputTokens. 0 = the ceiling covers tokens only: at list price 3 queries ($0.042)
    # would leave ~700 output tokens under $0.05, too few for a brief with thinking. Google's free grounding allowance (5,000 a
    # month) likely covers arm C; the pilot measures it. The fee is still recorded at list price and flagged when over the cap.
    reserved_queries: int = 0
    prompt_version: str = "exp013-c-writer-2026-11-12"


@dataclass(frozen=True)
class NewsConfig:
    """EXP-013 arm D (B-80): AskNews with keywords written by a cheap model instead of the full question sentence, plus the
    historical ("news knowledge") search next to "latest news". Both parts can be switched separately. Credits per question:
    1 (keyword latest) + 5 (historical, assumed; check on the AskNews usage page before switch-on), instead of the live 1."""

    keywords: bool = True
    historical: bool = True
    keyword_model: str = "openai/gpt-6-luna"  # was gpt-5-mini (expires 2026-12-11), Christian 2026-11-12
    keyword_max_tokens: int = 2000
    keyword_reasoning_effort: str = "low"
    n_articles: int = 8
    historical_n_articles: int = 8
    historical_lookback_days: int = 150  # AskNews refuses windows over 160 days (live check 2026-11-12)
    prompt_version: str = "exp013-d-news-2026-11-12"


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
    # B-17 / EXP-008 follow-up searches on top of the gathered research. None = off (every live config). Only the shadow runner
    # applies it (main.py `_run_shadows`); gather_research() ignores it.
    followup: FollowupConfig | None = None
    # EXP-013 (B-80) research v2 steps, applied by the shadow runner only (forecast_engine/research_v2.py), never by
    # gather_research(). None = off (every live config).
    source_block: SourceBlockConfig | None = None
    brief_writer: BriefWriterConfig | None = None
    news: NewsConfig | None = None


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
    prompt_version: str = "dates-yes-live-2026-11-07"  # template c16d91f + EXP-002 (live 2026-11-07); was "template-2026-09-26"

    def to_dict(self) -> dict:
        d = asdict(self)
        for step in ("followup", "source_block", "brief_writer", "news"):
            if d["research"][step] is None:
                del d["research"][step]  # off: leave the dict, and so config_hash(), as it was before the field existed
        return d

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
# EXP-008 research desk (B-64, card v2, pre-registered at switch-on): shadow arms, each the cheap ensemble with a research
# follow-up step on top of the live question's research; arm A (baseline) is the "cheap" shadow below. Cost setting (Christian,
# 2026-11-07, after the B-70 pilot): at most 2 follow-up searches per arm, grounding fee counted at list price, cap $0.15/q.
EXP008_FOLLOWUP = {"max_gaps": 2, "max_cost_usd_per_q": 0.15}
EXP008_ARMS: dict[str, BotConfig] = {
    f"exp008-{arm}": replace(DEFAULT_CONFIG, research=replace(
        DEFAULT_CONFIG.research, followup=FollowupConfig(critic_mode=mode, **EXP008_FOLLOWUP)))
    for arm, mode in (("b-gaps", "gaps"), ("c-against", "against"), ("d-control", "control"))
}
# Shadow-run mode (B-62): configs run unpublished on every tournament question with the live question's research, stored as
# separate records and scored as they resolve. The cheap ensemble gives the paired live comparison ADR-0009 asks for; experiments
# (EXP-005, EXP-008) add their arm here. Shadow failures never affect the live forecast. EXP-008 switched on here (Christian's
# OK, 2026-11-07); to stop it, remove **EXP008_ARMS.
# EXP-002 Tier 1 (live 2026-11-07): "cheap-old-prompt" is the cheap ensemble with the previous prompt, paired with "cheap" (new
# prompt) on every question, so live can switch back if the new prompt turns out worse (review at ~150 resolved questions).
EXP002_OLD_PROMPT_SHADOW = replace(DEFAULT_CONFIG, prompt_version="template-2026-09-26-old")
SHADOW_CONFIGS: dict[str, BotConfig] = {"cheap": DEFAULT_CONFIG, "cheap-old-prompt": EXP002_OLD_PROMPT_SHADOW, **EXP008_ARMS}
# EXP-013 "research v2" (B-80): built 2026-11-12, switched OFF. Arms B-D are the cheap ensemble with one research change each, on
# top of the live question's research (baseline = the "cheap" shadow); arm E is the live frontier roster with B's source block,
# compared with the published live forecast. B and E share one fetch + extraction per question. To switch on (only with
# Christian's OK and the frozen card docs/experiments/EXP-013-research-v2.md): add **EXP013_ARMS to SHADOW_CONFIGS.
EXP013_ARMS: dict[str, BotConfig] = {
    "exp013-b-source": replace(DEFAULT_CONFIG, research=replace(DEFAULT_CONFIG.research, source_block=SourceBlockConfig())),
    "exp013-c-writer": replace(DEFAULT_CONFIG, research=replace(DEFAULT_CONFIG.research, brief_writer=BriefWriterConfig())),
    "exp013-d-news": replace(DEFAULT_CONFIG, research=replace(DEFAULT_CONFIG.research, news=NewsConfig())),
    "exp013-e-frontier-source": replace(TOURNAMENT_CONFIG, research=replace(TOURNAMENT_CONFIG.research,
                                                                            source_block=SourceBlockConfig())),
}
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
