"""Release-date leakage check for live-replay experiments (protocol T1; ADR-0009 amendment 2026-10-07).

A model may re-forecast a stored live record only if it was released at least MARGIN_DAYS before the record's as_of (the moment
the live forecast and its frozen research were made). Such a model can't know anything after as_of, and the outcome comes later
still. (A stricter "released before the question opened" rule was considered and rejected: it drops long-running main-site and
Cup questions for no gain.)

Release dates are the OpenRouter listing dates ("created", checked on 2026-10-07). A model missing from the table can't be used.
Replace a listing date with the vendor's announcement date when it is verified to be earlier.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

MARGIN_DAYS = 1  # release must be at least this many days before as_of (listing dates are day-resolution)

MODEL_RELEASE_DATES: dict[str, date] = {
    # frontier candidates (ADR-0009)
    "openai/gpt-6.1-sol": date(2026, 9, 29),
    "anthropic/claude-sonnet-5.5": date(2026, 9, 28),
    "x-ai/grok-4.7": date(2026, 9, 21),
    "qwen/qwen3.8-max-0902": date(2026, 9, 3),
    "google/gemini-3.1-pro-preview": date(2026, 2, 19),
    "deepseek/deepseek-v4-pro": date(2026, 4, 24),
    # live cheap roster (ADR-0006)
    "openai/gpt-6-luna": date(2026, 9, 22),
    "google/gemini-3.5-flash-lite": date(2026, 7, 21),
    "deepseek/deepseek-v4.1-flash": date(2026, 9, 10),
}


class IneligiblePair(ValueError):
    """The model may know more than the live forecaster could have at as_of (or its release date is unknown)."""


def _as_date(x: date | datetime | str) -> date:
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    return datetime.fromisoformat(str(x).replace("Z", "+00:00")).date()


def check_eligible(model: str, as_of: date | datetime | str | None) -> None:
    """Raise IneligiblePair unless `model` was released at least MARGIN_DAYS before the record's as_of."""
    if model not in MODEL_RELEASE_DATES:
        raise IneligiblePair(f"{model}: release date unknown; add it to MODEL_RELEASE_DATES after checking")
    if as_of is None:
        raise IneligiblePair(f"{model}: record has no as_of, so eligibility can't be checked")
    released, cutoff = MODEL_RELEASE_DATES[model], _as_date(as_of) - timedelta(days=MARGIN_DAYS)
    if released > cutoff:
        raise IneligiblePair(f"{model} released {released} is not at least {MARGIN_DAYS} day(s) before as_of {_as_date(as_of)}")


def eligible(model: str, as_of: date | datetime | str | None) -> bool:
    try:
        check_eligible(model, as_of)
    except IneligiblePair:
        return False
    return True
