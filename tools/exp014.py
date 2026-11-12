"""EXP-014 pooled ensemble (B-92): live median of 5 frontier answers (arm A) vs the median of those 5 plus the same run's 3 cheap
shadow answers (arm B), on resolved live records. $0: no model calls. Card: docs/experiments/EXP-014-pooled-ensemble.md.

    poetry run python tools/exp014.py --data data                      # screen: every resolved pair
    poetry run python tools/exp014.py --data data --resolved-after 2026-11-12   # the card's deciding rerun

Primary metric: Metaculus baseline score per question across all types (protocol §4), B - A, cluster bootstrap 95% CI.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from evals import exp010, scoring  # noqa: E402
from evals import outcomes as oc  # noqa: E402
from evals.report import cluster_bootstrap_ci  # noqa: E402
from forecast_engine import parsing  # noqa: E402

SEED = 1014
REPS = 10_000
SESOI = 0.75  # baseline points per question (card)
LN2_POINTS = 100 / math.log(2)  # binary log score (nats) -> baseline points


def usable(rec: dict) -> list:
    return [f["prediction"] for f in rec.get("forecasters", []) if f.get("prediction") is not None and f.get("excluded") is None]


def median(qtype: str, preds: list):
    if qtype == "binary":
        return parsing.median_binary([float(p) for p in preds])
    if qtype == "multiple_choice":
        return parsing.median_multiple_choice(preds)
    return parsing.median_numeric([[(float(a), float(b)) for a, b in p] for p in preds])


def same(qtype: str, a, b) -> bool:
    if qtype == "binary":
        return abs(float(a) - float(b)) < 1e-9
    if qtype == "multiple_choice":
        return set(a) == set(b) and all(abs(a[k] - b[k]) < 1e-9 for k in a)
    return len(a) == len(b) and all(abs(float(x[1]) - float(y[1])) < 1e-9 for x, y in zip(a, b))


def load_pairs(runs: Path) -> tuple[dict[int, tuple[dict, dict]], Counter]:
    """First published frontier record per question with a same-run shadow-cheap record."""
    dropped: Counter = Counter()
    pairs: dict[int, tuple[str, dict, dict]] = {}
    for path in sorted(runs.glob("*/data/forecasts/*-tournament/q*.json")):
        rec = json.loads(path.read_text(encoding="utf-8"))
        if rec.get("status") != "ok" or not rec.get("published") or "frontier" not in str(rec.get("config_version")):
            continue
        shadow = path.parent.with_name(path.parent.name + "-shadow-cheap") / path.name
        if not shadow.exists():
            dropped["no same-run cheap shadow record"] += 1
            continue
        sh = json.loads(shadow.read_text(encoding="utf-8"))
        qid = int(rec["question"]["question_id"])
        key = str(rec.get("as_of", ""))
        if qid in pairs:
            dropped["later record of the same question"] += 1
            if key >= pairs[qid][0]:
                continue
        pairs[qid] = (key, rec, sh)
    return {q: (r, s) for q, (_, r, s) in pairs.items()}, dropped


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", type=Path, default=ROOT / "data", help="folder holding runs/ and outcomes/")
    ap.add_argument("--resolved-after", type=date.fromisoformat, default=None)
    ap.add_argument("--json", type=Path, default=None, help="write per-question rows here")
    args = ap.parse_args(argv)

    from exp010 import LIVE  # tools/exp010.py: the live CDF builder (imports the live adapter)

    pairs, dropped = load_pairs(args.data / "runs")
    outcomes = oc.load_outcomes(args.data / "outcomes" / "outcomes.jsonl")
    scaling = exp010.load_scaling(args.data / "outcomes" / "scaling.jsonl")
    rows = []
    for qid, (rec, sh) in sorted(pairs.items()):
        snap = rec["question"]
        qtype = snap["question_type"]
        row = outcomes.get(qid)
        res = str((row or {}).get("resolution") or "").strip().lower()
        if row is None or row.get("status") not in oc.SETTLED or not res:
            dropped["not resolved yet"] += 1
            continue
        if res in oc.NO_SCORE_RESOLUTIONS:
            dropped["annulled or ambiguous"] += 1
            continue
        resolved_at = oc.resolved_date(row)
        if args.resolved_after is not None and (resolved_at is None or date.fromisoformat(resolved_at) <= args.resolved_after):
            dropped["resolved on or before --resolved-after"] += 1
            continue
        live, cheap = usable(rec), usable(sh)
        if not cheap:
            dropped["cheap shadow has no usable answer"] += 1
            continue
        a = median(qtype, live)
        if not same(qtype, a, rec["aggregate"]):
            dropped["recomputed live median differs from the stored aggregate"] += 1
            continue
        b = median(qtype, live + cheap)
        if qtype == "binary":
            o = 1 if res == "yes" else 0
            sa, sb = scoring.binary_baseline_score(a, o), scoring.binary_baseline_score(b, o)
            la, lb = scoring.binary_log_score(a, o), scoring.binary_log_score(b, o)
        elif qtype == "multiple_choice":
            opts = list(a.keys())
            if res not in [x.lower() for x in opts]:
                dropped["MC resolution not among the options"] += 1
                continue
            idx = [x.lower() for x in opts].index(res)
            sa = scoring.mc_baseline_score([a[k] for k in opts], idx)
            sb = scoring.mc_baseline_score([b[k] for k in opts], idx)
            la = lb = None
        else:
            sc = scaling.get(qid)
            if sc is None:
                dropped["numeric/discrete without stored scaling"] += 1
                continue
            if not exp010._bounds_match(snap, sc):
                dropped["stored scaling disagrees with the record"] += 1
                continue
            axis = exp010.axis_from(snap, sc)
            loc = exp010.outcome_location(res, axis)
            if loc is None or math.isnan(loc):
                dropped["resolution not a number or bound"] += 1
                continue
            try:
                ca, cb = LIVE.build(axis, [tuple(map(float, p)) for p in a]), LIVE.build(axis, [tuple(map(float, p)) for p in b])
            except Exception:  # noqa: BLE001 - the live path would have failed to submit
                dropped["live CDF conversion failed"] += 1
                continue
            sa = exp010.score_cdf(ca, loc, axis, ("baseline",))["baseline"]
            sb = exp010.score_cdf(cb, loc, axis, ("baseline",))["baseline"]
            la = lb = None
        rows.append(dict(question_id=qid, cluster=str(snap.get("post_id") or qid), type=qtype,
                         era=rec.get("config_version"), resolved_at=resolved_at, n_live=len(live), n_cheap=len(cheap),
                         a=sa, b=sb, d=sb - sa, log_a=la, log_b=lb))

    print(f"EXP-014 pooled ensemble, {'screen' if args.resolved_after is None else f'rerun, resolved after {args.resolved_after}'}")
    print("dropped:", dict(dropped))
    if not rows:
        print("no questions")
        return 0
    d = np.array([r["d"] for r in rows])
    lo, hi, se = cluster_bootstrap_ci(d, [r["cluster"] for r in rows], reps=REPS, seed=SEED)
    sd = float(d.std(ddof=1)) if len(d) > 1 else float("nan")
    wins = float(np.mean(np.where(d > 0, 1.0, np.where(d == 0, 0.5, 0.0))))
    print(f"PRIMARY  N={len(d)} clusters={len({r['cluster'] for r in rows})}  mean B-A = {d.mean():+.3f} baseline points/q  "
          f"95% CI [{lo:+.3f}, {hi:+.3f}]  median {np.median(d):+.3f}  SD {sd:.3f}  MDE≈{2.8 * sd / math.sqrt(len(d)):.3f} (cluster-aware {2.8 * se:.3f})  "
          f"B wins {wins:.0%} (ties half; {int((d == 0).sum())} identical)  SESOI {SESOI}")
    print(f"mean score A {np.mean([r['a'] for r in rows]):.2f}  B {np.mean([r['b'] for r in rows]):.2f}")
    by: dict[str, list] = defaultdict(list)
    for r in rows:
        by[r["type"]].append(r)
        by["era " + str(r["era"])].append(r)
    for k, rs in sorted(by.items()):
        dd = np.array([r["d"] for r in rs])
        print(f"  {k:28s} n={len(rs):3d} mean {dd.mean():+.3f}  (descriptive)")
    bl = [r for r in rows if r["log_a"] is not None]
    if bl:
        dl = np.array([r["log_b"] - r["log_a"] for r in bl])
        print(f"  binary log score B-A n={len(bl)} mean {dl.mean():+.4f} nats (descriptive)")
    if args.json:
        args.json.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
