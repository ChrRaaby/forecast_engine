"""As-of AskNews archive search for backtests (B-28; protocol T2; ADR-0008).

`archive_search(q, clock, cfg, client, budget)` asks the AskNews historical archive for articles published in
[as_of − lookback, as_of], then drops anything undated, later than as_of, or from a forecast aggregator (`evals.asof`), and
returns the core's ResearchBundle. Each search costs 5 credits and goes through the budget guard first.

Only a FixedClock is accepted: a backtest that silently searched "as of now" would be the worst kind of leak.
The AskNews client is injected (tests use a fake); `SdkArchiveClient.from_env()` builds the real one.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any, Protocol

from forecast_engine.clock import Clock, FixedClock
from forecast_engine.schema import LlmCall, QuestionSnapshot, ResearchBundle, ResearchItem

from .asknews_budget import CREDITS_PER_ARCHIVE_SEARCH, AskNewsBudget
from .asof import assert_bundle_as_of, filter_items

PROVIDER = "asknews_archive"
# Bump when the way a query is built from the question changes: it changes the research, so it must change the cache key.
QUERY_VERSION = "question_text-v1"


@dataclass(frozen=True)
class ArchiveConfig:
    n_articles: int = 10
    lookback_days: int = 30
    method: str = "nl"  # natural-language query (the question text)
    strategy: str = "default"
    # Filter on publication date, the only date the returned articles carry, so the server filters on what we can verify.
    time_filter: str = "pub_date"
    # Not part of the research content, so excluded from the config hash. The live bot shares the key, so keep it slow.
    min_interval_s: float = 12.0

    def retrieval_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("min_interval_s")
        return {"provider": PROVIDER, "query_version": QUERY_VERSION, **d}

    def config_hash(self) -> str:
        blob = json.dumps(self.retrieval_dict(), sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:16]


class ArchiveClient(Protocol):
    async def search_news(self, **params: Any) -> Any: ...  # returns an object with `.as_dicts` (AskNews SearchResponse)


class SdkArchiveClient:
    """The real AskNews client. Credentials as in the live bot: ASKNEWS_CLIENT_ID/ASKNEWS_SECRET or ASKNEWS_API_KEY."""

    def __init__(self, creds: dict[str, str]) -> None:
        self._creds = creds

    @classmethod
    def from_env(cls) -> SdkArchiveClient:
        if os.getenv("ASKNEWS_CLIENT_ID") and os.getenv("ASKNEWS_SECRET"):
            return cls({"client_id": os.environ["ASKNEWS_CLIENT_ID"], "client_secret": os.environ["ASKNEWS_SECRET"]})
        if os.getenv("ASKNEWS_API_KEY"):
            return cls({"api_key": os.environ["ASKNEWS_API_KEY"]})
        raise RuntimeError("no AskNews credentials (ASKNEWS_API_KEY or ASKNEWS_CLIENT_ID/ASKNEWS_SECRET)")

    async def search_news(self, **params: Any) -> Any:
        from asknews_sdk import AsyncAskNewsSDK

        async with AsyncAskNewsSDK(**self._creds, scopes={"news"}) as ask:
            return await ask.news.search_news(**params)


_LOCK = asyncio.Lock()
_last_call = [float("-inf")]


def search_params(q: QuestionSnapshot, as_of_ts: int, cfg: ArchiveConfig) -> dict[str, Any]:
    return {
        "query": q.question_text,
        "n_articles": cfg.n_articles,
        "historical": True,
        "start_timestamp": as_of_ts - cfg.lookback_days * 86400,
        "end_timestamp": as_of_ts,
        "time_filter": cfg.time_filter,
        "method": cfg.method,
        "strategy": cfg.strategy,
        # The SDK's default is 24 h; None makes it leave the parameter out, so the window above is the only time limit we set.
        # Whether the server then applies a default of its own is checked live (`evals.asknews_check`, "lookback window used").
        "hours_back": None,
        "return_type": "dicts",
    }


async def archive_search(
    q: QuestionSnapshot, clock: Clock, cfg: ArchiveConfig, client: ArchiveClient, budget: AskNewsBudget
) -> ResearchBundle:
    if not isinstance(clock, FixedClock):
        raise TypeError("archive_search needs a FixedClock(as_of); backtests never search 'as of now'")
    as_of = clock.now()
    # Floor to whole seconds, so the server's end bound is never after as_of.
    params = search_params(q, int(as_of.timestamp() // 1), cfg)
    bundle = ResearchBundle(as_of=as_of, providers=[], items=[], calls=[])
    call = LlmCall(
        purpose="research",
        provider="asknews",
        model="news/search:historical",
        params={k: v for k, v in params.items() if k != "query"} | {"retrieval_config_hash": cfg.config_hash()},
        prompt=q.question_text,
        requested_at=as_of,
    )
    async with _LOCK:
        wait = cfg.min_interval_s - (time.monotonic() - _last_call[0])
        if wait > 0:
            await asyncio.sleep(wait)
        entry = budget.reserve(CREDITS_PER_ARCHIVE_SEARCH, question_id=q.question_id, as_of=as_of.isoformat(),
                               retrieval_config_hash=cfg.config_hash())
        call.extra["asknews_calls"] = 1
        call.extra["asknews_credits"] = CREDITS_PER_ARCHIVE_SEARCH
        call.extra["wall_time"] = budget.clock.now().isoformat()
        start = time.monotonic()
        try:
            resp = await client.search_news(**params)
        except Exception as e:  # recorded, not raised: one failed search must not sink a batch
            call.error = f"{type(e).__name__}: {e}"
        finally:
            _last_call[0] = time.monotonic()
            call.latency_s = time.monotonic() - start
        budget.record_outcome(entry, ok=call.error is None, error=call.error)

    if call.error is None:
        articles = getattr(resp, "as_dicts", None) or []
        raw = [
            ResearchItem(
                source=PROVIDER,
                text=a.summary,
                url=str(a.article_url) if a.article_url else None,
                title=a.eng_title,
                published_at=a.pub_date,
            )
            for a in articles
        ]
        kept, rejected = filter_items(raw, as_of)
        dates = sorted(i.published_at.isoformat() for i in raw if i.published_at is not None)
        call.extra.update(
            n_articles_returned=len(raw),
            n_kept=len(kept),
            rejected=Counter(r.reason for r in rejected),
            rejected_items=[asdict(r) for r in rejected],  # titles/urls/dates only, no text
            returned_pub_min=dates[0] if dates else None,
            returned_pub_max=dates[-1] if dates else None,
        )
        bundle.items = kept
        bundle.providers.append(PROVIDER)
        n_late = call.extra["rejected"].get("after_as_of", 0)
        if n_late:
            bundle.errors.append(f"{PROVIDER}: the archive returned {n_late} article(s) dated after as_of (dropped); "
                                 "is end_timestamp honoured?")
    else:
        bundle.errors.append(f"{PROVIDER} failed: {call.error}")
    bundle.calls.append(call)
    assert_bundle_as_of(bundle)  # belt and braces: the filter above must already guarantee this
    return bundle
