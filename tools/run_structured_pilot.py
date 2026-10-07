"""EXP-005 protocol pilot (unscored): re-forecast stored binary records with the structured-reasoning protocol.

    poetry run python tools/run_structured_pilot.py --estimate      # prompt sizes and a worst-case cost, no calls
    poetry run python tools/run_structured_pilot.py                 # run arms B and C, frontier + cheap model, $5 hard cap

Live-replay: each record's frozen research and as_of date are reused; every (model, record) pair must pass the release-date check
(evals/model_release.py). Arm B = protocol in one call; arm C = blind base-rate call (question only) + protocol from that base.
Outputs go to data/experiments/EXP-005/<run>/ (private data repo); spend is appended to docs/experiments/budget-ledger.md.
Not scored: the pilot is for inspecting the protocol's behaviour before the card's scored runs.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.model_release import IneligiblePair, check_eligible  # noqa: E402
from forecast_engine import structured as st  # noqa: E402
from forecast_engine.clock import SystemClock  # noqa: E402
from forecast_engine.llm import OpenRouterClient  # noqa: E402

DATA = ROOT / "data"
OUT = DATA / "experiments" / "EXP-005"
LEDGER = ROOT / "docs" / "experiments" / "budget-ledger.md"
# research/11's disputed questions; q43330 (dry run, no research, as_of on gpt-6.1-sol's release day) replaced by q46128.
PILOT_QIDS = [46107, 46082, 46122, 46080, 46113, 46083, 46109, 46076, 8725, 46128]
MODELS = ["openai/gpt-6.1-sol", "openai/gpt-6-luna"]  # frontier (ADR-0009 roster) + current cheap member, same vendor
# Worst-case $ per call (max_tokens of output at list price + ~10k input), used to stop before the cap is crossed.
MAX_CALL_USD = {"openai/gpt-6.1-sol": 0.18, "openai/gpt-6-luna": 0.01}
MAX_TOKENS = 16000
REASONING = "medium"


def pick_records(qids: list[int]) -> dict[int, Path]:
    """Per question, the archived live record with the widest member spread (the one research/11 looked at)."""
    best: dict[int, tuple[float, Path]] = {}
    for p in (DATA / "runs").rglob("q*.json"):
        if not p.stem[1:].isdigit() or int(p.stem[1:]) not in qids:
            continue
        r = json.loads(p.read_text(encoding="utf-8"))
        preds = [f["prediction"] for f in r["forecasters"] if isinstance(f.get("prediction"), (int, float))]
        spread = max(preds) - min(preds) if len(preds) > 1 else 0.0
        q = r["question"]["question_id"]
        if q not in best or spread > best[q][0]:
            best[q] = (spread, p)
    return {q: best[q][1] for q in qids if q in best}


def window_days(record: dict) -> float | None:
    res = record["question"].get("scheduled_resolution_time")
    if not res:
        return None
    a = datetime.fromisoformat(record["as_of"])
    b = datetime.fromisoformat(res.replace("Z", "+00:00"))
    return max((b - a).total_seconds() / 86400, 0.0)


class Budget:
    def __init__(self, cap: float) -> None:
        self.cap, self.spent, self.reserved, self.calls, self.refused = cap, 0.0, 0.0, 0, 0

    def reserve(self, usd: float) -> bool:
        if self.spent + self.reserved + usd > self.cap:
            self.refused += 1
            return False
        self.reserved += usd
        return True

    def settle(self, reserved: float, actual: float | None) -> None:
        self.reserved -= reserved
        self.spent += actual if actual is not None else reserved  # unknown cost counts as the worst case
        self.calls += 1


async def call(client: OpenRouterClient, budget: Budget, model: str, prompt: str, purpose: str):
    r = MAX_CALL_USD[model]
    if not budget.reserve(r):
        return None
    c = await client.complete(model=model, prompt=prompt, purpose=purpose, max_tokens=MAX_TOKENS, reasoning_effort=REASONING)
    budget.settle(r, c.cost_usd)
    return c


async def run_one(client, budget, sem, model: str, arm: str, qid: int, path: Path, run_dir: Path) -> dict:
    rec = json.loads(path.read_text(encoding="utf-8"))
    q, today, win = rec["question"], rec["as_of"][:10], window_days(rec)
    research = st.research_block(rec["research"]["items"])
    out = {"question_id": qid, "question_text": q["question_text"], "record": path.relative_to(DATA).as_posix(),
           "as_of": rec["as_of"], "window_days": win, "model": model, "arm": arm, "protocol": st.PROTOCOL_VERSION,
           "live": {"aggregate": rec["aggregate"], "members": {f["model"]: f["prediction"] for f in rec["forecasters"]}},
           "research_items": [{"source": i.get("source"), "title": i.get("title"), "url": i.get("url"),
                               "date": (i.get("published_at") or "")[:10]} for i in rec["research"]["items"]],
           "calls": [], "blind_base": None, "forecast": None, "error": None}
    try:
        check_eligible(model, rec["as_of"])
    except IneligiblePair as e:
        out["error"] = f"ineligible: {e}"
        return out
    async with sem:
        given = None
        if arm == "C":
            c1 = await call(client, budget, model, st.blind_base_rate_prompt(q, today), "exp005-blind-base")
            if c1 is None:
                out["error"] = "budget cap reached"
                return out
            out["calls"].append(c1.to_dict())
            try:
                given, probs = st.build_blind_base(st.parse_json_object(c1.output), max_window_days=win)
                out["blind_base"] = {"base": None if given is None else st.asdict(given), "problems": probs}
            except st.ProtocolError as e:
                out["error"] = f"blind base: {e}"
                return out
            if given is None:
                out["error"] = "blind base: no usable reference class"
                return out
            prompt = st.protocol_prompt_given_base(q, research, today, given)
        else:
            prompt = st.protocol_prompt(q, research, today)
        c2 = await call(client, budget, model, prompt, f"exp005-arm{arm}")
        if c2 is None:
            out["error"] = "budget cap reached"
            return out
        out["calls"].append(c2.to_dict())
        if c2.error:
            out["error"] = f"call: {c2.error}"
            return out
        try:
            out["forecast"] = st.build(st.parse_json_object(c2.output), max_window_days=win, given_base=given).to_dict()
        except st.ProtocolError as e:
            out["error"] = f"protocol: {e}"
    dest = run_dir / model.replace("/", "_") / f"arm{arm}" / f"q{qid}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    f = out["forecast"]
    print(f"  {model:22s} arm {arm} q{qid}: " + (f"p0 {f['p0']:.3f}  scen {f['p_scen'] if f['p_scen'] is None else round(f['p_scen'], 3)}"
          f"  drv {f['p_drv'] if f['p_drv'] is None else round(f['p_drv'], 3)}  final {f['final']:.3f}" if f else out["error"])
          + f"  (${budget.spent:.3f} spent)")
    return out


def append_ledger(run: str, budget: Budget, note: str) -> None:
    if not LEDGER.exists():
        LEDGER.write_text(
            "# Experiment spend ledger\n\n"
            "Frontier-model experiment budget: **$180**, approved by Christian 2026-10-07 (ADR-0009 amendment), on top of the $100 PoC.\n"
            "Every run that spends money adds a row (tools append automatically). Costs are OpenRouter-reported.\n\n"
            "| Date | Experiment | Run | What | Calls | Cost | Cumulative | Left of $180 |\n|---|---|---|---|---|---|---|---|\n",
            encoding="utf-8")
    rows = [l for l in LEDGER.read_text(encoding="utf-8").splitlines() if l.startswith("| 20")]
    cum = sum(float(l.split("|")[6].strip().lstrip("$")) for l in rows) + budget.spent
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(f"| {run[:4]}-{run[4:6]}-{run[6:8]} | EXP-005 | {run} | {note} | {budget.calls} | ${budget.spent:.3f} | ${cum:.3f} | ${180 - cum:.2f} |\n")


async def main_async(args) -> int:
    records = pick_records(args.questions)
    missing = [q for q in args.questions if q not in records]
    if missing:
        print(f"no archived record for {missing}")
    jobs = [(m, a, q, p) for q, p in records.items() for m in args.models for a in args.arms]
    if args.estimate:
        worst = sum(MAX_CALL_USD[m] * (2 if a == "C" else 1) for m, a, _, _ in jobs)
        for q, p in records.items():
            rec = json.loads(p.read_text(encoding="utf-8"))
            n = len(st.protocol_prompt(rec["question"], st.research_block(rec["research"]["items"]), rec["as_of"][:10]))
            ok = ", ".join(m for m in args.models if check_ok(m, rec["as_of"]))
            print(f"q{q}: prompt ~{n // 4} tokens, window {window_days(rec) or 0:.1f} d, eligible: {ok or 'none'}  ({p.relative_to(DATA).as_posix()})")
        print(f"{len(jobs)} jobs, worst case ${worst:.2f} (cap ${args.cap:.2f})")
        return 0
    run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-pilot"
    run_dir = OUT / run
    budget, sem = Budget(args.cap), asyncio.Semaphore(args.parallel)
    client = OpenRouterClient(SystemClock())
    results = await asyncio.gather(*(run_one(client, budget, sem, m, a, q, p, run_dir) for m, a, q, p in jobs))
    ok = sum(1 for r in results if r["forecast"])
    print(f"done: {ok}/{len(results)} forecasts, {budget.calls} calls, ${budget.spent:.4f} spent, {budget.refused} calls refused by the cap")
    append_ledger(run, budget, f"protocol pilot, arms {'/'.join(args.arms)}, {', '.join(args.models)}, {len(records)} questions (unscored)")
    print(f"outputs: {run_dir.relative_to(ROOT).as_posix()}")
    return 0


def check_ok(model: str, as_of: str) -> bool:
    try:
        check_eligible(model, as_of)
        return True
    except IneligiblePair:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--questions", nargs="*", type=int, default=PILOT_QIDS)
    ap.add_argument("--models", nargs="*", default=MODELS)
    ap.add_argument("--arms", nargs="*", default=["B", "C"], choices=["B", "C"])
    ap.add_argument("--cap", type=float, default=5.0, help="hard spend cap in $ for this run")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--estimate", action="store_true")
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
