"""Spend guards for the FAST lane: OpenRouter dollars and AskNews credits. Both stop the run, never the live bot.

Both fail closed (design review point 5): the state files live in the private data repo between runs; a missing file stops the run
unless the first-run switch FASTLANE_INIT=1 is set, and an unreadable file stops it always. State is written after every question
(atomic replace), so a crash loses at most one question's spend.

Dollars: the lane runs on the EXPERIMENTS OpenRouter key (OPENROUTER_API_KEY; never OPENROUTER_LIVE_KEY, checked) with a $80 cap until
2027-01-31 (Christian, 2026-11-15). That key is shared with the tournament shadows and PC replays, so the key's own usage can't be the
stop rule; the lane's ledger is: every record's cost (OpenRouter-reported) plus Google's grounding fee at list price ($0.014 per
search query, counted even inside the free tier, the same way as EXP-008).

AskNews: Christian added a $25 wallet on 2026-11-16 (about 1,250 credits at $0.02) and asked for full coverage. The plan allows 500 a
month and 597 were used on 2026-11-16 (his usage page). The live bot comes first: a credit is taken only if, after it, the period's
total stays under plan + wallet minus a reserve of `live_per_day` credits for each remaining day of the period. A refusal is counted
(`skipped`), never silent: the runner flags the question's records and reports the count.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from evals.asknews_budget import BudgetConfig, billing_period

STATE_DIR = Path("data/fastlane")
LANE_ENDS = date(2027, 1, 31)
GOOGLE_USD_PER_QUERY = 0.014


class GuardStop(RuntimeError):
    pass


def init_allowed() -> bool:
    return os.getenv("FASTLANE_INIT") == "1"


def _read(path: Path, init_ok: bool) -> dict:
    """The state dict. Missing: {} only if `init_ok`, else GuardStop. Unreadable: GuardStop."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if init_ok:
            return {}
        raise GuardStop(f"{path} is missing: not starting without the spend state (first run only: set FASTLANE_INIT=1)") from None
    except (OSError, ValueError) as e:
        raise GuardStop(f"{path} is unreadable ({type(e).__name__}); not starting") from e
    if not isinstance(data, dict):
        raise GuardStop(f"{path} is not a JSON object; not starting")
    return data


def _write(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1), encoding="utf-8")
    os.replace(tmp, path)


@dataclass
class SpendGuard:
    cap_usd: float = 80.0
    state_path: Path = STATE_DIR / "spend.json"
    init_ok: bool = field(default_factory=init_allowed)
    run_usd: float = 0.0
    google_queries: int = 0

    def start(self, today: date) -> None:
        if today > LANE_ENDS:
            raise GuardStop(f"the FAST lane's budget period ended {LANE_ENDS}")
        live, exp = os.getenv("OPENROUTER_LIVE_KEY"), os.getenv("OPENROUTER_API_KEY")
        if live and exp and live == exp:
            raise GuardStop("OPENROUTER_API_KEY is the live key: the FAST lane must run on the experiments key only")
        st = _read(self.state_path, self.init_ok)
        if self.spent(st) >= self.cap_usd:
            raise GuardStop(f"FAST lane spend ${self.spent(st):.2f} has reached the ${self.cap_usd:g} cap")

    def spent(self, st: dict | None = None) -> float:
        st = _read(self.state_path, self.init_ok) if st is None else st
        return float(st.get("ledger_usd", 0.0)) + self.run_usd

    def add(self, usd: float, google_queries: int = 0) -> None:
        self.google_queries += google_queries
        self.run_usd += max(0.0, usd) + google_queries * GOOGLE_USD_PER_QUERY
        if self.spent() >= self.cap_usd:
            raise GuardStop(f"FAST lane spend reached ${self.cap_usd:g}")

    def save(self) -> None:
        st = _read(self.state_path, self.init_ok)
        st["ledger_usd"] = round(float(st.get("ledger_usd", 0.0)) + self.run_usd, 6)
        st["google_queries"] = int(st.get("google_queries", 0)) + self.google_queries
        self.run_usd, self.google_queries = 0.0, 0
        _write(self.state_path, st)


@dataclass
class AskNewsGuard:
    """Say yes or no to AskNews credits. `base_used` = credits used by everything else this period (the usage page, via
    ASKNEWS_PERIOD_USED); the lane adds its own count on top. Unknown or unreadable state answers no, and counts the refusal."""

    base_used: int
    cfg: BudgetConfig = field(default_factory=lambda: BudgetConfig(plan_credits=500, wallet_credits=1250, allow_overage=True))
    live_per_day: int = 6  # live bot: ~4 a day on average, more on batch days (Nov 2: 46)
    daily_cap: int = 60
    total_cap: int = 1100  # wallet-sized: 280 questions + later arms; the real stop is the period limit below
    state_path: Path = STATE_DIR / "asknews.json"
    today: date | None = None
    init_ok: bool = field(default_factory=init_allowed)
    skipped_run: int = 0

    def _day(self) -> date:
        return self.today or datetime.now(timezone.utc).date()

    def _state(self) -> dict | None:
        try:
            return _read(self.state_path, self.init_ok)
        except GuardStop:
            return None

    def used_by_lane(self) -> tuple[int, int]:
        st = self._state() or {}
        return int(st.get("total", 0)), int(st.get("by_day", {}).get(self._day().isoformat(), 0))

    def allow(self, credits: int = 1) -> bool:
        if self._state() is None:
            return False
        total, today_n = self.used_by_lane()
        if total + credits > self.total_cap or today_n + credits > self.daily_cap:
            return False
        _, end = billing_period(self._day(), self.cfg.anchor_day)
        days_left = max(1, (end - self._day()).days)
        limit = self.cfg.plan_credits + (self.cfg.wallet_credits if self.cfg.allow_overage else 0)
        return self.base_used + total + credits + self.live_per_day * days_left <= limit

    def take(self, credits: int = 1) -> None:
        """Record spend before the call (it counts even if the call fails)."""
        st = self._state()
        if st is None:
            raise GuardStop("AskNews state unreadable")
        st["total"] = int(st.get("total", 0)) + credits
        day = self._day().isoformat()
        st.setdefault("by_day", {})[day] = int(st.get("by_day", {}).get(day, 0)) + credits
        _write(self.state_path, st)

    def skip(self, n: int = 1) -> None:
        self.skipped_run += n
        st = self._state()
        if st is not None:
            st["skipped"] = int(st.get("skipped", 0)) + n
            _write(self.state_path, st)
