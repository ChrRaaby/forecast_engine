"""The backtest research path: cache → AskNews archive (budget + date guard) → cache, then the leakage screen (ADR-0008).

    bundle = await gather_research_as_of(q, FixedClock(as_of), cfg, client=..., budget=..., cache=...)
    verdict = await screen_cached(q, bundle, cfg.config_hash(), llm=..., screen_cfg=..., cache=...)
    if verdict.excluded: count it and skip the question

A cache hit costs nothing and never touches the budget. Bundles from failed searches are returned (so the run can record the
error) but not cached, so a rerun retries them.
"""
from __future__ import annotations

from forecast_engine.clock import FixedClock
from forecast_engine.llm import LlmClient
from forecast_engine.schema import QuestionSnapshot, ResearchBundle

from .asknews_archive import ArchiveClient, ArchiveConfig, archive_search
from .asknews_budget import AskNewsBudget
from .leakage_screen import ScreenConfig, ScreenVerdict, screen_bundle
from .research_cache import ResearchCache, cache_key


async def gather_research_as_of(
    q: QuestionSnapshot, clock: FixedClock, cfg: ArchiveConfig, *, client: ArchiveClient, budget: AskNewsBudget,
    cache: ResearchCache,
) -> ResearchBundle:
    if not isinstance(clock, FixedClock):
        raise TypeError("backtest research needs a FixedClock(as_of)")
    as_of = clock.now()
    rhash = cfg.config_hash()
    cached = cache.get(q.question_id, as_of, rhash)
    if cached is not None:
        return cached
    bundle = await archive_search(q, clock, cfg, client, budget)
    if not any(c.error for c in bundle.calls):
        cache.put(q.question_id, as_of, rhash, bundle)
    return bundle


async def screen_cached(
    q: QuestionSnapshot, bundle: ResearchBundle, retrieval_config_hash: str, *, llm: LlmClient, screen_cfg: ScreenConfig,
    cache: ResearchCache,
) -> ScreenVerdict:
    """Screen a bundle once per screen config; reuse the stored verdict afterwards. Failed screens are not stored."""
    key = cache_key(q.question_id, bundle.as_of, retrieval_config_hash)
    shash = screen_cfg.config_hash()
    stored = cache.get_screen(key, shash)
    if stored is not None:
        return ScreenVerdict(status=stored["status"], flagged_items=stored["flagged_items"], reason=stored["reason"])
    verdict = await screen_bundle(q, bundle, llm, screen_cfg)
    if verdict.status != "screen_failed":
        cache.put_screen(key, shash, verdict.to_dict())
    return verdict
