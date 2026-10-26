"""B-32 real A/A test: replay N resolved live records twice with the same config and check the report finds no difference.

    poetry run python tools/run_aa_replay.py --dry-run          # pick records, estimate the cost; no model calls, no key needed
    poetry run python tools/run_aa_replay.py                    # replay 60 records twice (arms A and A2), cheap config, $5 cap
    poetry run python -m evals.validate_harness --real data/experiments/B-32-aa/<run>/runs.csv

Live-replay (`evals.replay.replay_record`): each record's frozen research and as_of are reused, so the two arms see identical
inputs and differ only by the models' own randomness. A harness that is right finds no difference (the CI contains 0) about 19
times in 20. Records are picked from data/runs/ (one per question: the earliest non-shadow record), binary questions that resolved
yes/no in data/outcomes/outcomes.jsonl, oldest as_of first. A record is skipped when any forecaster in the config fails the
release-date check (`evals.model_release`, protocol T1) or the replay leakage guard refuses it (research dated after as_of).

The config's polarity check is switched off: its model has no verified release date in `evals.model_release`, and an A/A test
only needs both arms to be the same config. Both arms of a question run at the same time (same provider conditions, T11).

Cost: estimated before anything runs from the mean cost per call of each model in the archived records (a model with no history
counts at FALLBACK_CALL_USD), times SAFETY. The run refuses to start if the estimate is above --max-cost (default $5), and a hard
cap stops further calls once spent + one call's reserve would cross it. Outputs go to data/experiments/B-32-aa/<run>/ (private
data repo: runs.csv, report.md, and the replayed records, which hold research text); spend is appended to
docs/experiments/budget-ledger.md. Nothing printed here contains research or article text.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals import report  # noqa: E402
from evals.asof import LeakageError  # noqa: E402
from evals.model_release import IneligiblePair, check_eligible  # noqa: E402
from evals.outcomes import load_outcomes  # noqa: E402
from evals.replay import assert_record_as_of, live_records, replay_record  # noqa: E402
from forecast_engine.config import DEFAULT_CONFIG, TOURNAMENT_CONFIG, BotConfig  # noqa: E402
from forecast_engine.core import ForecastFailed  # noqa: E402
from forecast_engine.schema import ForecastRecord, LlmCall  # noqa: E402

DATA = ROOT / "data"
RUNS = DATA / "runs"
OUTCOMES = DATA / "outcomes" / "outcomes.jsonl"
OUT = DATA / "experiments" / "B-32-aa"
LEDGER = ROOT / "docs" / "experiments" / "budget-ledger.md"
CONFIGS = {"cheap": DEFAULT_CONFIG, "frontier": TOURNAMENT_CONFIG}
ARMS = ("A", "A2")
FALLBACK_CALL_USD = 0.20  # per call, for a model with no cost history: deliberately high
MIN_CALL_USD = 0.001  # floor per call, so a history of free or unreported calls can't make the estimate (and the cap's reserve) 0
SAFETY = 1.5  # the estimate is the historical mean × this; the hard cap reserves 3× the mean per call
COLUMNS = ("question_id", "cluster_id", "arm", "member", "repeat", "p", "outcome", "question_type", "source", "failed", "error",
           "cost_usd", "tokens", "calls", "resolved_at")


def aa_config(name: str) -> BotConfig:
    return replace(CONFIGS[name], polarity_check_model=None)


def resolved_binary(outcomes: dict[int, dict[str, Any]]) -> dict[int, tuple[int, str]]:
    """question_id -> (1/0, resolved date) for binary questions that resolved yes or no."""
    out = {}
    for qid, row in outcomes.items():
        res = str(row.get("resolution") or "").lower()
        if row.get("status") == "resolved" and res in ("yes", "no"):
            out[qid] = (1 if res == "yes" else 0, str(row.get("actual_resolve_time") or "")[:10])
    return out


def pick_records(runs_dir: Path, outcomes: dict[int, dict[str, Any]], cfg: BotConfig, n: int):
    """(picked [(path, record, outcome, resolved_at)], skip counts by reason). One record per question, oldest as_of first."""
    resolved = resolved_binary(outcomes)
    by_q: dict[int, list[tuple[Path, dict[str, Any]]]] = defaultdict(list)
    for path, rec in live_records(runs_dir):
        q = rec.get("question", {})
        if q.get("question_type") == "binary" and q.get("question_id") in resolved:
            by_q[q["question_id"]].append((path, rec))
    skips: dict[str, int] = defaultdict(int)
    candidates = []
    for qid, recs in by_q.items():
        live = [x for x in recs if "-shadow-" not in x[0].parent.name] or recs
        path, rec = min(live, key=lambda x: (x[1]["as_of"], str(x[0])))
        try:
            for f in cfg.forecasters:
                check_eligible(f.model, rec["as_of"])
        except IneligiblePair:
            skips["a model in the config was released too late for this record (T1)"] += 1
            continue
        try:
            assert_record_as_of(rec)
        except LeakageError:
            skips["research dated after as_of (replay guard)"] += 1
            continue
        candidates.append((path, rec, *resolved[qid]))
    candidates.sort(key=lambda x: (x[1]["as_of"], x[1]["question"]["question_id"]))
    if len(candidates) > n:
        skips[f"beyond --n {n}"] += len(candidates) - n
    return candidates[:n], dict(skips)


def call_costs(runs_dir: Path) -> dict[str, float]:
    """Mean reported cost per successful forecaster call, per model, over every archived live record."""
    seen: dict[str, list[float]] = defaultdict(list)
    for _, rec in live_records(runs_dir):
        for f in rec.get("forecasters", []):
            c = f.get("call") or {}
            if c.get("cost_usd") is not None and not c.get("error"):
                seen[f["model"]].append(float(c["cost_usd"]))
    return {m: sum(v) / len(v) for m, v in seen.items()}


def estimate(n_records: int, cfg: BotConfig, costs: dict[str, float]) -> tuple[float, dict[str, float]]:
    per_call = {f.model: max(costs.get(f.model, FALLBACK_CALL_USD), MIN_CALL_USD) for f in cfg.forecasters}
    return len(ARMS) * n_records * sum(per_call.values()) * SAFETY, per_call


class CappedLlm:
    """Wraps an LlmClient: a call that could cross the cap is not made (it comes back as a failed call instead)."""

    def __init__(self, inner, cap: float, reserve: dict[str, float]) -> None:
        self.inner, self.cap, self.reserve = inner, cap, reserve
        self.spent = self.reserved = 0.0
        self.calls = self.refused = 0

    async def complete(self, *, model, prompt, purpose, max_tokens, reasoning_effort=None) -> LlmCall:
        r = 3.0 * self.reserve.get(model, FALLBACK_CALL_USD)
        if self.spent + self.reserved + r > self.cap:
            self.refused += 1
            return LlmCall(purpose=purpose, provider="none", model=model, params={}, prompt=prompt,
                           requested_at=datetime.now(timezone.utc), error="budget cap reached (call not made)")
        self.reserved += r
        try:
            c = await self.inner.complete(model=model, prompt=prompt, purpose=purpose, max_tokens=max_tokens,
                                          reasoning_effort=reasoning_effort)
        finally:
            self.reserved -= r
        self.spent += c.cost_usd if c.cost_usd is not None else r  # an unreported cost counts as the reserve
        self.calls += 1
        return c


def _error_kind(f) -> str:
    if f.call.error:
        return "budget" if "budget cap" in f.call.error else "call"
    if f.parse_error:
        return "parse"
    return "excluded" if f.excluded else ""


def table_rows(rec: ForecastRecord, arm: str, outcome: int, resolved_at: str, failed: bool) -> list[dict[str, Any]]:
    q = rec.question
    base = {"question_id": q.question_id, "cluster_id": f"post:{q.post_id or q.question_id}", "arm": arm, "repeat": 1,
            "outcome": outcome, "question_type": "binary", "source": ",".join(q.tournaments) or "live",
            "resolved_at": resolved_at}
    rows = []
    for f in rec.forecasters:
        tokens = (f.call.tokens_in or 0) + (f.call.tokens_out or 0) if f.call.tokens_in is not None else ""
        rows.append({**base, "member": f.model, "p": f.prediction if f.ok else "", "failed": int(not f.ok),
                     "error": _error_kind(f), "cost_usd": f.call.cost_usd if f.call.cost_usd is not None else "",
                     "tokens": tokens, "calls": 0 if f.call.provider == "none" else 1})
    rows.append({**base, "member": report.AGGREGATE, "p": "" if failed or rec.aggregate is None else rec.aggregate,
                 "failed": int(failed or rec.aggregate is None), "error": "", "cost_usd": "", "tokens": "", "calls": 0})
    return rows


async def replay_both(picked, cfg: BotConfig, llm, parallel: int, run_dir: Path, runs_dir: Path) -> list[dict[str, Any]]:
    sem = asyncio.Semaphore(parallel)

    async def one(path: Path, rec: dict[str, Any], outcome: int, resolved_at: str, arm: str) -> list[dict[str, Any]]:
        async with sem:
            try:
                out, failed = await replay_record(rec, cfg, llm), False
            except ForecastFailed as e:
                out, failed = e.record, True
        dest = run_dir / "records" / arm / f"q{out.question.question_id}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        src = path.relative_to(runs_dir).as_posix() if path.is_relative_to(runs_dir) else path.name
        dest.write_text(json.dumps({**out.to_dict(), "replayed_from": src}, indent=1, ensure_ascii=False), encoding="utf-8")
        return table_rows(out, arm, outcome, resolved_at, failed)

    jobs = [one(p, r, o, d, arm) for p, r, o, d in picked for arm in ARMS]
    return [row for rows in await asyncio.gather(*jobs) for row in rows]


def write_table(rows: list[dict[str, Any]], path: Path) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(COLUMNS))
        w.writeheader()
        w.writerows(rows)


def append_ledger(run: str, calls: int, spent: float, note: str, ledger: Path = LEDGER) -> None:
    """Same table as tools/run_structured_pilot.py writes (the ledger counts every paid experiment run)."""
    if not ledger.exists():
        return
    rows = [ln for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.startswith("| 20")]
    cum = sum(float(ln.split("|")[6].strip().lstrip("$")) for ln in rows) + spent
    with ledger.open("a", encoding="utf-8") as f:
        f.write(f"| {run[:4]}-{run[4:6]}-{run[6:8]} | B-32 | {run} | {note} | {calls} | ${spent:.3f} | ${cum:.3f} | ${180 - cum:.2f} |\n")


def run(args, llm=None, *, runs_dir: Path = RUNS, outcomes_path: Path = OUTCOMES, out_root: Path = OUT,
        ledger: Path = LEDGER) -> int:
    cfg = aa_config(args.config)
    picked, skips = pick_records(runs_dir, load_outcomes(outcomes_path), cfg, args.n)
    history = call_costs(runs_dir)
    est, per_call = estimate(len(picked), cfg, history)
    print(f"config {args.config} (hash {cfg.config_hash()}, polarity check off), {len(cfg.forecasters)} forecasters; "
          f"arms {' and '.join(ARMS)}")
    print(f"{len(picked)} resolved binary records picked" + (f" (asked for {args.n})" if len(picked) < args.n else ""))
    for reason, k in sorted(skips.items()):
        print(f"  skipped {k}: {reason}")
    for m, c in per_call.items():
        print(f"  {m}: ${c:.4f}/call" + (" (no cost history: fallback)" if m not in history else " (mean of archive)"))
    print(f"estimated cost: ${est:.2f} = {len(ARMS)} arms × {len(picked)} records × ${sum(per_call.values()):.4f} × {SAFETY} "
          f"safety (cap ${args.max_cost:.2f})")
    if not picked:
        print("nothing to replay")
        return 1
    if est > args.max_cost:
        print(f"REFUSED: the estimate ${est:.2f} is above --max-cost ${args.max_cost:.2f}; lower --n or raise --max-cost")
        return 2
    if args.dry_run:
        print("dry run: no model was called")
        return 0
    if llm is None:
        from dotenv import load_dotenv

        from forecast_engine.clock import SystemClock
        from forecast_engine.llm import OpenRouterClient

        load_dotenv(ROOT / ".env")
        llm = OpenRouterClient(SystemClock())
    capped = CappedLlm(llm, args.max_cost, per_call)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = out_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    rows = asyncio.run(replay_both(picked, cfg, capped, args.parallel, run_dir, runs_dir))
    table = run_dir / "runs.csv"
    write_table(rows, table)
    print(f"done: {capped.calls} calls, ${capped.spent:.4f} spent, {capped.refused} calls refused by the cap")
    an = report.analyse(report.read_table(table), ARMS[0], name="B-32 A/A replay", decision_arms=[ARMS[1]], meta=report.provenance(table))
    (run_dir / "report.md").write_text(report.render_report(an), encoding="utf-8", newline="\n")
    c = an.comparisons[ARMS[1]][an.metric]
    print(f"{ARMS[1]} vs {ARMS[0]}: N {c.n}, log Δ {report._f(c.mean_diff)}, 95% CI [{report._f(c.ci_low)}, {report._f(c.ci_high)}]"
          f": {c.verdict_words()}")
    print(f"table: {table}\nnext: poetry run python -m evals.validate_harness --real {table}")
    if capped.calls:
        append_ledger(run_id, capped.calls, capped.spent, f"A/A replay, {len(picked)} binary records × 2, {args.config} config",
                      ledger)
    return 0


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=60, help="number of resolved binary records to replay (default 60)")
    ap.add_argument("--config", choices=sorted(CONFIGS), default="cheap", help="config replayed in both arms (default cheap)")
    ap.add_argument("--max-cost", type=float, default=5.0, help="refuse above this estimate; hard cap on spend in $ (default 5)")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true", help="pick records and estimate the cost; no model calls")
    return ap


def main(argv: list[str] | None = None) -> int:
    return run(parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
