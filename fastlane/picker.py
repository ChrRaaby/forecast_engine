"""Source A: Polymarket markets that close in 3-7 days, outside sports, with real liquidity.

The market's YES price is read once, at pick time, for one purpose: markets priced under 10% or over 90% are dropped (they teach
the screen almost nothing). It is stored in a private file for the after-the-fact market comparison and never enters a question,
a prompt or a record. Beyond that filter, selection ignores price. Within an event, markets are picked by a seeded hash, at most `per_event` of them; across events, by the
same hash, at most `max_picks` per run. The Gamma API lists events (with their markets); `exclude_tag_id` drops sports.
Read-only, public, keyless; about 10 requests per run, far under Polymarket's published limits.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from evals.markets import Market, market_from_polymarket, parse_time
from forecast_engine.schema import QuestionSnapshot

from .types import POLYMARKET_ID_BASE, FastQuestion, GetJson

GAMMA = "https://gamma-api.polymarket.com"
FALLBACK_SPORTS_TAG_ID = "1"  # checked 2026-11-16 (GET /tags/slug/sports)
PAGE = 100
MAX_PAGES = 20  # the events endpoint refuses offsets above ~2,100 (HTTP 422)
MIN_LIQUIDITY_USD = 5_000.0
HORIZON_DAYS = (3.0, 7.0)
RULES_MAX_CHARS = 3_000


@dataclass(frozen=True)
class PickerConfig:
    min_liquidity_usd: float = MIN_LIQUIDITY_USD
    horizon_days: tuple[float, float] = HORIZON_DAYS
    per_event: int = 1
    max_picks: int = 12
    salt: str = "fastlane-1"
    price_band: tuple[float, float] = (0.10, 0.90)
    # Price-strike ladders on crypto are what source B already makes from the feed, so source A leaves them out.
    exclude_tags: tuple[str, ...] = ("crypto", "bitcoin", "ethereum", "solana", "xrp", "crypto-prices")
    # Families with many near-identical events: at most this many questions per run, so one family cannot fill the sample.
    tag_caps: tuple[tuple[str, int], ...] = (("tweets-markets", 2), ("equities", 3), ("recurring", 4), ("rotten-tomatoes", 1))


def sports_tag_id(get: GetJson) -> str:
    try:
        tag = get(f"{GAMMA}/tags/slug/sports", None)
        return str((tag or {}).get("id") or FALLBACK_SPORTS_TAG_ID)
    except Exception:
        return FALLBACK_SPORTS_TAG_ID


def fetch_events(get: GetJson, now: datetime, cfg: PickerConfig = PickerConfig()) -> list[dict[str, Any]]:
    lo, hi = (now + timedelta(days=d) for d in cfg.horizon_days)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    exclude = sports_tag_id(get)
    events: list[dict[str, Any]] = []
    for page in range(MAX_PAGES):
        batch = get(f"{GAMMA}/events", {"closed": "false", "active": "true", "limit": PAGE, "offset": page * PAGE,
                                         "end_date_min": lo.strftime(fmt), "end_date_max": hi.strftime(fmt),
                                         "exclude_tag_id": exclude})
        if not batch:
            break
        events.extend(batch)
        if len(batch) < PAGE:
            break
    return events


def _liquidity(m: dict[str, Any]) -> float:
    try:
        return float(m.get("liquidityNum") if m.get("liquidityNum") is not None else m.get("liquidity") or 0)
    except (TypeError, ValueError):
        return 0.0


def _rank(key: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{key}".encode()).hexdigest()


def yes_price(raw: dict[str, Any]) -> float | None:
    """The market's YES price from the Gamma payload, or None if it is not there."""
    try:
        prices = raw.get("outcomePrices")
        prices = json.loads(prices) if isinstance(prices, str) else prices
        return float(prices[0])
    except (TypeError, ValueError, IndexError, KeyError):
        return None


def eligible(events: Iterable[dict[str, Any]], now: datetime, cfg: PickerConfig = PickerConfig()) -> list[tuple[Market, dict[str, Any], float]]:
    """(market, event, yes price) that are open, binary Yes/No, liquid, priced 10-90%, and end inside the horizon."""
    lo, hi = (now + timedelta(days=d) for d in cfg.horizon_days)
    out = []
    for ev in events:
        if {t.get("slug") for t in ev.get("tags") or []} & set(cfg.exclude_tags):
            continue
        for raw in ev.get("markets") or []:
            if raw.get("closed") or raw.get("active") is False or raw.get("archived"):
                continue
            if _liquidity(raw) < cfg.min_liquidity_usd:
                continue
            m = market_from_polymarket(raw, ev)
            if m is None or m.end is None or not (lo <= m.end <= hi):
                continue
            p = yes_price(raw)
            if p is None or not (cfg.price_band[0] <= p <= cfg.price_band[1]):
                continue
            out.append((m, ev, p))
    return out


def to_question(m: Market, ev: dict[str, Any], now: datetime, price: float | None = None) -> FastQuestion:
    rules = (m.rules or "")[:RULES_MAX_CHARS]
    q = QuestionSnapshot(
        question_id=POLYMARKET_ID_BASE + int(m.market_id), post_id=None, question_type="binary", question_text=m.title,
        background_info="", resolution_criteria=rules, fine_print="", page_url=m.url, tournaments=("fastlane",),
        open_time=now, scheduled_resolution_time=m.end)
    return FastQuestion(key=f"pm:{m.market_id}", source="polymarket", snapshot=q, closes=m.end,
                        meta={"market_id": m.market_id, "event_slug": ev.get("slug"), "url": m.url,
                              "tags": [t.get("slug") for t in ev.get("tags") or []][:6]}, price_at_pick=price)


def pick(events: Iterable[dict[str, Any]], now: datetime, already: set[str] = frozenset(),
         cfg: PickerConfig = PickerConfig()) -> list[FastQuestion]:
    """Seeded-hash sample: at most `per_event` per event, at most `max_picks` in all, none picked before."""
    pairs = [(m, ev, p) for m, ev, p in eligible(events, now, cfg) if f"pm:{m.market_id}" not in already]
    pairs.sort(key=lambda p: _rank(p[0].market_id, cfg.salt))
    per_event: dict[str, int] = {}
    per_tag: dict[str, int] = {}
    caps = dict(cfg.tag_caps)
    picked: list[tuple[Market, dict[str, Any], float]] = []
    for m, ev, p in pairs:
        ek = str(ev.get("id") or ev.get("slug"))
        if per_event.get(ek, 0) >= cfg.per_event:
            continue
        capped = [t for t in {x.get("slug") for x in ev.get("tags") or []} if t in caps]
        if any(per_tag.get(t, 0) >= caps[t] for t in capped):
            continue
        for t in capped:
            per_tag[t] = per_tag.get(t, 0) + 1
        per_event[ek] = per_event.get(ek, 0) + 1
        picked.append((m, ev, p))
        if len(picked) >= cfg.max_picks:
            break
    return [to_question(m, ev, now, p) for m, ev, p in picked]
