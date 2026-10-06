"""Outcome collector (B-38): resolutions for every question the bot has forecast.

Metaculus only returns a closed question's resolution to accounts that forecast on it (resources page; research/05), so this uses
the bot's own token. It reads question ids from the archived run records (data/runs/**/q*.json), asks Metaculus for each one that
isn't settled yet, and keeps one line per question in data/outcomes/outcomes.jsonl (private data repo):

    poetry run python -m evals.outcomes            # update; safe to run repeatedly (settled questions aren't re-fetched)

Each line: question_id, post_id, type, status, resolution (Metaculus's string: "yes"/"no", an option name, a number, a date,
"above_upper_bound"/"below_lower_bound", or "annulled"/"ambiguous"), actual_resolve_time, checked_at, and `scores`: Metaculus's
official scores for the bot's own forecast (`my_forecasts.score_data`: baseline_score, peer_score, coverage, ...; None until the
question resolves). Peer score is our comparison with the other forecasters; the community prediction itself is hidden from our
account. Official scores are time-weighted by coverage, so a forecast made late scores near 0 whatever its accuracy; evals/scoring.py
gives the per-forecast score. A resolved question is re-fetched until its scores appear (rows written before scores were collected
get them on the next run). Requests are spaced, because the live bot shares the token.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

from forecast_engine.clock import Clock, SystemClock

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "data" / "runs"
OUT = ROOT / "data" / "outcomes" / "outcomes.jsonl"
API = "https://www.metaculus.com/api/posts/{post_id}/"
REQUEST_GAP_S = 1.0
SETTLED = ("resolved",)
NO_SCORE_RESOLUTIONS = ("annulled", "ambiguous")  # resolved but never scored


def forecast_questions(runs_dir: Path = RUNS) -> dict[int, dict[str, Any]]:
    """question_id -> {post_id, type} for every question with a live (non-test) record."""
    out: dict[int, dict[str, Any]] = {}
    for path in runs_dir.rglob("q*.json"):
        if "test_questions" in path.parent.name:
            continue
        try:
            q = json.loads(path.read_text(encoding="utf-8"))["question"]
        except (OSError, ValueError, KeyError):
            continue
        if q.get("post_id"):
            out[q["question_id"]] = {"post_id": q["post_id"], "type": q["question_type"]}
    return out


def load_outcomes(path: Path = OUT) -> dict[int, dict[str, Any]]:
    """Latest line per question (the file is append-only)."""
    out: dict[int, dict[str, Any]] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                out[row["question_id"]] = row
    return out


def find_question(post: dict[str, Any], question_id: int) -> dict[str, Any] | None:
    """The question object inside a post: a plain question or one sub-question of a group."""
    q = post.get("question")
    if q and q.get("id") == question_id:
        return q
    for sub in (post.get("group_of_questions") or {}).get("questions", []) or []:
        if sub.get("id") == question_id:
            return sub
    return q if q and not post.get("group_of_questions") else None


def outcome_row(question_id: int, meta: dict[str, Any], post: dict[str, Any], clock: Clock) -> dict[str, Any]:
    q = find_question(post, question_id) or {}
    return {
        "question_id": question_id,
        "post_id": meta["post_id"],
        "type": meta["type"],
        "status": q.get("status") or post.get("status"),
        "resolution": q.get("resolution"),
        "actual_resolve_time": q.get("actual_resolve_time"),
        "scheduled_resolve_time": q.get("scheduled_resolve_time"),
        "scores": (q.get("my_forecasts") or {}).get("score_data") or None,
        "checked_at": clock.now().isoformat(timespec="seconds"),
    }


def is_settled(row: dict[str, Any]) -> bool:
    """Resolved, and scored by Metaculus (or annulled/ambiguous, which are never scored): nothing left to fetch."""
    if row["status"] not in SETTLED or row["resolution"] is None:
        return False
    return bool(row.get("scores")) or row["resolution"] in NO_SCORE_RESOLUTIONS


def update(session: requests.Session, clock: Clock, runs_dir: Path = RUNS, out_path: Path = OUT) -> dict[str, int]:
    known = load_outcomes(out_path)
    todo = {qid: m for qid, m in forecast_questions(runs_dir).items()
            if not (qid in known and is_settled(known[qid]))}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    counts = {"checked": 0, "newly_resolved": 0, "newly_scored": 0, "errors": 0}
    with out_path.open("a", encoding="utf-8") as f:
        for qid, meta in sorted(todo.items()):
            for attempt in range(4):
                resp = session.get(API.format(post_id=meta["post_id"]), timeout=60)
                if resp.status_code != 429:
                    break
                time.sleep(30 * (attempt + 1))
            time.sleep(REQUEST_GAP_S)
            if not resp.ok:
                counts["errors"] += 1
                continue
            row = outcome_row(qid, meta, resp.json(), clock)
            counts["checked"] += 1
            prev = known.get(qid)
            key = ("status", "resolution", "scores")
            if prev is None or tuple(prev.get(k) for k in key) != tuple(row[k] for k in key):
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                was_resolved = prev is not None and prev["status"] in SETTLED and prev["resolution"] is not None
                if row["status"] in SETTLED and row["resolution"] is not None and not was_resolved:
                    counts["newly_resolved"] += 1
                if row["scores"] and not (prev or {}).get("scores"):
                    counts["newly_scored"] += 1
    return counts


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    load_dotenv(ROOT / ".env")
    s = requests.Session()
    s.headers["Authorization"] = f"Token {os.environ['METACULUS_TOKEN']}"
    counts = update(s, SystemClock())
    rows = load_outcomes().values()
    resolved = sum(1 for r in rows if r["status"] in SETTLED and r["resolution"] is not None)
    scored = sum(1 for r in rows if r.get("scores"))
    print(f"checked {counts['checked']} questions, {counts['newly_resolved']} newly resolved, {counts['newly_scored']} newly "
          f"scored, {counts['errors']} errors; {resolved} resolved, {scored} with official scores ({OUT})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
