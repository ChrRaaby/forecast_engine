"""Synthetic forecast tables for the report generator (B-31): tests, the PR example, and trying the report without real data.

    poetry run python -m evals.report_demo out.csv                 # write a synthetic long table
    poetry run python -m evals.report out.csv --baseline A --name DEMO --rule exp005 --out <scratch>/DEMO-report.md

Everything here is made up: no real questions, forecasts or news text (the repo is public). Each question has a true
probability q (questions in one cluster share a cluster effect), the outcome is drawn from q, and each member forecasts q
blurred by noise on the log-odds scale. Arms differ in that noise (`arm_noise`), in overconfidence (`arm_scale` stretches the
log-odds: 1 = calibrated, > 1 = too extreme) and an optional bias, so a calibrated low-noise arm is genuinely better.
"""
from __future__ import annotations

import argparse
import csv
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

COLUMNS = (
    "question_id", "cluster_id", "arm", "member", "repeat", "p", "outcome",
    "question_type", "source", "failed", "error", "cost_usd", "tokens",
)
SOURCES = ("tournament", "main-site", "benchmark-forward")


def _sigmoid(x: np.ndarray | float) -> np.ndarray | float:
    return 1.0 / (1.0 + np.exp(-x))


def synthetic_rows(
    n_questions: int = 120,
    n_clusters: int = 50,
    members: Sequence[str] = ("model-a", "model-b", "model-c"),
    arm_noise: dict[str, float] | None = None,
    arm_bias: dict[str, float] | None = None,
    arm_scale: dict[str, float] | None = None,
    arm_failure: dict[str, float] | None = None,
    arm_cost: dict[str, float] | None = None,
    k: int = 3,
    cluster_sd: float = 1.0,
    seed: int = 0,
    with_aggregate: bool = False,
) -> list[dict[str, Any]]:
    """Rows in the report's input format (one per arm × member × question × repeat)."""
    arm_noise = arm_noise or {"A": 1.0, "B": 0.6}
    arm_bias = arm_bias or {}
    arm_scale = arm_scale or {}
    arm_failure = arm_failure or {}
    arm_cost = arm_cost or {}
    rng = np.random.default_rng(seed)
    cluster_of = rng.integers(0, n_clusters, n_questions)
    cluster_effect = rng.normal(0.0, cluster_sd, n_clusters)
    logit_q = cluster_effect[cluster_of] + rng.normal(0.0, 1.0, n_questions)
    q = _sigmoid(logit_q)
    outcome = (rng.random(n_questions) < q).astype(int)
    source = rng.choice(SOURCES, n_questions, p=(0.5, 0.2, 0.3))
    rows: list[dict[str, Any]] = []
    for arm, noise in arm_noise.items():
        for i in range(n_questions):
            for rep in range(1, k + 1):
                ps = []
                for member in members:
                    failed = rng.random() < arm_failure.get(arm, 0.0)
                    logit_p = arm_scale.get(arm, 1.0) * logit_q[i] + arm_bias.get(arm, 0.0) + rng.normal(0.0, noise)
                    p = float(np.clip(_sigmoid(logit_p), 0.01, 0.99))  # clipped like live (binary_clip)
                    ps.append(None if failed else p)
                    rows.append(
                        {
                            "question_id": f"Q{i:04d}",
                            "cluster_id": f"C{cluster_of[i]:03d}",
                            "arm": arm,
                            "member": member,
                            "repeat": rep,
                            "p": "" if failed else round(p, 4),
                            "outcome": int(outcome[i]),
                            "question_type": "binary",
                            "source": str(source[i]),
                            "failed": int(failed),
                            "error": "parse" if failed else "",
                            "cost_usd": round(arm_cost.get(arm, 0.01) * (0.8 + 0.4 * rng.random()), 5),
                            "tokens": int(3000 * (0.8 + 0.4 * rng.random())),
                        }
                    )
                if with_aggregate:
                    ok = [p for p in ps if p is not None]
                    rows.append(
                        {
                            "question_id": f"Q{i:04d}", "cluster_id": f"C{cluster_of[i]:03d}", "arm": arm,
                            "member": "aggregate", "repeat": rep, "p": round(float(np.median(ok)), 4) if ok else "",
                            "outcome": int(outcome[i]), "question_type": "binary", "source": str(source[i]),
                            "failed": int(not ok), "error": "", "cost_usd": "", "tokens": "",
                        }
                    )
    return rows


def write_csv(rows: list[dict[str, Any]], path: str | Path) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(COLUMNS) + sorted({k for r in rows for k in r} - set(COLUMNS)))
        w.writeheader()
        w.writerows(rows)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m evals.report_demo", description="write a synthetic forecast table")
    ap.add_argument("out")
    ap.add_argument("--questions", type=int, default=120)
    ap.add_argument("--clusters", type=int, default=50)
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)
    rows = synthetic_rows(
        args.questions, args.clusters, k=args.k, seed=args.seed,
        arm_noise={"A": 0.8, "B": 0.5, "C": 0.8}, arm_scale={"A": 1.6, "B": 1.0, "C": 1.6},
        arm_failure={"A": 0.01, "B": 0.03, "C": 0.08}, arm_cost={"A": 0.01, "B": 0.03, "C": 0.05},
    )
    write_csv(rows, args.out)
    print(f"wrote {len(rows)} synthetic rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
