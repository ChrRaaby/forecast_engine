"""B-51a outcome screen (offline, $0, read-only): do questions with a wrong weekday in their research get a lower peer score?

    poetry run python tools/research_flag_outcomes.py [--runs-dir <path>/data/runs] [--outcomes <path>/outcomes.jsonl]

One row per scored question: the earliest record's research (flagged = at least one `weekday_mismatch`), and the official Metaculus peer
score of our forecast from the last outcomes.jsonl row for the question. Reports flagged minus unflagged mean peer score with a 95%
bootstrap CI (seed 0, 10,000 resamples), for binary questions alone and for all types. Gate (B-51b, docs/research/15): at >= 150
scored binary questions, if the CI upper bound for binary is below 0, propose B-51b as an arm; otherwise close it. Observational and
suggestive only. Output is counts and scores, no article text.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from statistics import fmean
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals import outcomes as oc  # noqa: E402
from evals.replay import RUNS  # noqa: E402
from evals.research_cache import bundle_from_dict  # noqa: E402
from forecast_engine.research_checks import check_bundle  # noqa: E402

GATE_N = 150


def rows_from_records(recs: list[dict[str, Any]], outcomes: dict[int, dict[str, Any]], flag: str = "weekday_mismatch") -> list[dict]:
    first: dict[int, dict] = {}
    for rec in recs:
        qid = (rec.get("question") or {}).get("question_id")
        if qid is not None and (qid not in first or str(rec.get("as_of")) < str(first[qid].get("as_of"))):
            first[qid] = rec
    rows = []
    for qid, rec in sorted(first.items()):
        peer = ((outcomes.get(qid) or {}).get("scores") or {}).get("peer_score")
        if not isinstance(peer, (int, float)):
            continue
        flagged = any(f["flag"] == flag for f in check_bundle(bundle_from_dict(rec["research"])))
        rows.append({"qid": qid, "type": rec["question"].get("question_type"), "flagged": flagged, "peer": float(peer)})
    return rows


def bootstrap_diff(a: list[float], b: list[float], n: int = 10_000, seed: int = 0) -> tuple[float, float, float]:
    rng = random.Random(seed)
    diffs = sorted(fmean(rng.choices(a, k=len(a))) - fmean(rng.choices(b, k=len(b))) for _ in range(n))
    return fmean(a) - fmean(b), diffs[int(0.025 * n)], diffs[int(0.975 * n) - 1]


def summarise(rows: list[dict]) -> dict[str, Any]:
    fl, un = [r["peer"] for r in rows if r["flagged"]], [r["peer"] for r in rows if not r["flagged"]]
    out: dict[str, Any] = {"n": len(rows), "n_flagged": len(fl), "n_unflagged": len(un)}
    if len(fl) >= 2 and len(un) >= 2:
        out.update(flagged_mean=fmean(fl), unflagged_mean=fmean(un))
        out["diff"], lo, hi = bootstrap_diff(fl, un)
        out["ci"] = (lo, hi)
    return out


def gate(binary: dict[str, Any]) -> str:
    if binary["n"] < GATE_N:
        return f"gate not reached ({binary['n']} of {GATE_N} scored binary questions)"
    if "ci" not in binary:
        return "gate reached but too few flagged questions to compare: close B-51b as not worth it"
    return ("CI upper bound < 0: propose B-51b as an arm" if binary["ci"][1] < 0
            else "CI upper bound >= 0: close B-51b as not worth it")


def report(rows: list[dict]) -> str:
    lines = []
    binary = summarise([r for r in rows if r["type"] == "binary"])
    for label, s in (("binary", binary), ("all types", summarise(rows))):
        line = f"{label}: scored n={s['n']}, flagged {s['n_flagged']}, unflagged {s['n_unflagged']}"
        if "ci" in s:
            line += (f"; mean peer score flagged {s['flagged_mean']:+.1f} vs unflagged {s['unflagged_mean']:+.1f}; "
                     f"flagged - unflagged {s['diff']:+.1f}, 95% CI [{s['ci'][0]:+.1f}, {s['ci'][1]:+.1f}]")
        lines.append(line)
    lines.append("gate: " + gate(binary))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs-dir", default=str(RUNS))
    ap.add_argument("--outcomes", default=str(oc.OUT))
    a = ap.parse_args(argv)
    recs = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(a.runs_dir).rglob("q*.json")) if "test_questions" not in p.parent.name]
    print(report(rows_from_records(recs, oc.load_outcomes(Path(a.outcomes)))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
