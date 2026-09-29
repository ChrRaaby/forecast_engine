"""The single source of "today" (ADR-0005, evaluation protocol T5).

No other module may call datetime.now()/date.today(). Live runs use SystemClock; backtests use FixedClock(as_of).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    """Wall-clock time in UTC. Live mode only."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


@dataclass(frozen=True)
class FixedClock:
    """A frozen point in time, e.g. a backtest question's as-of timestamp."""

    as_of: datetime

    def __post_init__(self) -> None:
        if self.as_of.tzinfo is None:
            raise ValueError("FixedClock needs a timezone-aware datetime")

    def now(self) -> datetime:
        return self.as_of


def today_str(clock: Clock) -> str:
    return clock.now().strftime("%Y-%m-%d")
