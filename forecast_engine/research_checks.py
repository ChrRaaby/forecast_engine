"""Research date sanity checks (B-51a). Plain code, no model calls; flags are stored in the record, never shown to the forecasters.

`check_bundle` marks research items whose dates look wrong: a weekday that doesn't fit its date, a publish date after the forecast's
as_of, a weekday+date phrase after as_of (stale text from another year, or a leak), or a long-finished marketing/fiscal year
(soft flag). It never raises and never copies article text: `detail` holds only the matched phrase (AskNews text is licensed).
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from .schema import ResearchBundle

_MAX_DETAIL = 60
_MAX_FLAGS_PER_ITEM = 20

_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}

_WD = r"(?P<wd>(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]{0,6})\.?"
_MO = r"(?P<mo>(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]{0,6})\.?"
_DAY = r"(?P<d>\d{1,2})(?:st|nd|rd|th)?"
_YEAR = r"(?:,?\s+(?P<y>20\d{2})(?!\d))?"
# "Friday, October 4, 2026" / "Fri, Oct 4"  and  "Friday 4 October 2026" / "Friday, the 4th of October"
_MD_FIRST = re.compile(rf"\b{_WD},?\s+{_MO}\s+{_DAY}(?!\d){_YEAR}")
_DAY_FIRST = re.compile(rf"\b{_WD},?\s+(?:the\s+)?{_DAY}\s+(?:of\s+)?{_MO}{_YEAR}")

_SEASON = re.compile(
    r"(?:\b(?:MY|marketing year|crop year)\s*(?P<a>20\d{2})\s*[/-]\s*(?P<b>\d{2,4})(?!\d))"
    r"|(?:(?<![\d/-])(?P<c>20\d{2})\s*[/-]\s*(?P<e>\d{2,4})(?!\d)\s+(?:marketing year|crop year|MY))",
    re.IGNORECASE,
)
_FISCAL = re.compile(r"\b(?:FY\s?'?|fiscal year\s+)(?P<y>20\d{2})(?!\d)", re.IGNORECASE)


def _flag(flag: str, index: int, source: str, detail: str) -> dict[str, Any]:
    return {"flag": flag, "item_index": index, "source": source, "detail": detail[:_MAX_DETAIL]}


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _lookup(table: dict[str, int], word: str) -> int | None:
    key = word.lower()[:3]
    return table.get(key)


def _resolve(m: re.Match, as_of: date) -> tuple[date, int] | None:
    """The date a weekday+date phrase names and its stated weekday. A missing year is the as_of year, or the one before when
    that would put the date more than 60 days after as_of."""
    wd, mo = _lookup(_WEEKDAYS, m.group("wd")), _lookup(_MONTHS, m.group("mo"))
    if wd is None or mo is None:
        return None
    day = int(m.group("d"))
    try:
        if m.group("y"):
            return date(int(m.group("y")), mo, day), wd
        d = date(as_of.year, mo, day)
        if d > as_of + timedelta(days=60):
            d = date(as_of.year - 1, mo, day)
        return d, wd
    except ValueError:
        return None


def _check_item(i: int, item: Any, as_of_dt: datetime) -> list[dict[str, Any]]:
    as_of = as_of_dt.date()
    source = str(getattr(item, "source", "") or "")
    out: list[dict[str, Any]] = []

    pub = getattr(item, "published_at", None)
    if isinstance(pub, datetime) and _aware(pub) > as_of_dt:
        out.append(_flag("published_after_as_of", i, source, pub.date().isoformat()))

    text = getattr(item, "text", None)
    if not isinstance(text, str) or not text:
        return out

    seen: set[tuple[int, int]] = set()
    for rx in (_MD_FIRST, _DAY_FIRST):
        for m in rx.finditer(text):
            if m.span() in seen:
                continue
            seen.add(m.span())
            res = _resolve(m, as_of)
            if res is None:
                continue
            d, wd = res
            phrase = " ".join(m.group(0).split())
            if d.weekday() != wd:
                out.append(_flag("weekday_mismatch", i, source, phrase))
            if d > as_of:
                out.append(_flag("future_weekday_date", i, source, phrase))

    limit = as_of.year - 1
    for m in _SEASON.finditer(text):
        start = int(m.group("a") or m.group("c"))
        if start < limit:
            out.append(_flag("stale_season_year", i, source, " ".join(m.group(0).split())))
    for m in _FISCAL.finditer(text):
        if int(m.group("y")) < limit:
            out.append(_flag("stale_season_year", i, source, " ".join(m.group(0).split())))
    return out[:_MAX_FLAGS_PER_ITEM]


def check_bundle(bundle: ResearchBundle) -> list[dict[str, Any]]:
    """Flags for a research bundle: [{"flag", "item_index", "source", "detail"}]. Pure; never raises."""
    try:
        as_of_dt = _aware(bundle.as_of)
        flags: list[dict[str, Any]] = []
        for i, item in enumerate(bundle.items):
            try:
                flags.extend(_check_item(i, item, as_of_dt))
            except Exception as e:  # one bad item must not hide the others
                flags.append(_flag("check_error", i, str(getattr(item, "source", "")), f"{type(e).__name__}"))
        return flags
    except Exception as e:
        return [_flag("check_error", -1, "", f"{type(e).__name__}")]
