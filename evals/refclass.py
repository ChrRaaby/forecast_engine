"""Reference-class library (B-56): Yes-rates and similar resolved questions, for later experiments (EXP-006 arm D, research/10).

Evaluation side only: the live bot does not import this module and no config uses it. It answers two questions about a forecast
made at time `as_of`, using only questions that had **actually resolved strictly before as_of** (protocol T1/T6):

- `yes_rate(table, as_of, tournament=, category=)`: the share of resolved binary questions that resolved Yes, with n and a Wilson
  95% interval. Small n is flagged, never hidden.
- `similar(table, question, as_of, k=5)`: up to k resolved binary questions with outcomes, most similar title first. Never the
  question itself, nor another sub-question of its own Metaculus post. The scorer is a plain function (`Scorer`), BM25 over titles
  by default (numpy only), so an embedding scorer can replace it later without touching the filter.
- `prompt_block(...)`: a short labelled text block of both, for a future experiment arm. Not wired into any config.

The table (`load_table`) is built from (a) our live records `data/runs/**/q*.json` joined to `data/outcomes/outcomes.jsonl` and
(b) the B-39 benchmark posts `data/benchmark/posts_*.jsonl` (latest file), when present. Only binary questions that resolved
yes/no with a time-zone-aware actual resolve time are kept; every other question is dropped and counted by reason.

The as-of rule. The time used is Metaculus's `actual_resolve_time` (gated by `evals.outcomes.resolved_date`: resolved, with a
resolution), never the scheduled close or resolve time: a question can resolve before or after its schedule, and only the actual
time says whether the outcome was known at as_of. The comparison is strict (`resolved_at < as_of`), and an as_of without a time zone
raises ValueError. Residual risk this can't see: a resolution changed after as_of (re-resolved or annulled later) shows its current
value; Metaculus gives no resolution history.

PC use (counts only; no titles are printed):

    poetry run python -m evals.refclass --summary                       # data/ in this checkout
    poetry run python -m evals.refclass --summary --data-dir <path>     # or FE_DATA=<path>: a data folder elsewhere
    poetry run python -m evals.refclass --summary --as-of 2026-10-01T00:00:00Z
    poetry run python -m evals.refclass --check        # leakage check on the real table: one PASS/FAIL line, counts only

Data location: `--data-dir`, else the environment variable `FE_DATA`, else `data/` next to this checkout. Under it the loader reads
`runs/`, `outcomes/outcomes.jsonl` and `benchmark/`; `--runs-dir`, `--outcomes` and `--benchmark-dir` override each one.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Protocol, Sequence

import numpy as np

from . import outcomes as oc

ROOT = Path(__file__).resolve().parents[1]
DATA_ENV = "FE_DATA"
Z95 = 1.959963984540054
SMALL_N = 30  # below this a Yes-rate is printed with a "small sample" flag (judgement: the Wilson interval is still ±15 points)
DEFAULT_K = 5

SOURCE_LIVE = "live"
SOURCE_BENCHMARK = "benchmark"

# Drop reasons (counted per question, per source)
DROP_UNREADABLE = "unreadable file or line"
DROP_TEST = "test run (test_questions)"
DROP_NOT_BINARY = "not binary"
DROP_NO_OUTCOME = "no outcome row"
DROP_UNRESOLVED = "not resolved"
DROP_ANNULLED = "annulled or ambiguous"
DROP_BAD_RESOLUTION = "resolution not yes/no"
DROP_NO_TIME = "actual resolve time missing or unreadable"
DROP_NAIVE_TIME = "actual resolve time has no time zone"
DROP_CONDITIONAL = "conditional post"
DROP_NO_ID = "no question id"
DROP_DUPLICATE = "already in the table from another source"


# --------------------------------------------------------------------------- table
@dataclass(frozen=True)
class RefQuestion:
    """One resolved binary question. Holds no research text; the title is public Metaculus data."""

    question_id: int
    post_id: int | None
    title: str
    source: str  # "live" | "benchmark"
    tournaments: tuple[str, ...]
    categories: tuple[str, ...]
    tags: tuple[str, ...]
    open_time: datetime | None
    resolved_at: datetime  # Metaculus actual_resolve_time, time-zone aware; never the scheduled close
    outcome: int  # 1 = Yes, 0 = No


@dataclass
class RefTable:
    rows: list[RefQuestion]
    drops: Counter = field(default_factory=Counter)  # (source, reason) -> questions
    found: dict[str, str] = field(default_factory=dict)  # source -> what was read, or why nothing was

    @property
    def no_data(self) -> bool:
        return not self.rows


def _require_aware(as_of: datetime) -> None:
    if not isinstance(as_of, datetime):
        raise TypeError(f"as_of must be a datetime, got {type(as_of).__name__}")
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware (a date without a time zone is refused)")


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def resolve_time(row: dict[str, Any]) -> tuple[datetime | None, str | None]:
    """(actual resolve time, None) or (None, drop reason). `row` is an outcomes.jsonl line or a Metaculus question object.

    Gated by `evals.outcomes.resolved_date` (resolved, with a resolution, actual time present), then the full timestamp is parsed:
    the date alone can't decide "strictly before as_of" within a day. The scheduled close/resolve time is never read."""
    if oc.resolved_date(row) is None:
        if row.get("status") in oc.SETTLED and row.get("resolution") is not None:
            return None, DROP_NO_TIME
        return None, DROP_UNRESOLVED
    try:
        t = _parse_time(row.get("actual_resolve_time"))
    except ValueError:
        return None, DROP_NO_TIME
    if t is None:
        return None, DROP_NO_TIME
    if t.tzinfo is None or t.utcoffset() is None:
        return None, DROP_NAIVE_TIME
    return t, None


def _binary_outcome(resolution: Any) -> tuple[int | None, str | None]:
    res = str(resolution or "").strip().lower()
    if res in oc.NO_SCORE_RESOLUTIONS:
        return None, DROP_ANNULLED
    if res == "yes":
        return 1, None
    if res == "no":
        return 0, None
    return None, DROP_BAD_RESOLUTION


def _optional_time(value: Any) -> datetime | None:
    try:
        return _parse_time(value)
    except ValueError:
        return None


def _row_from(source: str, qid: int, post_id: Any, title: str, outcome_row: dict[str, Any], *, tournaments: Iterable[str] = (),
              categories: Iterable[str] = (), tags: Iterable[str] = (), open_time: Any = None) -> tuple[RefQuestion | None, str | None]:
    if outcome_row.get("status") not in oc.SETTLED or outcome_row.get("resolution") is None:
        return None, DROP_UNRESOLVED
    y, why = _binary_outcome(outcome_row.get("resolution"))
    if y is None:
        return None, why
    t, why = resolve_time(outcome_row)
    if t is None:
        return None, why
    return RefQuestion(
        question_id=int(qid), post_id=int(post_id) if post_id not in (None, "") else None, title=str(title or ""), source=source,
        tournaments=tuple(str(x) for x in tournaments if x), categories=tuple(str(x) for x in categories if x),
        tags=tuple(str(x) for x in tags if x), open_time=_optional_time(open_time), resolved_at=t, outcome=y,
    ), None


def load_live(runs_dir: Path, outcomes_path: Path) -> tuple[list[RefQuestion], Counter, str]:
    """Live records joined to outcomes.jsonl, one row per question (published and shadow records of a question count once).
    Uses the question snapshot of the earliest record (by as_of) for title, tournaments and open time."""
    drops: Counter = Counter()
    if not runs_dir.is_dir():
        return [], drops, f"no runs folder at {runs_dir}"
    outcomes = oc.load_outcomes(outcomes_path)
    snaps: dict[int, tuple[str, dict[str, Any]]] = {}
    n_files = 0
    test_qids: set[int] = set()
    for path in sorted(runs_dir.rglob("q*.json")):
        n_files += 1
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
            snap = rec["question"]
            qid = int(snap["question_id"])
        except (OSError, ValueError, KeyError, TypeError):
            drops[DROP_UNREADABLE] += 1
            continue
        if "test_questions" in path.parent.name:
            test_qids.add(qid)
            continue
        as_of = str(rec.get("as_of") or "")
        if qid not in snaps or as_of < snaps[qid][0]:
            snaps[qid] = (as_of, snap)
    drops[DROP_TEST] += len(test_qids - set(snaps))
    rows: list[RefQuestion] = []
    for qid in sorted(snaps):
        snap = snaps[qid][1]
        if snap.get("question_type") != "binary":
            drops[DROP_NOT_BINARY] += 1
            continue
        out = outcomes.get(qid)
        if out is None:
            drops[DROP_NO_OUTCOME] += 1
            continue
        row, why = _row_from(SOURCE_LIVE, qid, snap.get("post_id") or out.get("post_id"), snap.get("question_text", ""), out,
                             tournaments=snap.get("tournaments") or (), open_time=snap.get("open_time"))
        if row is None:
            drops[why] += 1
        else:
            rows.append(row)
    note = f"{n_files} record files, {len(snaps)} questions; outcomes: " + (
        f"{len(outcomes)} rows" if outcomes_path.exists() else f"no file at {outcomes_path}")
    return rows, drops, note


def _slugs(items: Any) -> list[str]:
    out = []
    for x in items or ():
        if isinstance(x, dict):
            s = x.get("slug") or x.get("name")
            if s:
                out.append(str(s))
        elif x:
            out.append(str(x))
    return out


def post_labels(post: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    """(tournaments, categories, tags) of a Metaculus post (`projects`: tournament / question_series, category, tag)."""
    pr = post.get("projects") or {}
    tournaments = _slugs(pr.get("tournament")) + _slugs(pr.get("question_series"))
    return tournaments, _slugs(pr.get("category")), _slugs(pr.get("tag"))


def benchmark_questions(post: dict[str, Any]) -> tuple[list[tuple[dict[str, Any], str]], bool]:
    """([(question object, title)], is_conditional) for a post: a single question or each sub-question of a group."""
    if post.get("conditional"):
        return [], True
    q = post.get("question")
    if q:
        return [(q, q.get("title") or post.get("title") or "")], False
    out = []
    post_title = str(post.get("title") or "")
    for sub in (post.get("group_of_questions") or {}).get("questions", []) or []:
        title = str(sub.get("title") or post_title)
        label = str(sub.get("label") or "")
        if label and label not in title:
            title = f"{title} ({label})"
        out.append((sub, title))
    return out, False


def load_benchmark(bench_dir: Path) -> tuple[list[RefQuestion], Counter, str]:
    """B-39 benchmark posts (the latest posts_*.jsonl; a post's `_detail`, when fetched, is preferred)."""
    drops: Counter = Counter()
    files = sorted(bench_dir.glob("posts_*.jsonl")) if bench_dir.is_dir() else []
    if not files:
        return [], drops, f"no posts_*.jsonl in {bench_dir}"
    path = files[-1]
    rows: list[RefQuestion] = []
    seen: set[int] = set()
    n_posts = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            listed = json.loads(line)
            post = listed.get("_detail") or listed
        except (ValueError, AttributeError):
            drops[DROP_UNREADABLE] += 1
            continue
        n_posts += 1
        qs, conditional = benchmark_questions(post)
        if conditional:
            drops[DROP_CONDITIONAL] += 1
            continue
        tournaments, categories, tags = post_labels(post)
        for q, title in qs:
            if q.get("id") is None:
                drops[DROP_NO_ID] += 1
                continue
            qid = int(q["id"])
            if qid in seen:
                continue
            seen.add(qid)
            if q.get("type") != "binary":
                drops[DROP_NOT_BINARY] += 1
                continue
            row, why = _row_from(SOURCE_BENCHMARK, qid, post.get("id"), title, q, tournaments=tournaments,
                                 categories=categories, tags=tags, open_time=q.get("open_time"))
            if row is None:
                drops[why] += 1
            else:
                rows.append(row)
    older = f"; {len(files) - 1} older file(s) ignored" if len(files) > 1 else ""
    return rows, drops, f"{path.name}: {n_posts} posts{older}"


def data_dir(arg: str | None = None) -> Path:
    """--data-dir, else $FE_DATA, else data/ in this checkout."""
    return Path(arg or os.environ.get(DATA_ENV) or ROOT / "data")


def load_table(data: Path | None = None, *, runs_dir: Path | None = None, outcomes_path: Path | None = None,
               benchmark_dir: Path | None = None) -> RefTable:
    """One table of resolved binary questions from the live records and the B-39 benchmark. Missing folders give an empty table
    with a reason per source (`table.found`), never an exception. A question in both sources is kept once (live first)."""
    base = data or data_dir()
    live, live_drops, live_note = load_live(runs_dir or base / "runs", outcomes_path or base / "outcomes" / "outcomes.jsonl")
    bench, bench_drops, bench_note = load_benchmark(benchmark_dir or base / "benchmark")
    table = RefTable(rows=[], found={SOURCE_LIVE: live_note, SOURCE_BENCHMARK: bench_note})
    for reason, n in live_drops.items():
        table.drops[(SOURCE_LIVE, reason)] += n
    for reason, n in bench_drops.items():
        table.drops[(SOURCE_BENCHMARK, reason)] += n
    seen: set[int] = set()
    for row in live + bench:
        if row.question_id in seen:
            table.drops[(row.source, DROP_DUPLICATE)] += 1
            continue
        seen.add(row.question_id)
        table.rows.append(row)
    return table


# --------------------------------------------------------------------------- the as-of filter (one place)
def _ids(question: Any) -> tuple[int | None, int | None]:
    if question is None:
        return None, None
    get = question.get if isinstance(question, dict) else lambda k: getattr(question, k, None)
    qid, pid = get("question_id"), get("post_id")
    return (int(qid) if qid is not None else None), (int(pid) if pid not in (None, "") else None)


def available(rows: Iterable[RefQuestion], as_of: datetime, exclude: Any = None) -> list[RefQuestion]:
    """The rows whose outcome was known at as_of: actual resolve time strictly before as_of. With `exclude` (a question with
    question_id / post_id), also drops that question and every other question of its post. Every lookup goes through here."""
    _require_aware(as_of)
    qid, pid = _ids(exclude)
    out = []
    for r in rows:
        if r.resolved_at.tzinfo is None or r.resolved_at.utcoffset() is None:  # a hand-built row: refuse rather than guess
            raise ValueError(f"question {r.question_id}: resolved_at has no time zone")
        if not r.resolved_at < as_of:
            continue
        if qid is not None and r.question_id == qid:
            continue
        if pid is not None and r.post_id == pid:
            continue
        out.append(r)
    return out


# --------------------------------------------------------------------------- Yes-rate
@dataclass(frozen=True)
class YesRate:
    n: int
    yes: int
    rate: float | None  # None when n = 0
    lo: float | None  # Wilson 95% interval
    hi: float | None
    as_of: datetime
    tournament: str | None = None
    category: str | None = None

    @property
    def small(self) -> bool:
        return self.n < SMALL_N

    @property
    def label(self) -> str:
        parts = [f'tournament "{self.tournament}"' if self.tournament else "", f'category "{self.category}"' if self.category else ""]
        return ", ".join(p for p in parts if p) or "all sources"


def wilson(yes: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion (95% by default)."""
    if n <= 0:
        raise ValueError("n must be positive")
    if not 0 <= yes <= n:
        raise ValueError("yes must be between 0 and n")
    p = yes / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, centre - half), min(1.0, centre + half)


def yes_rate(table: RefTable | Sequence[RefQuestion], as_of: datetime, tournament: str | None = None,
             category: str | None = None, *, exclude: Any = None) -> YesRate:
    """Share of resolved binary questions that resolved Yes, among those resolved strictly before as_of (optionally in one
    tournament and/or category; `exclude` drops a question and its whole post)."""
    rows = table.rows if isinstance(table, RefTable) else list(table)
    pool = [r for r in available(rows, as_of, exclude)
            if (tournament is None or tournament in r.tournaments) and (category is None or category in r.categories)]
    n, yes = len(pool), sum(r.outcome for r in pool)
    if n == 0:
        return YesRate(0, 0, None, None, None, as_of, tournament, category)
    lo, hi = wilson(yes, n)
    return YesRate(n, yes, yes / n, lo, hi, as_of, tournament, category)


# --------------------------------------------------------------------------- similar questions
class Scorer(Protocol):
    def __call__(self, query: str, docs: Sequence[str]) -> np.ndarray: ...


_TOKEN = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    "a an and are as at be before by did do does for from has have in is it its of on or than that the this to was were "
    "what when which who will with would".split()
)


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in STOPWORDS]


def bm25(query: str, docs: Sequence[str], k1: float = 1.5, b: float = 0.75) -> np.ndarray:
    """Okapi BM25 score of each doc for the query (numpy only). IDF is computed on `docs`, i.e. on the questions that passed the
    as-of filter, so no later question shapes the weights either."""
    if not docs:
        return np.zeros(0)
    toks = [tokenize(d) for d in docs]
    vocab: dict[str, int] = {}
    for ts in toks:
        for t in ts:
            vocab.setdefault(t, len(vocab))
    q = [vocab[t] for t in dict.fromkeys(tokenize(query)) if t in vocab]
    if not q:
        return np.zeros(len(docs))
    tf = np.zeros((len(docs), len(vocab)))
    for i, ts in enumerate(toks):
        for t in ts:
            tf[i, vocab[t]] += 1
    n = len(docs)
    df = (tf > 0).sum(axis=0)
    idf = np.log(1 + (n - df + 0.5) / (df + 0.5))
    length = tf.sum(axis=1)
    avg = length.mean() or 1.0
    sub = tf[:, q]
    denom = sub + k1 * (1 - b + b * length / avg)[:, None]
    return (idf[q] * sub * (k1 + 1) / np.where(denom == 0, 1, denom)).sum(axis=1)


@dataclass(frozen=True)
class Similar:
    question: RefQuestion
    score: float


def _title(question: Any) -> str:
    if isinstance(question, str):
        return question
    get = question.get if isinstance(question, dict) else lambda k: getattr(question, k, None)
    return str(get("title") or get("question_text") or "")


def similar(table: RefTable | Sequence[RefQuestion], question: Any, as_of: datetime, k: int = DEFAULT_K, *,
            scorer: Scorer = bm25, min_score: float = 1e-9) -> list[Similar]:
    """Up to k resolved binary questions most similar to `question` (a QuestionSnapshot, RefQuestion or dict with question_id,
    post_id and a title/question_text), resolved strictly before as_of, never the question itself or a question of its post.
    Questions with no similarity (score ≤ min_score) are not returned; ties go to the later-resolved, then the lower id."""
    rows = table.rows if isinstance(table, RefTable) else list(table)
    pool = available(rows, as_of, exclude=question)
    if not pool or k <= 0:
        return []
    scores = np.asarray(scorer(_title(question), [r.title for r in pool]), dtype=float)
    if scores.shape != (len(pool),):
        raise ValueError(f"scorer returned shape {scores.shape}, expected ({len(pool)},)")
    order = sorted(range(len(pool)), key=lambda i: (-scores[i], -pool[i].resolved_at.timestamp(), pool[i].question_id))
    return [Similar(pool[i], float(scores[i])) for i in order[:k] if scores[i] > min_score]


# --------------------------------------------------------------------------- prompt block (for a future arm; not wired)
def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def prompt_block(rates: Sequence[YesRate], neighbours: Sequence[Similar]) -> str:
    """A short labelled text block: the Yes-rate(s) and the similar resolved questions. Holds titles, outcomes and resolve dates
    (all before as_of) only."""
    lines = ["Reference class (questions that resolved before this forecast was made):"]
    for r in rates:
        if r.n == 0:
            lines.append(f"- Yes-rate, {r.label}: no resolved questions yet")
        else:
            flag = " (small sample, treat with care)" if r.small else ""
            lines.append(f"- Yes-rate, {r.label}: {_pct(r.rate)} of {r.n} resolved Yes (95% interval {_pct(r.lo)}–{_pct(r.hi)})"
                         f"{flag}")
    if neighbours:
        lines.append("- Similar resolved questions, most similar first:")
        for i, s in enumerate(neighbours, 1):
            q = s.question
            lines.append(f'  {i}. "{q.title}": resolved {"YES" if q.outcome else "NO"} on {q.resolved_at:%Y-%m-%d}')
    else:
        lines.append("- Similar resolved questions: none found")
    return "\n".join(lines)


# --------------------------------------------------------------------------- CLI (counts only)
def summary(table: RefTable, as_of: datetime | None = None) -> str:
    """Counts per source / tournament / category, drops by reason, and the overall Yes-rate. No titles."""
    lines = ["Reference-class table (B-56): resolved binary questions"]
    for src in (SOURCE_LIVE, SOURCE_BENCHMARK):
        lines.append(f"  source {src}: {table.found.get(src, '')}")
    if table.no_data:
        lines.append("no data: no resolved binary questions found. Point --data-dir (or FE_DATA) at the private data folder "
                     "that holds runs/, outcomes/outcomes.jsonl and benchmark/.")
    rows = table.rows
    if as_of is not None:
        rows = available(rows, as_of)
        lines.append(f"  as of {as_of.isoformat()}: {len(rows)} of {len(table.rows)} questions resolved strictly before it")

    def block(title: str, counter: Counter) -> None:
        lines.append(title)
        for key, n in sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0]))):
            lines.append(f"  {n:6d}  {key}")

    block("questions per source:", Counter(r.source for r in rows))
    block("questions per tournament (a question can be in several):",
          Counter(t for r in rows for t in (r.tournaments or ("(none)",))))
    block("questions per category (a question can be in several):",
          Counter(c for r in rows for c in (r.categories or ("(none)",))))
    block("dropped (source / reason):", Counter({f"{s} / {why}": n for (s, why), n in table.drops.items()}))
    if rows:
        yes = sum(r.outcome for r in rows)
        lo, hi = wilson(yes, len(rows))
        flag = " (small sample)" if len(rows) < SMALL_N else ""
        lines.append(f"overall Yes-rate: {yes}/{len(rows)} = {_pct(yes / len(rows))} (Wilson 95% {_pct(lo)}–{_pct(hi)}){flag}")
    return "\n".join(lines) + "\n"


def self_check(table: RefTable, k: int = DEFAULT_K) -> tuple[bool, str]:
    """Leakage check on a real table (counts only): for every question with an open time, look it up as of that open time and
    verify every similar question resolved strictly before it, is not the question itself nor from its post, and that the
    Yes-rate counts no more questions than resolved before it."""
    checked = skipped = returned = bad = 0
    for r in table.rows:
        t = r.open_time
        if t is None or t.tzinfo is None or t.utcoffset() is None:
            skipped += 1
            continue
        checked += 1
        hits = similar(table, r, t, k)
        returned += len(hits)
        bad += sum(1 for h in hits if not h.question.resolved_at < t or h.question.question_id == r.question_id
                   or (r.post_id is not None and h.question.post_id == r.post_id))
        rate = yes_rate(table, t, exclude=r)
        bad += rate.n > sum(1 for x in table.rows if x.resolved_at < t)
    ok = bad == 0 and checked > 0
    words = (f"{checked} questions looked up as of their open time ({skipped} skipped: no open time with a time zone), "
             f"{returned} similar questions returned, {bad} violations")
    return ok, f"{'PASS' if ok else 'FAIL'}  refclass real-data check: {words}"


def _parse_as_of(s: str) -> datetime:
    t = _parse_time(s)
    if t is None:
        raise ValueError("empty time")
    _require_aware(t)
    return t


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m evals.refclass", description="Reference-class library (B-56): counts only.")
    ap.add_argument("--summary", action="store_true", help="print counts per source/tournament/category and the overall Yes-rate")
    ap.add_argument("--data-dir", help=f"data folder with runs/, outcomes/, benchmark/ (default: ${DATA_ENV}, else data/)")
    ap.add_argument("--runs-dir", help="override: live records folder")
    ap.add_argument("--outcomes", help="override: outcomes.jsonl")
    ap.add_argument("--benchmark-dir", help="override: B-39 benchmark folder")
    ap.add_argument("--as-of", help="only count questions resolved strictly before this time (ISO, with a time zone)")
    ap.add_argument("--check", action="store_true",
                    help="leakage check on the real table: one PASS/FAIL line with counts (exit 1 on FAIL)")
    args = ap.parse_args(argv)
    if not (args.summary or args.check):
        ap.print_help()
        return 0
    try:
        as_of = _parse_as_of(args.as_of) if args.as_of else None
    except ValueError as e:
        print(f"--as-of: {e}", file=sys.stderr)
        return 2
    table = load_table(data_dir(args.data_dir), runs_dir=Path(args.runs_dir) if args.runs_dir else None,
                       outcomes_path=Path(args.outcomes) if args.outcomes else None,
                       benchmark_dir=Path(args.benchmark_dir) if args.benchmark_dir else None)
    if args.summary:
        sys.stdout.write(summary(table, as_of))
    if args.check:
        if table.no_data:
            print("SKIP  refclass real-data check: no data")
            return 2
        ok, line = self_check(table)
        print(line)
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
