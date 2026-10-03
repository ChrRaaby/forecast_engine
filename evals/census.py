"""Question-supply census for backtests (B-27 step 1, I-14).

Pulls every closed/resolved question from past Metaculus bot tournaments and counts how many would be eligible for a backtest
at each candidate evaluation-model cutoff (protocol §3: as-of = question open time must be after the model's release date).

    poetry run python -m evals.census                 # fetch (cached) + print the report
    poetry run python -m evals.census --refresh       # re-fetch from the API

Raw rows are cached in data/census/questions.jsonl (private data repo). This script reads outcomes only to drop annulled or
unresolved questions, which the protocol's eligibility rule allows; it never selects on the outcome itself (T6).
Be gentle with the API: the bot uses the same token, so requests are spaced and 429s back off.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "census" / "questions.jsonl"
API = "https://www.metaculus.com/api/posts/"

# Past bot tournaments (ids from forecasting-tools' MetaculusClient constants and the tournaments API).
TOURNAMENTS = {
    "fall-aib-2025": 32813,
    "spring-aib-2026": 32916,
    "summer-futureeval-2026": 33022,
    "fall-futureeval-2026": 33121,
}
MINIBENCH_TAG = "minibench"
REQUEST_GAP_S = 0.6


def _get(session: requests.Session, params: dict) -> dict:
    for attempt in range(6):
        r = session.get(API, params=params, timeout=60)
        if r.status_code == 429:
            time.sleep(30 * (attempt + 1))
            continue
        r.raise_for_status()
        time.sleep(REQUEST_GAP_S)
        return r.json()
    raise RuntimeError("Metaculus kept returning 429")


def _dt(s: str | None) -> str | None:
    return s[:19] + "Z" if s else None


def _rows_from_post(p: dict, source: str) -> list[dict]:
    qs = []
    if p.get("question"):
        qs = [p["question"]]
    elif p.get("group_of_questions"):
        qs = p["group_of_questions"].get("questions", [])
    rows = []
    for q in qs:
        rows.append({
            "source": source,
            "post_id": p["id"],
            "question_id": q.get("id"),
            "title": q.get("title") or p.get("title"),
            "type": q.get("type"),
            "in_group": bool(p.get("group_of_questions")),
            "open_time": _dt(q.get("open_time") or p.get("open_time")),
            "scheduled_close_time": _dt(q.get("scheduled_close_time") or p.get("scheduled_close_time")),
            "scheduled_resolve_time": _dt(q.get("scheduled_resolve_time") or p.get("scheduled_resolve_time")),
            "actual_resolve_time": _dt(q.get("actual_resolve_time")),
            "status": q.get("status") or p.get("status"),
            # Only used to drop annulled/ambiguous questions (protocol §3 eligibility). Never used to select.
            "resolution": q.get("resolution"),
            "tournament_slugs": [t.get("slug") for t in (p.get("projects") or {}).get("tournament", []) or []],
        })
    return rows


def fetch(session: requests.Session) -> list[dict]:
    rows: list[dict] = []
    for name, tid in TOURNAMENTS.items():
        for status in ("resolved", "closed", "open"):
            offset = 0
            while True:
                data = _get(session, {"tournaments": tid, "statuses": status, "limit": 100, "offset": offset,
                                      "with_cp": "false"})
                res = data.get("results", [])
                for p in res:
                    rows += _rows_from_post(p, name)
                if len(res) < 100:
                    break
                offset += 100
        print(f"{name}: {sum(1 for r in rows if r['source'] == name)} questions")
    return rows


def minibench_rows(session: requests.Session) -> list[dict]:
    """MiniBench rounds are separate tournaments whose slugs start with 'minibench'; find them via search."""
    rows: list[dict] = []
    seen: set[int] = set()
    for status in ("resolved", "closed"):
        offset = 0
        while True:
            data = _get(session, {"search": "", "tournaments": MINIBENCH_TAG, "statuses": status, "limit": 100, "offset": offset})
            res = data.get("results", [])
            for p in res:
                if p["id"] not in seen:
                    seen.add(p["id"])
                    rows += _rows_from_post(p, "minibench")
            if len(res) < 100:
                break
            offset += 100
    print(f"minibench (current slug only): {len(rows)} questions")
    return rows


def eligible(rows: list[dict], cutoff: str) -> list[dict]:
    """Protocol §3: opened after the cutoff, resolved, not annulled/ambiguous."""
    return [
        r for r in rows
        if r["open_time"] and r["open_time"] > cutoff
        and r["status"] == "resolved"
        and r["resolution"] not in ("annulled", "ambiguous")  # None = hidden from bot accounts; counted, flagged in the report
    ]


def report(rows: list[dict]) -> str:
    out = []
    by_src = defaultdict(Counter)
    for r in rows:
        by_src[r["source"]][(r["status"], r["type"])] += 1
    out.append("## Questions by tournament, status and type\n")
    out.append("| Tournament | Questions | Resolved | Annulled/ambiguous | binary | multiple_choice | numeric | discrete | date | First opened | Last opened |")
    out.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for src in sorted(by_src, key=lambda s: min((r["open_time"] or "9") for r in rows if r["source"] == s)):
        rs = [r for r in rows if r["source"] == src]
        types = Counter(r["type"] for r in rs)
        resolved = sum(1 for r in rs if r["status"] == "resolved" and r["resolution"] not in ("annulled", "ambiguous"))
        annulled = sum(1 for r in rs if r["resolution"] in ("annulled", "ambiguous"))
        opens = sorted(r["open_time"] for r in rs if r["open_time"])
        out.append(f"| {src} | {len(rs)} | {resolved} | {annulled} | {types['binary']} | {types['multiple_choice']} | "
                   f"{types['numeric']} | {types['discrete']} | {types['date']} | {opens[0][:10] if opens else ''} | {opens[-1][:10] if opens else ''} |")
    out.append("\n## Eligible questions by evaluation-model cutoff\n")
    out.append("Eligible = opened after the cutoff, resolved, not annulled. Binary/MC/numeric/discrete only (the bot skips date questions).\n")
    out.append("| Model released on or before | Eligible | binary | multiple_choice | numeric+discrete | of which Summer 2026 FE | Spring 2026 AIB | Fall 2025 AIB | MiniBench |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    usable = [r for r in rows if r["type"] in ("binary", "multiple_choice", "numeric", "discrete")]
    for cutoff in ["2025-06-01", "2025-08-01", "2025-10-01", "2025-12-01", "2026-01-01", "2026-02-01", "2026-03-01",
                   "2026-04-01", "2026-05-01", "2026-06-01", "2026-07-01"]:
        e = eligible(usable, cutoff + "T00:00:00Z")
        t = Counter(r["type"] for r in e)
        s = Counter(r["source"] for r in e)
        out.append(f"| {cutoff} | {len(e)} | {t['binary']} | {t['multiple_choice']} | {t['numeric'] + t['discrete']} | "
                   f"{s['summer-futureeval-2026']} | {s['spring-aib-2026']} | {s['fall-aib-2025']} | {s['minibench']} |")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")
    if args.refresh or not CACHE.exists():
        s = requests.Session()
        s.headers["Authorization"] = f"Token {os.environ['METACULUS_TOKEN']}"
        rows = fetch(s) + minibench_rows(s)
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        with CACHE.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"cached {len(rows)} rows at {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC -> {CACHE}")
    rows = [json.loads(l) for l in CACHE.read_text(encoding="utf-8").splitlines()]
    print(report(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
