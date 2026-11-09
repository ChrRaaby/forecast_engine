"""Read-only prediction-market clients for EXP-012 (B-13): find markets that existed at a forecast's as_of, and the price then.

Venues: Polymarket (Gamma search + CLOB price history), Kalshi (public events/markets + trades), Manifold (search + bets).
Everything here is read-only and public; nothing is traded. The raw responses are cached under data/markets/raw/ (private data
repo) so a run can be audited and repeated.

Rules this module enforces in code (EXP-012 card; leakage checks LK1, LK2, LK5):
- `candidates` keeps a market only if it was created at or before as_of and had not closed by as_of (`open_at`).
- `pick_point` takes the last price point at or before as_of, and none if that point is more than 7 days older (dead market).
- A `Market` carries no price, volume, status or result fields, so nothing of that kind can reach the judge prompt by accident.
- `audit_rows` re-checks every stored match (price time, market creation time) and reports violations (LK5).

This module must not read the question results file or import the scoring code (LK4); tests/test_markets.py checks that.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import requests

from forecast_engine.clock import SystemClock

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "markets" / "raw"

MAX_PRICE_AGE = timedelta(days=7)
HISTORY_WINDOW = timedelta(days=7)  # Polymarket's history endpoint rejects windows over ~14 days
PRICE_CLAMP = (0.01, 0.99)
REAL_MONEY = {"polymarket": True, "kalshi": True, "manifold": False}
VENUE_ORDER = ("polymarket", "kalshi", "manifold")
MAX_CANDIDATES = 5
KALSHI_FLOOR = datetime(2026, 10, 1, tzinfo=timezone.utc)  # first forecast record; as_of filtering happens in code
MIN_OVERLAP = 0.12  # a candidate must share at least this (rarity-weighted) share of the question's content words

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
MANIFOLD = "https://api.manifold.markets/v0"

_CLOCK = SystemClock()


def now() -> datetime:
    """Wall-clock time, only for cache stamps and 'closed by now' flags (never for as-of logic)."""
    return _CLOCK.now()


GetJson = Callable[[str, dict[str, Any] | None], Any]


# ---------- time helpers ----------

def parse_time(v: Any) -> datetime | None:
    """ISO strings (also Polymarket's '2026-01-29 00:18:06+00') and epoch seconds/millis -> aware UTC datetime."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)) or (isinstance(v, str) and re.fullmatch(r"\d+(\.\d+)?", v)):
        x = float(v)
        return datetime.fromtimestamp(x / 1000 if x > 1e11 else x, tz=timezone.utc)
    s = str(v).strip().replace(" ", "T", 1)
    s = re.sub(r"([+-]\d\d)$", r"\1:00", s)
    s = s.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


# ---------- HTTP with a raw-response cache ----------

class Http:
    """GET JSON with an on-disk cache (the raw responses are the audit trail) and a small gap between live requests."""

    def __init__(self, raw_dir: Path = RAW_DIR, gap_s: float = 0.2, use_cache: bool = True) -> None:
        self.raw_dir = raw_dir
        self.gap_s = gap_s
        self.use_cache = use_cache
        self.live_requests = 0
        self.cache_hits = 0
        self._last = 0.0
        self._lock = threading.Lock()
        self.failures: list[str] = []
        self._session = requests.Session()

    def _path(self, url: str, params: dict[str, Any] | None) -> Path:
        key = json.dumps([url, sorted((params or {}).items())], default=str)
        host = re.sub(r"[^a-z0-9]+", "-", url.split("//", 1)[-1].split("/", 1)[0].lower())
        return self.raw_dir / host / (hashlib.sha1(key.encode()).hexdigest() + ".json")

    def get(self, url: str, params: dict[str, Any] | None = None, store: bool = True) -> Any:
        path = self._path(url, params)
        if store and self.use_cache and path.exists():
            self.cache_hits += 1
            return json.loads(path.read_text(encoding="utf-8"))["body"]
        for attempt in range(5):
            with self._lock:  # one request every gap_s across all worker threads
                wait = self.gap_s - (time.monotonic() - self._last)
                if wait > 0:
                    time.sleep(wait)
                self._last = time.monotonic()
                self.live_requests += 1
            try:
                r = self._session.get(url, params=params, timeout=60)
            except requests.RequestException:
                time.sleep(2 ** attempt)
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 404:
                body: Any = None
            elif r.status_code == 400:
                body = {"_http_error": 400, "text": r.text[:300]}
            else:
                r.raise_for_status()
                body = r.json()
            if not store:
                return body
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"url": url, "params": params, "fetched_at": now().isoformat(),
                                        "body": body}, ensure_ascii=False), encoding="utf-8")
            return body
        self.failures.append(f"{url} {params}")  # counted and reported; the caller sees no data (never cached)
        return None


# ---------- market record ----------

@dataclass(frozen=True)
class Market:
    """What a market was, not what it became: no price, volume, status or result."""

    venue: str
    market_id: str
    title: str
    rules: str
    end: datetime | None  # the scheduled end date shown to the judge
    created: datetime
    closed_at: datetime | None  # actual close (None = still open); used only by `open_at`, never shown to the judge
    ref: str  # Polymarket YES token id / Kalshi ticker / Manifold contract id
    url: str = ""

    @property
    def real_money(self) -> bool:
        return REAL_MONEY[self.venue]

    def key(self) -> str:
        return f"{self.venue}:{self.market_id}"


def open_at(m: Market, as_of: datetime) -> bool:
    """LK2: created at or before as_of, and not yet closed at as_of."""
    return m.created <= as_of and (m.closed_at is None or m.closed_at > as_of)


# ---------- raw -> Market converters ----------

def market_from_polymarket(m: dict[str, Any], event: dict[str, Any] | None = None) -> Market | None:
    event = event or {}
    try:
        outcomes = json.loads(m["outcomes"]) if isinstance(m.get("outcomes"), str) else m.get("outcomes")
        tokens = json.loads(m["clobTokenIds"]) if isinstance(m.get("clobTokenIds"), str) else m.get("clobTokenIds")
    except (KeyError, ValueError):
        return None
    if not outcomes or not tokens or [str(o).lower() for o in outcomes[:2]] != ["yes", "no"]:
        return None
    created = parse_time(m.get("createdAt") or m.get("startDate"))
    if created is None:
        return None
    closed_at = None
    if m.get("closed"):
        closed_at = parse_time(m.get("closedTime"))
        if closed_at is None:
            return None  # closed with no close time: we can't say it was open at as_of
    title = (m.get("question") or "").strip()
    ev_title = (event.get("title") or "").strip()
    if ev_title and ev_title.lower() not in title.lower():
        title = f"{ev_title}: {title}"
    rules = (m.get("description") or event.get("description") or "").strip()
    slug = event.get("slug") or m.get("slug") or ""
    return Market("polymarket", str(m["id"]), title, rules, parse_time(m.get("endDate") or event.get("endDate")), created,
                  closed_at, str(tokens[0]), f"https://polymarket.com/event/{slug}" if slug else "")


def market_from_kalshi(m: dict[str, Any]) -> Market | None:
    """From a compact index row (`kalshi_compact`) or a raw market dict."""
    if m.get("market_type", "binary") != "binary" or str(m.get("ticker", "")).startswith("KXMVE"):
        return None
    created = parse_time(m.get("created_time"))
    close = parse_time(m.get("close_time"))
    if created is None or close is None:
        return None
    parts = [(m.get("event_title") or "").strip(), (m.get("title") or "").strip()]
    sub = (m.get("yes_sub_title") or "").strip()
    title = " — ".join(p for p in parts if p)
    if sub and sub.lower() not in title.lower():
        title += f" [Yes = {sub}]"
    rules = "\n".join(x for x in ((m.get("rules_primary") or "").strip(), (m.get("rules_secondary") or "").strip()) if x)
    # The scheduled end shown to the judge is the latest possible expiration, never the actual (possibly early) close time,
    # because an early close can reveal that the event happened.
    end = parse_time(m.get("latest_expiration_time") or m.get("expiration_time"))
    return Market("kalshi", str(m["ticker"]), title, rules, end, created, close, str(m["ticker"]),
                  f"https://kalshi.com/markets/{str(m.get('event_ticker', '')).lower()}")


def market_from_manifold(m: dict[str, Any]) -> Market | None:
    if m.get("outcomeType") != "BINARY":
        return None
    created = parse_time(m.get("createdTime"))
    if created is None:
        return None
    close, res = parse_time(m.get("closeTime")), parse_time(m.get("resolutionTime"))
    ends = [t for t in (close, res) if t]
    closed_at = min(ends) if ends and (m.get("isResolved") or (close and close < now())) else None
    # A market resolved early gets closeTime == resolutionTime; showing that date would reveal the resolution moment.
    end = close if close and not (res and abs((close - res).total_seconds()) < 600) else None
    rules = (m.get("textDescription") or m.get("description_text") or "").strip()
    return Market("manifold", str(m["id"]), (m.get("question") or "").strip(), rules, end, created, closed_at, str(m["id"]),
                  m.get("url") or "")


# ---------- query building and ranking ----------

STOP = set("""a an the of to in on at by for from with and or not no be is are was were will would should could may might can do does
did has have had this that these those it its as if than then there their other any all more most than before after until during
between through over under about into within without which what who whom whose when where while how many much per yes""".split())


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9][a-z0-9.\-']*", text.lower().replace("’", "'")) if t not in STOP and len(t) > 1]


def search_queries(question_text: str) -> list[str]:
    """Three searches per venue: the title, its eight leading content words, and its four leading content words."""
    title = " ".join(question_text.split())[:150]
    kw = list(dict.fromkeys(tokens(question_text)))
    out = [title]
    for n in (8, 4):
        q = " ".join(kw[:n])
        if q and q not in out:
            out.append(q)
    return out


def rank(question_text: str, docs: Sequence[tuple[Any, str]], pool_for_idf: Sequence[str] | None = None) -> list[tuple[float, Any]]:
    """(score, item) best first. Score = share of the question's content words (weighted by rarity) found in the document."""
    corpus = list(pool_for_idf) if pool_for_idf is not None else [d for _, d in docs]
    df: dict[str, int] = {}
    for d in corpus:
        for t in set(tokens(d)):
            df[t] = df.get(t, 0) + 1
    n = max(len(corpus), 1)
    idf = lambda t: math.log(1 + n / (1 + df.get(t, 0)))  # noqa: E731
    q = set(tokens(question_text))
    total = sum(idf(t) for t in q) or 1.0
    scored = []
    for item, d in docs:
        dt = set(tokens(d))
        scored.append((sum(idf(t) for t in q & dt) / total, item))
    scored.sort(key=lambda x: -x[0])
    return scored


# ---------- venue clients ----------

def polymarket_search(http: Http, question_text: str) -> list[Market]:
    found: dict[str, Market] = {}
    for q in search_queries(question_text):
        j = http.get(f"{GAMMA}/public-search", {"q": q, "limit_per_type": 10, "keep_closed_markets": 1, "events_status": "all"})
        for e in (j or {}).get("events", []) or []:
            for m in e.get("markets", []) or []:
                mk = market_from_polymarket(m, e)
                if mk:
                    found.setdefault(mk.market_id, mk)
    return list(found.values())


def manifold_search(http: Http, question_text: str) -> list[Market]:
    found: dict[str, Market] = {}
    for q in search_queries(question_text):
        j = http.get(f"{MANIFOLD}/search-markets", {"term": q, "limit": 20, "filter": "all", "contractType": "BINARY"})
        for m in j or []:
            mk = market_from_manifold(m)
            if mk:
                found.setdefault(mk.market_id, mk)
    return list(found.values())


def manifold_with_rules(http: Http, m: Market) -> Market:
    """Search results carry no description; fetch the full market for the candidates that are kept."""
    j = http.get(f"{MANIFOLD}/market/{m.market_id}", None) or {}
    text = (j.get("textDescription") or "").strip()
    return Market(m.venue, m.market_id, m.title, text, m.end, m.created, m.closed_at, m.ref, m.url)


def kalshi_compact(ev: dict[str, Any], m: dict[str, Any]) -> dict[str, Any]:
    """Only the fields matching needs. Result, settlement and price fields are deliberately not kept."""
    keep = ("ticker", "event_ticker", "title", "yes_sub_title", "rules_primary", "rules_secondary", "created_time", "close_time",
            "latest_expiration_time", "expiration_time", "market_type")
    row = {k: m.get(k) for k in keep}
    row["event_title"] = ev.get("title")
    return row


def kalshi_series_index(http: Http, refresh: bool = False, path: Path | None = None) -> list[dict[str, Any]]:
    """Kalshi has no public keyword search. The list of its ~16,000 series (title, category, tags) is small enough to keep locally
    (compact rows only; the 20 MB raw list is not stored) and rank by keywords; markets are then fetched for the best series."""
    path = path or (http.raw_dir / "kalshi" / "series-index.jsonl")
    if path.exists() and not refresh:
        return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    j = http.get(f"{KALSHI}/series", None, store=False) or {}
    rows = [{"ticker": s.get("ticker"), "title": s.get("title") or "", "category": s.get("category"), "tags": s.get("tags") or []}
            for s in j.get("series", []) if s.get("ticker")]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return rows


def kalshi_compact(ev: dict[str, Any], m: dict[str, Any]) -> dict[str, Any]:
    """Only the fields matching needs. Result, settlement and price fields are deliberately not kept."""
    keep = ("ticker", "event_ticker", "title", "yes_sub_title", "rules_primary", "rules_secondary", "created_time", "close_time",
            "latest_expiration_time", "expiration_time", "market_type")
    row = {k: m.get(k) for k in keep}
    row["event_title"] = ev.get("title")
    return row


def kalshi_search(http: Http, series: Sequence[dict[str, Any]], question_text: str, as_of: datetime, n_series: int = 6,
                  top: int = 25) -> list[Market]:
    """Best-matching series by keywords -> their events and markets that close after as_of -> time filter -> rank."""
    docs = [(s, f"{s['title']} {' '.join(s['tags'])}") for s in series]
    best = [s for sc, s in rank(question_text, docs)[:n_series] if sc > 0]
    found: dict[str, tuple[Market, str]] = {}
    for s in best:
        cursor = None
        for _ in range(5):
            p: dict[str, Any] = {"series_ticker": s["ticker"], "with_nested_markets": "true", "limit": 200,
                                 "min_close_ts": int(min(as_of, KALSHI_FLOOR).timestamp())}  # same key for every record: cache reuse
            if cursor:
                p["cursor"] = cursor
            j = http.get(f"{KALSHI}/events", p) or {}
            for ev in j.get("events", []) or []:
                for m in ev.get("markets", []) or []:
                    mk_ = market_from_kalshi(kalshi_compact(ev, m))
                    if mk_ and open_at(mk_, as_of):
                        found.setdefault(mk_.market_id, (mk_, f"{ev.get('title') or ''} {m.get('title') or ''} {m.get('yes_sub_title') or ''}"))
            cursor = j.get("cursor")
            if not cursor:
                break
    return [m for sc, m in rank(question_text, list(found.values()))[:top] if sc > 0]


def candidates(question_text: str, as_of: datetime, http: Http, kalshi_series: Sequence[dict[str, Any]] | None = None,
               max_n: int = MAX_CANDIDATES, venues: Iterable[str] = VENUE_ORDER) -> list[Market]:
    """Up to `max_n` markets that existed and were open at as_of, best keyword overlap first (real money wins ties)."""
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    pool: list[Market] = []
    venues = set(venues)
    if "polymarket" in venues:
        pool += [m for m in polymarket_search(http, question_text) if open_at(m, as_of)]
    if "kalshi" in venues and kalshi_series is not None:
        pool += kalshi_search(http, kalshi_series, question_text, as_of)
    if "manifold" in venues:
        pool += [m for m in manifold_search(http, question_text) if open_at(m, as_of)]
    pool = [m for m in pool if open_at(m, as_of)]  # belt and braces: nothing unfiltered leaves this function
    if not pool:
        return []
    scored = rank(question_text, [(m, m.title) for m in pool])
    scored.sort(key=lambda x: (-round(x[0], 2), not x[1].real_money))
    top = [m for s, m in scored[:max_n] if s >= MIN_OVERLAP]
    return [manifold_with_rules(http, m) if m.venue == "manifold" else m for m in top]


# ---------- as-of price ----------

@dataclass(frozen=True)
class PricePoint:
    t: datetime
    p: float  # market's own YES price, 0..1
    age: timedelta
    n_points: int  # points in the fetch window (all <= as_of or not: before filtering)
    fidelity_min: int | None = None  # Polymarket bucket size actually used
    gap_min: float | None = None  # median minutes between the last few points up to as_of (granularity, LK6)


def pick_point(points: Iterable[tuple[datetime, float]], as_of: datetime, max_age: timedelta = MAX_PRICE_AGE) -> tuple[datetime, float] | None:
    """LK1: the last point at or before as_of; none if there is none or it is older than `max_age`. Later points are ignored
    however attractive they look."""
    best: tuple[datetime, float] | None = None
    for t, p in points:
        if t <= as_of and (best is None or t > best[0]):
            best = (t, p)
    if best is None or as_of - best[0] > max_age:
        return None
    return best


def median_gap_min(points: Iterable[tuple[datetime, float]], as_of: datetime, last: int = 10) -> float | None:
    ts = sorted(t for t, _ in points if t <= as_of)[-last:]
    gaps = [(b - a).total_seconds() / 60 for a, b in zip(ts, ts[1:])]
    return statistics.median(gaps) if gaps else None


def flip_and_clamp(p: float, polarity: str) -> float:
    """Market YES price -> probability that the Metaculus question resolves YES, clamped to [0.01, 0.99]."""
    if polarity not in ("same", "inverted"):
        raise ValueError(f"polarity must be same|inverted, got {polarity!r}")
    q = 1.0 - p if polarity == "inverted" else p
    return min(max(q, PRICE_CLAMP[0]), PRICE_CLAMP[1])


def logit(p: float) -> float:
    return math.log(p / (1 - p))


def mean_logit_price(prices: Sequence[float]) -> float:
    """Average of several markets' probabilities on the log-odds scale (matching rule 4)."""
    z = statistics.fmean(logit(p) for p in prices)
    return 1 / (1 + math.exp(-z))


FIDELITIES = (10, 60, 720, 1440)


def price_as_of(m: Market, as_of: datetime, http: Http) -> PricePoint | None:
    start = as_of - HISTORY_WINDOW
    if m.venue == "polymarket":
        for fid in FIDELITIES:
            j = http.get(f"{CLOB}/prices-history", {"market": m.ref, "startTs": int(start.timestamp()), "endTs": int(as_of.timestamp()),
                                                     "fidelity": fid})
            hist = (j or {}).get("history") if isinstance(j, dict) else None
            if hist:
                pts = [(parse_time(h["t"]), float(h["p"])) for h in hist]
                pick = pick_point(pts, as_of)
                return PricePoint(pick[0], pick[1], as_of - pick[0], len(pts), fid, median_gap_min(pts, as_of)) if pick else None
        return None
    if m.venue == "kalshi":
        j = http.get(f"{KALSHI}/markets/trades", {"ticker": m.ref, "min_ts": int(start.timestamp()), "max_ts": int(as_of.timestamp()),
                                                   "limit": 10})
        trades = (j or {}).get("trades", []) if isinstance(j, dict) else []
        pts = [(parse_time(t["created_time"]), float(t["yes_price_dollars"])) for t in trades if t.get("yes_price_dollars") is not None]
        pick = pick_point(pts, as_of)
        return PricePoint(pick[0], pick[1], as_of - pick[0], len(pts), None, median_gap_min(pts, as_of)) if pick else None
    if m.venue == "manifold":
        j = http.get(f"{MANIFOLD}/bets", {"contractId": m.ref, "limit": 5, "beforeTime": int(as_of.timestamp() * 1000)})
        pts = [(parse_time(b["createdTime"]), float(b["probAfter"])) for b in (j or []) if b.get("probAfter") is not None]
        pick = pick_point(pts, as_of)
        return PricePoint(pick[0], pick[1], as_of - pick[0], len(pts), None, median_gap_min(pts, as_of)) if pick else None
    raise ValueError(m.venue)


# ---------- LK5 audit and LK4 freeze gate ----------

def audit_rows(rows: Iterable[dict[str, Any]]) -> list[str]:
    """LK5: for every stored match, price time <= as_of and market creation <= as_of. Any line returned is a bug."""
    bad: list[str] = []
    for r in rows:
        as_of = parse_time(r["as_of"])
        for c in r.get("candidates", []):
            if c.get("price_t") and parse_time(c["price_t"]) > as_of:
                bad.append(f"{r['record']}: {c['venue']}:{c['market_id']} price time {c['price_t']} after as_of {r['as_of']}")
            if c.get("created") and parse_time(c["created"]) > as_of:
                bad.append(f"{r['record']}: {c['venue']}:{c['market_id']} created {c['created']} after as_of {r['as_of']}")
            if c.get("price_t") and c.get("created") and parse_time(c["price_t"]) < parse_time(c["created"]):
                bad.append(f"{r['record']}: {c['venue']}:{c['market_id']} price time before market creation")
    return bad


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git_committed(path: Path) -> bool:
    """True if `path` (inside a git repo, here the private data repo) has a commit and no uncommitted change."""
    d = Path(path).resolve().parent
    tracked = subprocess.run(["git", "-C", str(d), "ls-files", "--error-unmatch", Path(path).name], capture_output=True)
    if tracked.returncode != 0:
        return False
    dirty = subprocess.run(["git", "-C", str(d), "status", "--porcelain", "--", Path(path).name], capture_output=True, text=True)
    return dirty.returncode == 0 and not dirty.stdout.strip()


def require_frozen(table_path: Path, committed: Callable[[Path], bool] = git_committed) -> str:
    """LK4: the scorer calls this first. It refuses unless <table>.sha256 holds the table's current hash and both files are
    committed in the data repo. Returns the hash."""
    table_path = Path(table_path)
    freeze = table_path.with_suffix(table_path.suffix + ".sha256")
    if not table_path.exists() or not freeze.exists():
        raise RuntimeError(f"match table not frozen: {freeze.name} missing")
    h = file_sha256(table_path)
    if freeze.read_text(encoding="utf-8").split()[0] != h:
        raise RuntimeError("match table changed after it was frozen (hash differs)")
    if not (committed(table_path) and committed(freeze)):
        raise RuntimeError("match table and its hash must be committed in the data repo before scoring")
    return h
