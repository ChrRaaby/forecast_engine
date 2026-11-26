"""Spend guards for the FAST lane: OpenRouter dollars and AskNews credits. Both stop the run, never the live bot.

Both fail closed (design review point 5): the state files live in the private data repo between runs; a missing file stops the run
unless the first-run switch FASTLANE_INIT=1 is set, and an unreadable file stops it always. State is written after every question
(atomic replace), so a crash loses at most one question's spend.

Dollars: the lane runs on the EXPERIMENTS OpenRouter key (OPENROUTER_API_KEY; never OPENROUTER_LIVE_KEY, checked) with a $80 cap until
2027-01-31 (Christian, 2026-11-15). That key is shared with the tournament shadows and PC replays, so the key's own usage can't be the
stop rule; the lane's ledger is: every record's cost (OpenRouter-reported) plus Google's grounding fee at list price ($0.014 per
search query, counted even inside the free tier, the same way as EXP-008).

AskNews: the lane wants an AskNews search for every question (Christian, 2026-11-16) but the account is shared with the live bot, the
shadow arms and experiments, which come first. The guard works from a dated reading of Christian's usage page (`asknews_usage.json`),
adds `burn_per_day` for each day since, subtracts what the lane itself took, and holds back `reserve_days` of everyone else's use.
A missing, unreadable, future, over-a-week-old or previous-period reading answers no. When the answer is no the question is skipped
and counted, so every forecast question has the same research package.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from math import ceil
from pathlib import Path

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
    """Lane ledger plus one line per experiment. The run's primary experiment (`experiment`) and the lane cap stop the run; a joined
    experiment's line (`extra_caps`) only stops that experiment's own arms (`exhausted`), the run goes on."""

    cap_usd: float = 80.0  # the whole lane
    state_path: Path = STATE_DIR / "spend.json"
    init_ok: bool = field(default_factory=init_allowed)
    experiment: str | None = None  # the experiment this run serves first; its own cap applies too
    experiment_cap_usd: float | None = None
    extra_caps: dict[str, float] = field(default_factory=dict)  # experiments that joined this run: name -> cap
    run_by: dict[str, float] = field(default_factory=dict)  # this run's spend per line (not yet saved)
    google_queries: int = 0

    @property
    def run_usd(self) -> float:
        return sum(self.run_by.values())

    def _line(self, line: str | None) -> str:
        return line or self.experiment or ""

    def _cap(self, line: str) -> float | None:
        return self.experiment_cap_usd if line == self._line(None) else self.extra_caps.get(line)

    def start(self, today: date) -> None:
        if today > LANE_ENDS:
            raise GuardStop(f"the FAST lane's budget period ended {LANE_ENDS}")
        live, exp = os.getenv("OPENROUTER_LIVE_KEY"), os.getenv("OPENROUTER_API_KEY")
        if live and exp and live == exp:
            raise GuardStop("OPENROUTER_API_KEY is the live key: the FAST lane must run on the experiments key only")
        st = _read(self.state_path, self.init_ok)
        if self.spent(st) >= self.cap_usd:
            raise GuardStop(f"FAST lane spend ${self.spent(st):.2f} has reached the ${self.cap_usd:g} cap")
        if self.experiment_cap_usd is not None and self.spent_experiment(st) >= self.experiment_cap_usd:
            raise GuardStop(f"{self.experiment} spend ${self.spent_experiment(st):.2f} has reached its ${self.experiment_cap_usd:g} cap")

    def spent(self, st: dict | None = None) -> float:
        st = _read(self.state_path, self.init_ok) if st is None else st
        return float(st.get("ledger_usd", 0.0)) + self.run_usd

    def spent_experiment(self, st: dict | None = None, line: str | None = None) -> float:
        st = _read(self.state_path, self.init_ok) if st is None else st
        name = self._line(line)
        return float((st.get("by_experiment") or {}).get(name, 0.0)) + self.run_by.get(name, 0.0)

    def exhausted(self, line: str | None) -> bool:
        """True when this line's cap is used up (a joined experiment then skips its own arms)."""
        name = self._line(line)
        cap = self._cap(name)
        return cap is not None and self.spent_experiment(line=name) >= cap

    def add(self, usd: float, google_queries: int = 0, line: str | None = None) -> None:
        name = self._line(line)
        self.google_queries += google_queries
        self.run_by[name] = self.run_by.get(name, 0.0) + max(0.0, usd) + google_queries * GOOGLE_USD_PER_QUERY
        st = _read(self.state_path, self.init_ok)
        if self.spent(st) >= self.cap_usd:
            raise GuardStop(f"FAST lane spend reached ${self.cap_usd:g}")
        if name == self._line(None) and self.experiment_cap_usd is not None and self.spent_experiment(st) >= self.experiment_cap_usd:
            raise GuardStop(f"{self.experiment} spend reached its ${self.experiment_cap_usd:g} cap")

    def save(self) -> None:
        st = _read(self.state_path, self.init_ok)
        st["ledger_usd"] = round(float(st.get("ledger_usd", 0.0)) + self.run_usd, 6)
        by = st.setdefault("by_experiment", {})
        for name, usd in self.run_by.items():
            if name:
                by[name] = round(float(by.get(name, 0.0)) + usd, 6)
        st["google_queries"] = int(st.get("google_queries", 0)) + self.google_queries
        self.run_by, self.google_queries = {}, 0
        _write(self.state_path, st)


@dataclass(frozen=True)
class AskNewsUsage:
    """One reading of the AskNews usage page."""

    as_of: date
    renews: date  # first day of the next billing period: this reading is worthless from then on
    period_used: int
    plan_credits: int
    wallet_usd: float
    usd_per_credit: float

    @classmethod
    def load(cls, path: Path) -> "AskNewsUsage | None":
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            u = cls(date.fromisoformat(d["as_of"]), date.fromisoformat(d["renews"]), int(d["period_used"]), int(d["plan_credits"]),
                    float(d["wallet_usd"]), float(d["usd_per_credit"]))
        except (OSError, ValueError, KeyError, TypeError):
            return None
        return u if u.usd_per_credit > 0 and u.wallet_usd >= 0 and u.period_used >= 0 else None

    @property
    def credits_left(self) -> int:
        return max(0, self.plan_credits - self.period_used) + int(self.wallet_usd / self.usd_per_credit)


@dataclass
class AskNewsGuard:
    """Say yes or no to AskNews credits; every reason to say no is in `why`."""

    usage_path: Path = STATE_DIR / "asknews_usage.json"
    burn_per_day: float = 42.0  # everyone else: 597 -> 891 credits from 16 to 23 Nov 2026 (usage page)
    reserve_days: int = 14
    max_age_days: int = 7
    daily_cap: int = 60
    total_cap: int = 400  # 280 calibration questions + slack; raise only with Christian's OK
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

    def why_not(self, credits: int = 1) -> str | None:
        """None = allowed, otherwise the reason."""
        st, day = self._state(), self._day()
        if st is None:
            return "AskNews state file missing or unreadable"
        u = AskNewsUsage.load(self.usage_path)
        if u is None:
            return "no readable AskNews usage reading"
        age = (day - u.as_of).days
        if age < 0:
            return f"AskNews usage reading is dated in the future ({u.as_of})"
        if age > self.max_age_days:
            return f"AskNews usage reading is {age} days old (limit {self.max_age_days})"
        if day >= u.renews:
            return f"AskNews period renewed on {u.renews}: a new reading is needed"
        total, today_n = self.used_by_lane()
        if total + credits > self.total_cap:
            return f"lane total cap of {self.total_cap} credits"
        if today_n + credits > self.daily_cap:
            return f"lane daily cap of {self.daily_cap} credits"
        since = sum(int(n) for d, n in st.get("by_day", {}).items() if date.fromisoformat(d) >= u.as_of)
        # the days since the reading count as full days, today included
        left = u.credits_left - ceil(self.burn_per_day * (age + 1)) - since - credits
        need = ceil(self.burn_per_day * self.reserve_days)
        return None if left >= need else f"only {left} credits would be left; {need} are held back for the live bot and shadows"

    def allow(self, credits: int = 1) -> bool:
        return self.why_not(credits) is None

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
