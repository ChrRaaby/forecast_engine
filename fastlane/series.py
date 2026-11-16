"""Source B: NUMBER questions made by code from free, keyless feeds, resolved from the same feeds.

Four families chosen to move independently of each other (design review point 3):
  weather  highest air temperature (2 m, deg C) in a city on a date          Open-Meteo archive API
  quakes   number of M5.0+ earthquakes worldwide in a 3-day window           USGS FDSN event count
  crypto   BTC-USD / ETH-USD daily close (UTC), USD                          Coinbase Exchange candles
  fx       USD in EUR / JPY / GBP, ECB reference rate                        Frankfurter (ECB)
All are answered with percentiles (the numeric prompt), so there is no strike to choose and no volatility to scale it by: the
forecaster's interval is what is scored. Close dates are 3-7 days out. Crypto is capped at one question in four because it moves
together; US rates would need a FRED key (not set up).
ECB holidays: the question says the latest rate published on or before the date counts, and the resolver waits two days after the
date so the ECB's afternoon publication is in.
Whether the feeds answer from GitHub Actions is checked by `python -m fastlane.run --check-feeds` (Step 2).
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone
from typing import Any

from forecast_engine.schema import QuestionSnapshot

from .types import SERIES_ID_BASE, FastQuestion, GetJson

COINBASE = "https://api.exchange.coinbase.com"
FRANKFURTER = "https://api.frankfurter.dev/v1"
OPEN_METEO = "https://archive-api.open-meteo.com/v1/archive"
USGS = "https://earthquake.usgs.gov/fdsnws/event/1/count"
CRYPTO = ("BTC-USD", "ETH-USD")
FX = ("EUR", "JPY", "GBP")
CITIES = {  # name: (lat, lon, temperature range the question allows, deg C)
    "Oslo": (59.91, 10.75, (-35, 35)), "Chicago": (41.88, -87.63, (-35, 45)), "Singapore": (1.35, 103.82, (20, 40)),
    "Cairo": (30.04, 31.24, (0, 50)), "Sydney": (-33.87, 151.21, (5, 50)), "Reykjavik": (64.15, -21.94, (-25, 25)),
    "Mumbai": (19.08, 72.88, (15, 45)), "Santiago": (-33.45, -70.67, (-5, 45)),
}
FAMILIES = ("weather", "quakes", "crypto", "weather", "fx", "quakes")  # crypto once in six; the rest alternate
FX_WAIT_DAYS = 2


def _h(*parts: Any) -> int:
    return int(hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:12], 16)


def _fmt(x: float) -> str:
    return f"{x:,.2f}" if abs(x) >= 100 else f"{x:.4f}".rstrip("0").rstrip(".")


def _day_end(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(days=1)


# ---- feeds ----

def coinbase_spot(get: GetJson, product: str) -> float:
    return float(get(f"{COINBASE}/products/{product}/ticker", None)["price"])


def coinbase_close(get: GetJson, product: str, day: date, now: datetime) -> float | None:
    """The UTC daily close for `day`; None until the day is over."""
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    if start + timedelta(days=1) > now:
        return None
    rows = get(f"{COINBASE}/products/{product}/candles",
               {"granularity": 86400, "start": start.isoformat(), "end": (start + timedelta(days=1)).isoformat()})
    for t, _low, _high, _open, close, _vol in rows or []:
        if int(t) == int(start.timestamp()):
            return float(close)
    return None


def frankfurter_latest(get: GetJson, quote: str) -> float:
    return float(get(f"{FRANKFURTER}/latest", {"base": "USD", "symbols": quote})["rates"][quote])


def frankfurter_on(get: GetJson, quote: str, day: date, now: datetime) -> float | None:
    """The latest ECB rate published on or before `day` (holidays and weekends fall back); None until two days after `day`."""
    if now < _day_end(day) + timedelta(days=FX_WAIT_DAYS - 1):
        return None
    j = get(f"{FRANKFURTER}/{day.isoformat()}", {"base": "USD", "symbols": quote})
    if not j or date.fromisoformat(j["date"]) > day:
        return None
    return float(j["rates"][quote])


def open_meteo_tmax(get: GetJson, lat: float, lon: float, day: date) -> float | None:
    j = get(OPEN_METEO, {"latitude": lat, "longitude": lon, "start_date": day.isoformat(), "end_date": day.isoformat(),
                         "daily": "temperature_2m_max", "timezone": "UTC"})
    v = ((j or {}).get("daily") or {}).get("temperature_2m_max") or [None]
    return None if v[0] is None else float(v[0])


def usgs_count(get: GetJson, first: date, last: date, now: datetime) -> int | None:
    if now < _day_end(last) + timedelta(hours=6):
        return None  # let late catalogue entries arrive
    j = get(USGS, {"starttime": first.isoformat(), "endtime": (last + timedelta(days=1)).isoformat(), "minmagnitude": 5,
                   "format": "geojson"})
    return None if j is None or "count" not in j else int(j["count"])


# ---- questions ----

def _snap(q_id_key: str, kind: str, text: str, crit: str, bg: str, unit: str, lo: float, hi: float, open_lo: bool, open_hi: bool,
          now: datetime, closes: datetime) -> QuestionSnapshot:
    return QuestionSnapshot(
        question_id=SERIES_ID_BASE + _h(q_id_key) % 1_000_000_000, post_id=None, question_type=kind, question_text=text,
        background_info=bg, resolution_criteria=crit, page_url="", tournaments=("fastlane",), open_time=now,
        scheduled_resolution_time=closes, unit_of_measure=unit, lower_bound=lo, upper_bound=hi, open_lower_bound=open_lo,
        open_upper_bound=open_hi)


def _one(get: GetJson, family: str, now: datetime, salt: str, i: int) -> FastQuestion | None:
    today = now.date()
    close_day = today + timedelta(days=3 + _h(salt, today, family, i) % 5)
    if family == "weather":
        city = list(CITIES)[_h(salt, today, i, "c") % len(CITIES)]
        lat, lon, (lo, hi) = CITIES[city]
        key = f"series:weather:{city}:{close_day.isoformat()}"
        text = f"What will the highest air temperature at 2 m in {city} be on {close_day:%d %B %Y} (UTC day), in degrees Celsius?"
        crit = (f"Resolves to the value of daily temperature_2m_max for {close_day.isoformat()} (UTC) at latitude {lat}, longitude "
                f"{lon} in the Open-Meteo historical weather archive (archive-api.open-meteo.com), read after the day has ended.")
        snap = _snap(key, "numeric", text, crit, "", "°C", lo, hi, True, True, now, _day_end(close_day))
        return FastQuestion(key, "series", snap, _day_end(close_day),
                            {"family": "weather", "city": city, "lat": lat, "lon": lon, "day": close_day.isoformat()})
    if family == "quakes":
        first = close_day - timedelta(days=2)
        key = f"series:quakes:{close_day.isoformat()}"
        text = (f"How many earthquakes of magnitude 5.0 or greater will the USGS catalogue list worldwide from {first:%d %B} "
                f"to {close_day:%d %B %Y} (three UTC days, both ends included)?")
        crit = ("Resolves to the count returned by the USGS FDSN event count service (earthquake.usgs.gov/fdsnws/event/1/count) "
                f"for minmagnitude=5, starttime={first.isoformat()}, endtime={(close_day + timedelta(days=1)).isoformat()}, read at "
                "least six hours after the window ends.")
        snap = _snap(key, "discrete", text, crit, "", "earthquakes", 0, 60, False, True, now, _day_end(close_day))
        return FastQuestion(key, "series", snap, _day_end(close_day),
                            {"family": "quakes", "first": first.isoformat(), "last": close_day.isoformat()})
    if family == "crypto":
        sym = CRYPTO[_h(salt, today, i, "s") % len(CRYPTO)]
        spot = coinbase_spot(get, sym)
        key = f"series:crypto:{sym}:{close_day.isoformat()}"
        text = f"What will the {sym} daily closing price on Coinbase Exchange be for {close_day:%d %B %Y} (UTC), in US dollars?"
        crit = f"Resolves to the close of the {close_day.isoformat()} UTC daily candle (granularity 86400) for {sym} on Coinbase Exchange."
        snap = _snap(key, "numeric", text, crit, f"The latest {sym} price on Coinbase Exchange at the time of this question is {_fmt(spot)} USD.",
                     "USD", round(spot * 0.4, 2), round(spot * 1.8, 2), True, True, now, _day_end(close_day))
        return FastQuestion(key, "series", snap, _day_end(close_day),
                            {"family": "crypto", "symbol": sym, "day": close_day.isoformat(), "spot": spot})
    # fx
    while close_day.weekday() >= 5:
        close_day += timedelta(days=1)
    sym = FX[_h(salt, today, i, "s") % len(FX)]
    spot = frankfurter_latest(get, sym)
    key = f"series:fx:{sym}:{close_day.isoformat()}"
    text = f"How many {sym} will 1 US dollar buy at the ECB reference rate for {close_day:%d %B %Y}?"
    crit = (f"Resolves to the ECB reference rate for USD to {sym} as served by api.frankfurter.dev for {close_day.isoformat()}. If no rate "
            "is published for that day (holiday), the latest rate published before it counts.")
    snap = _snap(key, "numeric", text, crit, f"The latest published ECB-based rate for 1 USD in {sym} at the time of this question is {_fmt(spot)}.",
                 sym, round(spot * 0.8, 4), round(spot * 1.2, 4), True, True, now, _day_end(close_day))
    return FastQuestion(key, "series", snap, _day_end(close_day), {"family": "fx", "symbol": sym, "day": close_day.isoformat(), "spot": spot})


def make_questions(get: GetJson, now: datetime, n: int = 6, salt: str = "fastlane-1", done: set[str] = frozenset()) -> list[FastQuestion]:
    out: list[FastQuestion] = []
    for i in range(n * 4):
        if len(out) >= n:
            break
        family = FAMILIES[(i + _h(salt, now.date())) % len(FAMILIES)]
        try:
            q = _one(get, family, now, salt, i)
        except Exception:
            continue  # a feed that is down costs us its questions, nothing else
        if q and q.key not in done and all(q.key != x.key for x in out):
            out.append(q)
    return out


def resolve(get: GetJson, meta: dict[str, Any], now: datetime) -> float | None:
    """The value once the feed has it, None while it has not."""
    fam = meta["family"]
    if fam == "weather":
        day = date.fromisoformat(meta["day"])
        return None if now < _day_end(day) + timedelta(days=1) else open_meteo_tmax(get, meta["lat"], meta["lon"], day)
    if fam == "quakes":
        v = usgs_count(get, date.fromisoformat(meta["first"]), date.fromisoformat(meta["last"]), now)
        return None if v is None else float(v)
    if fam == "crypto":
        return coinbase_close(get, meta["symbol"], date.fromisoformat(meta["day"]), now)
    if fam == "fx":
        return frankfurter_on(get, meta["symbol"], date.fromisoformat(meta["day"]), now)
    raise ValueError(f"unknown series family {fam!r}")


def check_feeds(get: GetJson) -> dict[str, str]:
    """One tiny call per feed, for the Actions reachability test."""
    today = date.today()
    checks = {
        "coinbase": lambda: coinbase_spot(get, "BTC-USD"),
        "frankfurter": lambda: frankfurter_latest(get, "EUR"),
        "open-meteo": lambda: open_meteo_tmax(get, 59.91, 10.75, today - timedelta(days=10)),
        "usgs": lambda: (get(USGS, {"starttime": (today - timedelta(days=3)).isoformat(), "endtime": today.isoformat(),
                                    "minmagnitude": 5, "format": "geojson"}) or {})["count"],
    }
    res = {}
    for name, fn in checks.items():
        try:
            res[name] = f"ok ({fn()})"
        except Exception as e:
            res[name] = f"FAILED {type(e).__name__}: {e}"
    return res
