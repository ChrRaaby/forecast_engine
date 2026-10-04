"""AskNews credit guard for backtest archive searches (B-28; research/03 addendum b; ADR-0008).

The Pro plan includes 500 credits per billing period. AskNews itself allows up to 100k credits of overage, paid from the wallet
($5, no card, $0.02/credit ≈ 250 credits), so nothing on AskNews's side stops a runaway batch. This guard does.

Accounting, per billing period (Christian's usage page, 2026-10-03: "Sep 30, 2026 - Oct 29, 2026", so periods are anchored on
the 30th, clamped to the month's last day):
- archive spend: an append-only ledger, `data/asknews_ledger.jsonl`. Credits are written *before* each call and count whether or
  not the call succeeds (AskNews may bill failed calls, and a crash after the request must not lose the spend);
- live spend: the live bot's AskNews calls, counted from the archived run records in `data/runs/` (synced daily, so it lags);
- optionally the total shown on the AskNews usage page (`observed_used`), which is authoritative and catches the lag.

    remaining = limit − max(archive + live, observed) − max(0, live_budget − live)

`limit` is the plan allowance (plus the wallet only with allow_overage). `live_budget` is held back for the live bot for the whole
period; if live use exceeds it, the archive budget shrinks with it. The window starts one day before the period start, because
the time zone of AskNews's reset is unknown: spend on the boundary day counts against both periods (safe side).

This module reads real (wall-clock) time through a Clock: the budget is about real months, not as-of dates.
"""
from __future__ import annotations

import calendar
import json
import os
import time as _time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from forecast_engine.clock import Clock

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = ROOT / "data" / "asknews_ledger.jsonl"
DEFAULT_RUNS_DIR = ROOT / "data" / "runs"

CREDITS_PER_ARCHIVE_SEARCH = 5
CREDITS_PER_LATEST_SEARCH = 1
STALE_LOCK_S = 120


class BudgetExceeded(RuntimeError):
    pass


@dataclass(frozen=True)
class BudgetConfig:
    plan_credits: int = 500  # included per period (usage page, 2026-10-03)
    live_budget: int = 200  # held back for the live bot each period (tournament questions, 1 credit each)
    anchor_day: int = 30  # billing periods start on this day of the month
    allow_overage: bool = False
    wallet_credits: int = 250  # $5 wallet at $0.02/credit; only spendable with allow_overage


@dataclass(frozen=True)
class BudgetStatus:
    period_start: date
    period_end: date  # exclusive: the next period starts this day
    window_start: datetime  # counting starts here (one day before period_start)
    archive_used: int
    live_used: int
    observed_used: int | None
    limit: int
    live_hold: int
    remaining: int

    def summary(self) -> str:
        observed = f", AskNews page says {self.observed_used}" if self.observed_used is not None else ""
        return (
            f"AskNews period {self.period_start} to {self.period_end - timedelta(days=1)}: archive {self.archive_used} + live "
            f"{self.live_used} credits used{observed}; limit {self.limit}, held for live {self.live_hold}; "
            f"remaining for archive searches {self.remaining} credits ({self.remaining // CREDITS_PER_ARCHIVE_SEARCH} searches)"
        )


def _anchor(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def billing_period(today: date, anchor_day: int) -> tuple[date, date]:
    """(start, exclusive end) of the billing period containing `today`."""
    start = _anchor(today.year, today.month, anchor_day)
    if today < start:
        y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        start = _anchor(y, m, anchor_day)
    y, m = (start.year, start.month + 1) if start.month < 12 else (start.year + 1, 1)
    return start, _anchor(y, m, anchor_day)


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def live_credits_since(runs_dir: Path, since: datetime) -> int:
    """AskNews credits spent by the live bot since `since`, from the compact per-run logs (data/runs/**/forecasts.jsonl).

    Reads one small JSONL line per forecast (`as_of`, `asknews_calls`) instead of parsing every full record, which holds research
    text and grows by thousands of files a month (B-45 review). Counts every recorded attempt, including rate-limited retries,
    which errs on the safe side. A line without a parseable time counts too (safe side)."""
    if not runs_dir.exists():
        return 0
    total = 0
    for path in runs_dir.rglob("forecasts.jsonl"):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            at = _parse(row.get("as_of"))
            if at is None or at >= since:
                total += int(row.get("asknews_calls") or 0) * CREDITS_PER_LATEST_SEARCH
    return total


class AskNewsBudget:
    def __init__(
        self,
        clock: Clock,
        cfg: BudgetConfig = BudgetConfig(),
        ledger_path: Path = DEFAULT_LEDGER,
        runs_dir: Path | None = DEFAULT_RUNS_DIR,
        observed_used: int | None = None,
    ) -> None:
        self.clock, self.cfg, self.ledger_path, self.runs_dir = clock, cfg, Path(ledger_path), runs_dir
        self.observed_used = observed_used
        self._live: tuple[datetime, int] | None = None  # (window start, credits): run records only change on a sync

    def _entries(self) -> list[dict]:
        if not self.ledger_path.exists():
            return []
        out = []
        for line in self.ledger_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))  # a corrupt ledger must stop the batch, not be skipped
        return out

    def _append(self, entry: dict) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with self.ledger_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            f.flush()

    @contextmanager
    def _process_lock(self, timeout_s: float = 30.0):
        """Exclusive lock across processes around check + reserve (B-45 review: two batch processes could both pass check()).

        A lock file created with O_EXCL; a lock older than STALE_LOCK_S (a crashed process) is broken."""
        lock = self.ledger_path.with_name(self.ledger_path.name + ".lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        deadline = _time.monotonic() + timeout_s
        while True:
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                break
            except FileExistsError:
                try:
                    if _time.time_ns() / 1e9 - lock.stat().st_mtime > STALE_LOCK_S:
                        lock.unlink(missing_ok=True)
                        continue
                except FileNotFoundError:
                    continue
                if _time.monotonic() > deadline:
                    raise BudgetExceeded(f"couldn't get the budget lock {lock} within {timeout_s:.0f} s")
                _time.sleep(0.05)
        try:
            yield
        finally:
            lock.unlink(missing_ok=True)

    def status(self) -> BudgetStatus:
        """Budget for the current period. On the first UTC day of a new period, AskNews may not have reset yet (its reset time
        zone is unknown), so spend may still be billed to the previous period: the stricter of the two is returned (B-45 review)."""
        now = self.clock.now()
        start, _ = billing_period(now.astimezone(timezone.utc).date(), self.cfg.anchor_day)
        current = self._status_for(start)
        if now < datetime.combine(start + timedelta(days=1), time(), tzinfo=timezone.utc):
            prev_start, _ = billing_period(start - timedelta(days=1), self.cfg.anchor_day)
            previous = self._status_for(prev_start)
            if previous.remaining < current.remaining:
                return previous
        return current

    def _status_for(self, start: date) -> BudgetStatus:
        _, end = billing_period(start, self.cfg.anchor_day)
        window = datetime.combine(start - timedelta(days=1), time(), tzinfo=timezone.utc)
        window_end = datetime.combine(end, time(), tzinfo=timezone.utc) + timedelta(days=1)
        archive = 0
        for e in self._entries():
            at = _parse(e.get("at"))
            if at is None or window <= at < window_end:  # an undated entry counts: safe side
                archive += int(e.get("credits", 0))
        if self._live is None or self._live[0] != window:
            self._live = (window, live_credits_since(self.runs_dir, window) if self.runs_dir is not None else 0)
        live = self._live[1]
        used = max(archive + live, self.observed_used or 0)
        limit = self.cfg.plan_credits + (self.cfg.wallet_credits if self.cfg.allow_overage else 0)
        hold = max(0, self.cfg.live_budget - live)
        return BudgetStatus(
            period_start=start, period_end=end, window_start=window, archive_used=archive, live_used=live,
            observed_used=self.observed_used, limit=limit, live_hold=hold, remaining=max(0, limit - used - hold),
        )

    def check(self, credits: int) -> BudgetStatus:
        """Raise BudgetExceeded unless `credits` more can be spent this period. Use before a batch with its full estimate."""
        st = self.status()
        if credits > st.remaining:
            raise BudgetExceeded(f"needs {credits} credits, but only {st.remaining} remain. {st.summary()}")
        return st

    def reserve(self, credits: int, **meta) -> str:
        """Check, then record the spend before the call is made, under a cross-process lock. Returns the entry id."""
        with self._process_lock():
            self.check(credits)
            entry_id = uuid.uuid4().hex
            self._append({"id": entry_id, "at": self.clock.now().isoformat(), "credits": credits, "event": "reserve", **meta})
        return entry_id

    def record_outcome(self, entry_id: str, *, ok: bool, error: str | None = None) -> None:
        """Note how the call went. Adds no credits: the reservation already counted them."""
        self._append({"id": entry_id, "at": self.clock.now().isoformat(), "credits": 0, "event": "ok" if ok else "failed",
                      "error": error})
