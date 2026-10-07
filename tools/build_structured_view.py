"""Standalone page for an EXP-005 protocol pilot run: base rate -> scenarios -> driver waterfall -> both routes -> final.

    poetry run python tools/build_structured_view.py                 # latest pilot run
    poetry run python tools/build_structured_view.py --run <run dir name>

Reads data/experiments/EXP-005/<run>/ (written by tools/run_structured_pilot.py) and writes dashboard/exp005-pilot.html.
Unscored: the page is for inspecting how the protocol behaves, not for judging accuracy.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from forecast_engine.structured import TOTAL_ODDS_CAP, logit, sigmoid  # noqa: E402

RUNS = ROOT / "data" / "experiments" / "EXP-005"
TEMPLATE = Path(__file__).with_name("exp005_pilot_template.html")
OUT = ROOT / "dashboard" / "exp005-pilot.html"


def waterfall(p0: float, factors: list[float], cap: float = TOTAL_ODDS_CAP) -> list[dict]:
    """Probability after each driver, applying the odds factors in order; the last step absorbs the total cap if it binds."""
    steps, x, total = [], logit(p0), 0.0
    lim = math.log(cap)
    for f in factors:
        before = sigmoid(x)
        new_total = min(max(total + math.log(f), -lim), lim)
        x += new_total - total
        total = new_total
        steps.append({"factor": f, "before": before, "after": sigmoid(x), "capped": abs(new_total) >= lim - 1e-12})
    return steps


def load_run(run_dir: Path) -> dict:
    questions: dict[int, dict] = {}
    cost = 0.0
    for f in sorted(run_dir.glob("*/arm*/q*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        cost += sum(c.get("cost_usd") or 0 for c in d["calls"])
        q = questions.setdefault(d["question_id"], {
            "qid": d["question_id"], "title": d["question_text"], "as_of": d["as_of"][:10], "window_days": d["window_days"],
            "live": d["live"], "research": d["research_items"], "answers": []})
        fc = d["forecast"]
        if fc and fc.get("p0") is not None:
            fc["waterfall"] = waterfall(fc["p0"], [dr["factor"] for dr in fc["drivers"]])
        q["answers"].append({"model": d["model"], "arm": d["arm"], "forecast": fc, "error": d["error"],
                             "blind": d["arm"] == "C", "cost": sum(c.get("cost_usd") or 0 for c in d["calls"]),
                             "calls": len(d["calls"]), "tokens_out": sum(c.get("tokens_out") or 0 for c in d["calls"])})
    return {"run": run_dir.name, "cost": cost, "questions": sorted(questions.values(), key=lambda q: q["qid"])}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", help="run directory name under data/experiments/EXP-005 (default: latest)")
    args = ap.parse_args()
    runs = sorted(p for p in RUNS.glob("*-pilot") if p.is_dir())
    if not runs:
        sys.exit("no pilot runs found")
    run_dir = RUNS / args.run if args.run else runs[-1]
    data = load_run(run_dir)
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", blob), encoding="utf-8")
    n = sum(len(q["answers"]) for q in data["questions"])
    print(f"wrote {OUT} ({len(data['questions'])} questions, {n} answers, run cost ${data['cost']:.3f}, "
          f"{OUT.stat().st_size / 1e6:.2f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
