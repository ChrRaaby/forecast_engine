"""B-95 research-noise test: how much does a forecast move when the research is fetched again, compared with when only the
models are asked again? Card: docs/experiments/B-95-research-noise.md.

    poetry run python tools/run_research_noise.py --dry-run          # pick questions, estimate the cost; no calls, no keys
    poetry run python tools/run_research_noise.py --n 30             # the run: needs OpenRouter, AskNews and Gemini keys

For each of N unresolved binary tournament questions (snapshot from the latest live record; scheduled to resolve more than 3
days from now), the live research pipeline (`gather_research`, AskNews latest news + Gemini grounded search) runs TWICE, back
to back (seconds apart: provider randomness, not new news), and the cheap roster forecasts three times:
    S1 = research 1, answers a      S2 = research 1, answers b (same research: sampling noise only)
    R2 = research 2, answers a'     (new research AND new answers: research + sampling noise)
Research noise = what R2 vs S1 moves beyond S2 vs S1. Polarity check off (as in the A/A tools). Nothing is published.
The records (with research text) go to data/experiments/B-95/<run>/ in the private data repo; nothing printed here quotes
research text. The questions resolve later, so the same records can be scored then (descriptive only).

Cost: forecasting calls estimated from the archive (× SAFETY), refused above --max-cost (default $2), hard cap on spend;
2 AskNews calls and 2 Gemini grounded searches per question on top (counted and printed; the AskNews credits come from
the monthly plan). Spend goes to the budget ledger.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import math
import random
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.outcomes import load_outcomes  # noqa: E402
from evals.replay import live_records  # noqa: E402
from evals.replay import snapshot_from_dict  # noqa: E402
from forecast_engine.config import DEFAULT_CONFIG  # noqa: E402
from forecast_engine.core import ForecastFailed, forecast  # noqa: E402


def _aa():
    spec = importlib.util.spec_from_file_location("run_aa_replay", ROOT / "tools" / "run_aa_replay.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


aa = _aa()
OUT = ROOT / "data" / "experiments" / "B-95"
ARMS = ("S1", "S2", "R2")
SEED = 1095
MIN_DAYS_TO_RESOLUTION = 3


def pick_questions(runs_dir: Path, outcomes: dict[int, dict[str, Any]], n: int, now: datetime, seed: int = SEED):
    """Latest live (non-shadow) tournament record per unresolved binary question that resolves > 3 days from now. A seeded
    random sample of n, then in question-id order."""
    latest: dict[int, tuple[str, dict[str, Any]]] = {}
    for path, rec in live_records(runs_dir):
        if "shadow" in path.parent.name or "tournament" not in path.parent.name:
            continue
        q = rec.get("question") or {}
        if q.get("question_type") != "binary":
            continue
        qid = int(q["question_id"])
        if qid not in latest or str(rec.get("as_of")) > latest[qid][0]:
            latest[qid] = (str(rec.get("as_of")), rec)
    cut = now + timedelta(days=MIN_DAYS_TO_RESOLUTION)
    eligible = []
    for qid, (_, rec) in sorted(latest.items()):
        row = outcomes.get(qid) or {}
        if row.get("status") == "resolved":
            continue
        when = row.get("scheduled_resolve_time") or rec["question"].get("scheduled_resolution_time")
        if not when or datetime.fromisoformat(str(when).replace("Z", "+00:00")) <= cut:
            continue
        eligible.append(rec)
    rng = random.Random(seed)
    picked = rng.sample(eligible, min(n, len(eligible)))
    return sorted(picked, key=lambda r: int(r["question"]["question_id"])), len(eligible)


def _logit(p: float) -> float:
    p = min(max(p, 0.01), 0.99)
    return math.log(p / (1 - p))


def summarise(rows: list[dict[str, Any]], reps: int = 10_000, seed: int = SEED) -> list[str]:
    """rows: one per question with aggregate p per arm (None if failed) and the URL overlap of the two AskNews fetches."""
    ok = [r for r in rows if all(r.get(a) is not None for a in ARMS)]
    if not ok:
        return ["no question with all three forecasts"]
    samp = np.array([abs(r["S2"] - r["S1"]) for r in ok])
    res = np.array([abs(r["R2"] - r["S1"]) for r in ok])
    ls = np.array([_logit(r["S2"]) - _logit(r["S1"]) for r in ok])
    lr = np.array([_logit(r["R2"]) - _logit(r["S1"]) for r in ok])
    d = res - samp
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(d), size=(reps, len(d)))
    lo, hi = np.quantile(d[idx].mean(axis=1), [0.025, 0.975])
    extra_var = max(0.0, float(lr.var(ddof=1) - ls.var(ddof=1))) if len(ok) > 1 else float("nan")
    overlap = [r["url_overlap"] for r in ok if r.get("url_overlap") is not None]
    out = [
        f"N {len(ok)} questions with all three forecasts (of {len(rows)})",
        f"same research, answers again (S2 vs S1): mean |Δp| {100 * samp.mean():.1f} pts, SD of Δlogit {ls.std(ddof=1):.3f}",
        f"research fetched again too (R2 vs S1):  mean |Δp| {100 * res.mean():.1f} pts, SD of Δlogit {lr.std(ddof=1):.3f}",
        f"extra movement from research: {100 * d.mean():+.1f} pts (95% CI {100 * lo:+.1f} to {100 * hi:+.1f}); research-only SD "
        f"of Δlogit ≈ {math.sqrt(extra_var):.3f}" if not math.isnan(extra_var) else "research-only SD: n/a",
    ]
    if overlap:
        out.append(f"AskNews articles in common between the two fetches: mean {100 * sum(overlap) / len(overlap):.0f}% of URLs")
    return out


def _urls(bundle) -> set[str]:
    return {i.url for i in bundle.items if i.source == "asknews_latest" and i.url}


def run(args, llm=None, research_fn=None, *, runs_dir: Path = aa.RUNS, outcomes_path: Path = aa.OUTCOMES, out_root: Path = OUT,
        ledger: Path = aa.LEDGER, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    cfg = replace(DEFAULT_CONFIG, polarity_check_model=None)
    picked, n_eligible = pick_questions(runs_dir, load_outcomes(outcomes_path), args.n, now)
    history = aa.call_costs(runs_dir)
    per_call = {f.model: max(history.get(f.model, aa.FALLBACK_CALL_USD), aa.MIN_CALL_USD) for f in cfg.forecasters}
    est = len(ARMS) * len(picked) * sum(per_call.values()) * aa.SAFETY
    print(f"B-95 research noise: cheap roster ({len(cfg.forecasters)} models, polarity check off), {len(picked)} of "
          f"{n_eligible} eligible unresolved binary questions (seed {SEED})")
    print(f"estimated forecasting cost: ${est:.2f} = {len(ARMS)} forecasts × {len(picked)} questions × "
          f"${sum(per_call.values()):.4f} × {aa.SAFETY} safety (cap ${args.max_cost:.2f}); plus {2 * len(picked)} AskNews calls "
          f"and {2 * len(picked)} Gemini grounded searches")
    if not picked:
        print("nothing to test")
        return 1
    if est > args.max_cost:
        print(f"REFUSED: the estimate ${est:.2f} is above --max-cost ${args.max_cost:.2f}; lower --n or raise --max-cost")
        return 2
    if args.dry_run:
        print("dry run: nothing was called")
        return 0
    from forecast_engine.clock import SystemClock

    clock = SystemClock()
    if llm is None or research_fn is None:
        from dotenv import load_dotenv

        from forecast_engine.llm import OpenRouterClient
        from forecast_engine.research import gather_research

        load_dotenv(ROOT / ".env")
        llm = llm or OpenRouterClient(clock)
        research_fn = research_fn or (lambda q: gather_research(q, clock, cfg.research))
    capped = aa.CappedLlm(llm, args.max_cost, per_call)
    run_id = now.strftime("%Y%m%dT%H%M%SZ")
    run_dir = out_root / run_id
    research_cost = 0.0
    sem = asyncio.Semaphore(args.parallel)

    async def one(rec: dict[str, Any]) -> dict[str, Any]:
        nonlocal research_cost
        q = snapshot_from_dict(rec["question"])
        async with sem:
            b1 = await research_fn(q)
            b2 = await research_fn(q)
            research_cost += sum(c.cost_usd or 0.0 for c in b1.calls + b2.calls)
            out: dict[str, Any] = {"question_id": q.question_id}
            for arm, bundle in (("S1", b1), ("S2", b1), ("R2", b2)):
                try:
                    fr = await forecast(q, clock, bundle, cfg, capped)
                except ForecastFailed as e:
                    fr = e.record
                dest = run_dir / "records" / arm / f"q{q.question_id}.json"
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(json.dumps(fr.to_dict(), indent=1, ensure_ascii=False), encoding="utf-8")
                out[arm] = None if fr.aggregate is None else float(fr.aggregate)
            u1, u2 = _urls(b1), _urls(b2)
            out["url_overlap"] = len(u1 & u2) / len(u1 | u2) if (u1 | u2) else None
            out["research_errors"] = len(b1.errors) + len(b2.errors)
            return out

    async def all_():
        return await asyncio.gather(*(one(r) for r in picked))

    run_dir.mkdir(parents=True, exist_ok=True)
    rows = asyncio.run(all_())
    (run_dir / "summary.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    lines = summarise(rows)
    (run_dir / "summary.md").write_text("# B-95 research noise\n\n" + "".join(f"- {ln}\n" for ln in lines), encoding="utf-8")
    spent = capped.spent + research_cost
    print(f"done: {capped.calls} forecasting calls ${capped.spent:.4f} + research ${research_cost:.4f} (Gemini at list price, "
          f"grounding fee not included); {capped.refused} calls refused by the cap; "
          f"{sum(r['research_errors'] for r in rows)} research errors")
    for ln in lines:
        print(ln)
    print(f"records and summary: {run_dir}")
    if capped.calls:
        aa.append_ledger(run_id, capped.calls, spent, f"research-noise test, {len(picked)} binary questions × 3 forecasts, "
                         f"2 research fetches each ({2 * len(picked)} AskNews calls), cheap config", ledger, experiment="B-95")
    return 0


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=30, help="number of questions (default 30)")
    ap.add_argument("--max-cost", type=float, default=2.0, help="refuse above this estimate; hard cap on forecasting spend in $")
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true", help="pick questions and estimate the cost; nothing is called")
    return ap


def main(argv: list[str] | None = None) -> int:
    return run(parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
