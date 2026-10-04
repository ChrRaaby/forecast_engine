"""As-of guard for backtest research (evaluation protocol T2, T7; ADR-0008).

Every research item used in a backtest must carry a timezone-aware publication date at or before as_of, and must not come from
a forecast aggregator or prediction market (their numbers are the crowd's forecast, and may be later than as_of).

- `filter_items` is used by retrieval: it drops offending items and says why, so the drops can be counted.
- `assert_bundle_as_of` is the hard check: it raises on any offending item. The research cache runs it on every write and read.

Limitation: AskNews gives one `pub_date` per article, with no update or crawl date. A story edited after as_of, or a summary
written later, passes this check. That residual is what the LLM leakage screen (T3, `evals/leakage_screen.py`) is for.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable
from urllib.parse import urlparse

from forecast_engine.schema import ResearchBundle, ResearchItem

# Forecast aggregators, prediction markets and betting exchanges (T7). Subdomains match too.
AGGREGATOR_DOMAINS = (
    "metaculus.com",
    "polymarket.com",
    "manifold.markets",
    "kalshi.com",
    "predictit.org",
    "gjopen.com",
    "goodjudgment.com",
    "insightprediction.com",
    "smarkets.com",
    "betfair.com",
    "oddschecker.com",
    "electionbettingodds.com",
    "metaforecast.org",
    "predictionbook.com",
)


class LeakageError(RuntimeError):
    pass


@dataclass(frozen=True)
class Rejection:
    """Why an item was dropped. Deliberately holds no article text (the code repo is public)."""

    reason: str  # "undated" | "naive_date" | "after_as_of" | "forecast_aggregator"
    title: str | None
    url: str | None
    published_at: str | None


def _require_aware(as_of: datetime) -> None:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")


def is_aggregator(url: str | None) -> bool:
    if not url:
        return False
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in AGGREGATOR_DOMAINS)


def check_item(item: ResearchItem, as_of: datetime) -> str | None:
    """The reason the item may not be used at as_of, or None if it may."""
    _require_aware(as_of)
    if item.published_at is None:
        return "undated"
    if item.published_at.tzinfo is None:
        return "naive_date"
    if item.published_at > as_of:
        return "after_as_of"
    if is_aggregator(item.url):
        return "forecast_aggregator"
    return None


def filter_items(items: Iterable[ResearchItem], as_of: datetime) -> tuple[list[ResearchItem], list[Rejection]]:
    kept: list[ResearchItem] = []
    rejected: list[Rejection] = []
    for item in items:
        reason = check_item(item, as_of)
        if reason is None:
            kept.append(item)
        else:
            date = item.published_at.isoformat() if item.published_at else None
            rejected.append(Rejection(reason=reason, title=item.title, url=item.url, published_at=date))
    return kept, rejected


def assert_bundle_as_of(bundle: ResearchBundle, as_of: datetime | None = None) -> None:
    """Raise LeakageError unless every item in the bundle may be used at as_of (default: the bundle's own as_of)."""
    as_of = as_of or bundle.as_of
    _require_aware(as_of)
    if bundle.as_of != as_of:
        raise LeakageError(f"bundle is as of {bundle.as_of.isoformat()}, expected {as_of.isoformat()}")
    _, rejected = filter_items(bundle.items, as_of)
    if rejected:
        detail = ", ".join(f"{r.reason} ({r.published_at or 'no date'}, {r.url or 'no url'})" for r in rejected[:5])
        raise LeakageError(f"{len(rejected)} research item(s) not allowed as of {as_of.isoformat()}: {detail}")
