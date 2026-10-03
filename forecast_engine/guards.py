"""Reliability guards (B-09, R-17, R-18). Pure functions: they never change what the models see.

- `forecastable`: only open, unresolved questions whose close time hasn't passed.
- `clock_drift_days`: our clock vs. a trusted server Date header (wrong-"today" guard).
- `numeric_flags`: warnings for likely unit/magnitude problems; recorded and shown on the monitor, not blocking.
- `check_mc_options`: the forecast's options must be exactly the question's options, or we refuse to publish.
"""
from __future__ import annotations

from datetime import datetime
from email.utils import parsedate_to_datetime

MEDIAN_RANGE_FACTOR = 1.0  # flag a numeric median more than one question-range outside the bounds
MEMBER_RATIO_FLAG = 10.0  # flag when forecasters' medians differ by more than 10x (classic units bug)


class GuardError(RuntimeError):
    pass


def forecastable(state: str | None, close_time: datetime | None, resolution: str | None, now: datetime) -> tuple[bool, str]:
    """(ok, reason). `state` is the Metaculus status string ("open", "closed", ...)."""
    if state is not None and state != "open":
        return False, f"status is {state!r}, not open"
    if resolution is not None:
        return False, f"already resolved ({resolution})"
    if close_time is not None and close_time <= now:
        return False, f"closed at {close_time.isoformat()}"
    return True, ""


def clock_drift_days(now: datetime, server_date_header: str) -> float:
    server = parsedate_to_datetime(server_date_header)
    return abs((now - server).total_seconds()) / 86400


def _median(points: list[tuple[float, float]]) -> float | None:
    """Midpoint estimate from declared percentiles (mean of P40 and P60, else the nearest to 0.5)."""
    by = dict(points)
    if 0.4 in by and 0.6 in by:
        return (by[0.4] + by[0.6]) / 2
    return min(points, key=lambda p: abs(p[0] - 0.5))[1] if points else None


def numeric_flags(
    aggregate: list[tuple[float, float]],
    members: list[list[tuple[float, float]]],
    lower: float | None,
    upper: float | None,
) -> list[str]:
    flags: list[str] = []
    med = _median(aggregate)
    if med is not None and lower is not None and upper is not None:
        span = upper - lower
        if med < lower - MEDIAN_RANGE_FACTOR * span or med > upper + MEDIAN_RANGE_FACTOR * span:
            flags.append(f"median {med:g} is far outside the question range [{lower:g}, {upper:g}]")
    meds = [m for m in (_median(p) for p in members) if m is not None]
    pos = [m for m in meds if m > 0]
    if len(pos) >= 2 and len(pos) == len(meds) and max(pos) / min(pos) > MEMBER_RATIO_FLAG:
        flags.append(f"forecasters' medians differ by more than {MEMBER_RATIO_FLAG:g}x ({min(pos):g} vs {max(pos):g})")
    return flags


def check_mc_options(aggregate: dict[str, float], options: list[str] | tuple[str, ...]) -> None:
    if set(aggregate) != set(options) or len(aggregate) != len(options):
        raise GuardError(f"forecast options {sorted(aggregate)} don't match the question's options {sorted(options)}")
