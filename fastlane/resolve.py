"""Resolve FAST questions once they close; void the ones that never resolve; report counts.

Polymarket: a market is resolved when it is closed and its YES price is 0 or 1 (>= 0.99 / <= 0.01); the price is read ONLY here,
after the fact, to learn the outcome. Series questions resolve from their feed (series.resolve) and give a number.
A question not resolved `VOID_AFTER_DAYS` after it closed is voided (a disputed or stuck market, a feed that lost the day) and
is excluded from every comparison; voids are counted, never dropped quietly.
`counts` prints counts only. A score table waits for the paired log-score analysis (EXP-017 card): a raw Brier figure on a
few dozen questions invites over-reading.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import series
from .types import GetJson

GAMMA = "https://gamma-api.polymarket.com"
DIR = Path("data/fastlane")
VOID_AFTER_DAYS = {"polymarket": 30, "series": 10}


def polymarket_outcome(get: GetJson, market_id: str) -> int | None:
    m = get(f"{GAMMA}/markets/{market_id}", None)
    if not m or not m.get("closed"):
        return None
    prices = m.get("outcomePrices")
    prices = json.loads(prices) if isinstance(prices, str) else prices
    outcomes = m.get("outcomes")
    outcomes = json.loads(outcomes) if isinstance(outcomes, str) else outcomes
    if not prices or not outcomes or [str(o).lower() for o in outcomes[:2]] != ["yes", "no"]:
        return None
    yes = float(prices[0])
    return 1 if yes >= 0.99 else 0 if yes <= 0.01 else None


def resolve_pick(get: GetJson, pick: dict[str, Any], now: datetime) -> float | None:
    if pick["source"] == "polymarket":
        return polymarket_outcome(get, pick["meta"]["market_id"])
    return series.resolve(get, pick["meta"], now)


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.exists() else []


def update(get: GetJson, now: datetime, root: Path = DIR) -> dict[str, int]:
    picks, out_path = _rows(root / "picks.jsonl"), root / "outcomes.jsonl"
    done = {r["key"] for r in _rows(out_path)}
    new, n_open, n_void = [], 0, 0
    for pick in picks:
        if pick["key"] in done:
            continue
        closes = datetime.fromisoformat(pick["closes"])
        if closes > now:
            n_open += 1
            continue
        try:
            value = resolve_pick(get, pick, now)
        except Exception:
            value = None
        base = {"key": pick["key"], "question_id": pick["question_id"], "type": pick.get("type", "binary"), "at": now.isoformat()}
        if value is not None:
            new.append({**base, "outcome": value, "void": False})
        elif now > closes + timedelta(days=VOID_AFTER_DAYS[pick["source"]]):
            new.append({**base, "outcome": None, "void": True})
            n_void += 1
        else:
            n_open += 1
    if new:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("a", encoding="utf-8") as f:
            for r in new:
                f.write(json.dumps(r) + "\n")
    return {"resolved": len(new) - n_void, "voided": n_void, "open": n_open, "resolved_total": len(done) + len(new)}


def counts(root: Path = DIR) -> dict[str, Any]:
    picks, outs = _rows(root / "picks.jsonl"), {r["key"]: r for r in _rows(root / "outcomes.jsonl")}
    by_source: dict[str, dict[str, int]] = {}
    for p in picks:
        s = by_source.setdefault(p["source"], {"picked": 0, "resolved": 0, "voided": 0, "open": 0})
        s["picked"] += 1
        o = outs.get(p["key"])
        s["voided" if o and o["void"] else "resolved" if o else "open"] += 1
    arms: dict[str, int] = {}
    for rec in (root / "forecasts").glob("*-fast-*/q*.json"):
        arm = rec.parent.name.split("-fast-", 1)[1]
        arms[arm] = arms.get(arm, 0) + 1
    ask = {}
    try:
        ask = json.loads((root / "asknews.json").read_text(encoding="utf-8"))
        ask = {k: ask.get(k) for k in ("total", "skipped")}
    except (OSError, ValueError):
        pass
    spend = {}
    try:
        st = json.loads((root / "spend.json").read_text(encoding="utf-8"))
        spend = {"usd": st.get("ledger_usd"), "google_queries": st.get("google_queries")}
    except (OSError, ValueError):
        pass
    return {"by_source": by_source, "records_per_arm": arms, "asknews": ask, "spend": spend}
