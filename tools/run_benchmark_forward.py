"""EXP-005 benchmark-forward: forecast open benchmark questions now (arms A/B/C), to be scored when they resolve.

    poetry run python tools/run_benchmark_forward.py --list        # the selected questions (dates and ids only)
    poetry run python tools/run_benchmark_forward.py --estimate    # worst-case cost, no calls
    poetry run python tools/run_benchmark_forward.py               # research snapshots, then arms A/B/C; $5 hard cap

Plan: the "benchmark-forward" section of docs/experiments/EXP-005-graph-first-reasoning.md. One frozen research snapshot per
question with the live pipeline (AskNews latest + Gemini grounded), stored encrypted with ARCHIVE_KEY like live records; every arm
and member uses that bundle. as_of = run time. Never reads resolution fields or the community prediction: the snapshot built for
the prompts carries only title, background, resolution criteria, fine print and dates.
Outputs: data/experiments/EXP-005/benchmark-forward/<run>/ (private data repo).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import run_structured_pilot as pilot  # noqa: E402  (Budget, call, ledger)
from build_monitor import openssl_exe  # noqa: E402
from evals.model_release import IneligiblePair, check_eligible  # noqa: E402
from forecast_engine import parsing  # noqa: E402
from forecast_engine import structured as st  # noqa: E402
from forecast_engine.clock import FixedClock, SystemClock, today_str  # noqa: E402
from forecast_engine.config import DEFAULT_CONFIG  # noqa: E402
from forecast_engine.llm import OpenRouterClient  # noqa: E402
from forecast_engine.prompts import forecast_prompt  # noqa: E402
from forecast_engine.research import gather_research  # noqa: E402
from forecast_engine.schema import QuestionSnapshot  # noqa: E402

BENCH = ROOT / "data" / "benchmark"
OUT = ROOT / "data" / "experiments" / "EXP-005" / "benchmark-forward"
EXCLUDED_CLOSED = {22310, 39825, 43687, 43690}
RESOLVE_BY = "2026-12-31"
PER_GROUP = 3
# Committed in the EXP-005 card (benchmark-forward, 2026-10-08) before any run; selection must reproduce it exactly.
COMMITTED_IDS = [36515, 37460, 37484, 39090, 40180, 40184, 40185, 41205, 41219, 41418, 41930]
MODELS = ["openai/gpt-6.1-sol", "google/gemini-3.1-pro-preview"]
ARMS = ["A", "B", "C"]
pilot.MAX_CALL_USD["google/gemini-3.1-pro-preview"] = 0.21  # 16k out x $12/M + ~10k in; pilot.call reserves from this table
MAX_CALL_USD = pilot.MAX_CALL_USD
RESEARCH_RESERVE_USD = 0.02  # per question: Gemini grounded estimate (AskNews is credits, not $)
SNAPSHOT_FIELDS = ("question_id", "post_id", "title", "description", "resolution_criteria", "fine_print", "open_time",
                   "scheduled_resolve_time")  # never resolution, aggregations or anything after as_of


def select_questions() -> list[dict]:
    """Open binary questions resolving by RESOLVE_BY, closed ones excluded, at most PER_GROUP per post (lowest ids).
    Returns only SNAPSHOT_FIELDS; outcomes and community predictions are dropped here, before anything else sees them."""
    f = sorted(BENCH.glob("posts_*.jsonl"))[-1]
    by_post: dict[int, list[dict]] = defaultdict(list)
    for line in f.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        p = json.loads(line)
        for q in ([p["question"]] if p.get("question") else []) + list((p.get("group_of_questions") or {}).get("questions", [])):
            if (q["type"] == "binary" and q["status"] == "open" and q["id"] not in EXCLUDED_CLOSED
                    and (q.get("scheduled_resolve_time") or "9") <= RESOLVE_BY):
                by_post[p["id"]].append({"question_id": q["id"], "post_id": p["id"], "title": q.get("title") or p.get("title"),
                                         "description": q.get("description") or "", "resolution_criteria": q.get("resolution_criteria") or "",
                                         "fine_print": q.get("fine_print") or "", "open_time": q.get("open_time"),
                                         "scheduled_resolve_time": q.get("scheduled_resolve_time")})
    out = [q for qs in by_post.values() for q in sorted(qs, key=lambda q: q["question_id"])[:PER_GROUP]]
    return sorted(out, key=lambda q: q["question_id"])


def snapshot(q: dict) -> QuestionSnapshot:
    parse = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None  # noqa: E731
    return QuestionSnapshot(
        question_id=q["question_id"], post_id=q["post_id"], question_type="binary", question_text=q["title"],
        background_info=q["description"], resolution_criteria=q["resolution_criteria"], fine_print=q["fine_print"],
        page_url=f"https://www.metaculus.com/questions/{q['post_id']}", tournaments=("bot-benchmarking",),
        open_time=parse(q["open_time"]), scheduled_resolution_time=parse(q["scheduled_resolve_time"]))


def encrypt_json(obj: dict, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([openssl_exe(), "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-salt", "-pass", "env:ARCHIVE_KEY",
                    "-out", str(dest)], input=json.dumps(obj, ensure_ascii=False).encode("utf-8"), check=True,
                   env={**os.environ, "ARCHIVE_KEY": os.environ["ARCHIVE_KEY"]})


def decrypt_json(src: Path) -> dict:
    out = subprocess.run([openssl_exe(), "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-pass", "env:ARCHIVE_KEY",
                          "-in", str(src)], capture_output=True, check=True, env={**os.environ, "ARCHIVE_KEY": os.environ["ARCHIVE_KEY"]})
    return json.loads(out.stdout.decode("utf-8"))


async def take_snapshot(q: dict, run_dir: Path, budget: pilot.Budget) -> dict | None:
    dest = run_dir / "research" / f"q{q['question_id']}.json.enc"
    if dest.exists():
        return decrypt_json(dest)
    if not budget.reserve(RESEARCH_RESERVE_USD):
        return None
    bundle = await gather_research(snapshot(q), SystemClock(), DEFAULT_CONFIG.research)
    budget.settle(RESEARCH_RESERVE_USD, bundle.cost_usd)
    data = {"question": q, "bundle": bundle.to_dict(), "as_of": bundle.as_of.isoformat()}
    encrypt_json(data, dest)
    print(f"  research q{q['question_id']}: {len(bundle.items)} items from {bundle.providers} ${bundle.cost_usd:.4f}"
          + (f"  errors: {bundle.errors}" if bundle.errors else ""))
    return data


async def run_arm(client, budget, sem, model: str, arm: str, snap: dict, run_dir: Path) -> dict:
    q, as_of = snap["question"], snap["as_of"]
    items = snap["bundle"]["items"]
    today = as_of[:10]
    win = (datetime.fromisoformat(q["scheduled_resolve_time"].replace("Z", "+00:00"))
           - datetime.fromisoformat(as_of)).total_seconds() / 86400
    out = {"question_id": q["question_id"], "post_id": q["post_id"], "question_text": q["title"], "as_of": as_of,
           "window_days": win, "model": model, "arm": arm, "protocol": st.PROTOCOL_VERSION if arm != "A" else "live-binary",
           "calls": [], "prediction": None, "forecast": None, "blind_base": None, "error": None}
    try:
        check_eligible(model, as_of)
    except IneligiblePair as e:
        out["error"] = f"ineligible: {e}"
        return save(out, run_dir)
    pq = {"question_text": q["title"], "background_info": q["description"], "resolution_criteria": q["resolution_criteria"],
          "fine_print": q["fine_print"], "scheduled_resolution_time": q["scheduled_resolve_time"]}
    async with sem:
        if arm == "A":
            snapq = snapshot(q)
            text = "\n\n".join(f"[{i['source']}]" + (f" {i['title']}" if i.get("title") else "") + f"\n{(i.get('text') or '').strip()}"
                               for i in items) or "No research is available for this question."  # = ResearchBundle.as_prompt_text()
            prompt = forecast_prompt(snapq, text, today_str(FixedClock(datetime.fromisoformat(as_of))))
            c = await pilot.call(client, budget, model, prompt, "exp005-fwd-armA")
            if c is None:
                out["error"] = "budget cap reached"
                return save(out, run_dir)
            out["calls"].append(c.to_dict())
            if c.error:
                out["error"] = f"call: {c.error}"
            else:
                try:
                    out["prediction"] = parsing.parse_binary(c.output, DEFAULT_CONFIG.binary_clip)
                except parsing.ParseError as e:
                    out["error"] = f"parse: {e}"
            return save(out, run_dir)
        research = st.research_block(items)
        given = None
        if arm == "C":
            c1 = await pilot.call(client, budget, model, st.blind_base_rate_prompt(pq, today), "exp005-fwd-blind-base")
            if c1 is None:
                out["error"] = "budget cap reached"
                return save(out, run_dir)
            out["calls"].append(c1.to_dict())
            try:
                given, probs = st.build_blind_base(st.parse_json_object(c1.output), max_window_days=win)
                out["blind_base"] = {"base": None if given is None else st.asdict(given), "problems": probs}
            except st.ProtocolError as e:
                out["error"] = f"blind base: {e}"
                return save(out, run_dir)
            if given is None:
                out["error"] = "blind base: no usable reference class"
                return save(out, run_dir)
            prompt = st.protocol_prompt_given_base(pq, research, today, given)
        else:
            prompt = st.protocol_prompt(pq, research, today)
        c2 = await pilot.call(client, budget, model, prompt, f"exp005-fwd-arm{arm}")
        if c2 is None:
            out["error"] = "budget cap reached"
            return save(out, run_dir)
        out["calls"].append(c2.to_dict())
        if c2.error:
            out["error"] = f"call: {c2.error}"
            return save(out, run_dir)
        try:
            f = st.build(st.parse_json_object(c2.output), max_window_days=win, given_base=given)
            out["forecast"], out["prediction"] = f.to_dict(), f.final
        except st.ProtocolError as e:
            out["error"] = f"protocol: {e}"
    return save(out, run_dir)


def save(out: dict, run_dir: Path) -> dict:
    dest = run_dir / out["model"].replace("/", "_") / f"arm{out['arm']}" / f"q{out['question_id']}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    cost = sum(c.get("cost_usd") or 0 for c in out["calls"])
    print(f"  {out['model']:32s} arm {out['arm']} q{out['question_id']}: "
          + (f"{out['prediction']:.3f}" if out["prediction"] is not None else out["error"]) + f"  ${cost:.4f}")
    return out


async def main_async(args) -> int:
    qs = select_questions()
    ids = [q["question_id"] for q in qs]
    if ids != COMMITTED_IDS:
        sys.exit(f"selection {ids} differs from the committed list {COMMITTED_IDS}; stop (the card is the source of truth)")
    if args.list:
        for q in qs:
            print(q["question_id"], q["post_id"], (q["scheduled_resolve_time"] or "")[:10], q["title"][:90])
        print(f"{len(qs)} questions")
        return 0
    worst = len(qs) * (RESEARCH_RESERVE_USD + sum(MAX_CALL_USD[m] * 4 for m in MODELS))
    print(f"{len(qs)} questions x {len(MODELS)} models x arms {'/'.join(ARMS)} (4 calls per model), worst case ${worst:.2f}, cap ${args.cap:.2f}")
    if args.estimate:
        return 0
    run = args.resume or (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    run_dir = OUT / run
    budget, sem = pilot.Budget(args.cap), asyncio.Semaphore(args.parallel)
    print(f"run {run}: research snapshots")
    snaps = [await take_snapshot(q, run_dir, budget) for q in qs]  # sequential: AskNews is rate-limited
    client = OpenRouterClient(SystemClock())
    jobs = [(m, a, s) for s in snaps if s for m in MODELS for a in ARMS
            if not (run_dir / m.replace("/", "_") / f"arm{a}" / f"q{s['question']['question_id']}.json").exists()]
    print(f"forecasts: {len(jobs)} jobs")
    results = await asyncio.gather(*(run_arm(client, budget, sem, m, a, s, run_dir) for m, a, s in jobs))
    ok = sum(1 for r in results if r["prediction"] is not None)
    print(f"done: {ok}/{len(results)} forecasts, {budget.calls} paid calls, ${budget.spent:.4f} spent, {budget.refused} refused by the cap")
    pilot.append_ledger(run, budget, f"benchmark-forward, {len(qs)} questions, arms A/B/C, {', '.join(MODELS)}, k=1 (research incl.)")
    print(f"outputs: {run_dir.relative_to(ROOT).as_posix()}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--estimate", action="store_true")
    ap.add_argument("--cap", type=float, default=5.0)
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--resume", help="existing run id: reuse its snapshots, run only missing forecasts")
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
