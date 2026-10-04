"""Leakage-screen validation (B-45): how often does the screen miss a planted leak, and how often does it flag a clean bundle?

    poetry run python -m evals.screen_validation --n 30            # ~30 archive searches (150 credits) + screen calls (~$2)

For a seeded sample of eligible past questions (opened between the evaluation-model cutoff and Aug 2026; census cache):
1. fetch the as-of research bundle through the normal backtest path (budget guard, date guard, cache);
2. check the date bound held for that date (no article after as_of, all dated);
3. build three cases per bundle: clean; + an obvious planted leak (hindsight wording, explicit later date); + a subtle planted
   leak (a later-dated event described plainly, no hindsight words). Planted items are dated the day before as_of, so only the
   screen can catch them. They are written by a cheap generator model and are fictional;
4. run every candidate screen config on every case and report miss rate (plants not flagged at the planted index) and
   false-positive rate (clean bundles flagged).

Pass bar (agreed with Christian 2026-10-04): at most 1 miss in 30 plants per kind, and at most 10% false positives.
Per-case results, which quote article text, go to data/qa/ (private data repo); only the summary belongs in the public repo.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import random
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from forecast_engine.clock import FixedClock, SystemClock
from forecast_engine.llm import OpenRouterClient
from forecast_engine.schema import QuestionSnapshot, ResearchItem

from .asknews_archive import ArchiveConfig, SdkArchiveClient
from .asknews_budget import CREDITS_PER_ARCHIVE_SEARCH, AskNewsBudget
from .asof import assert_bundle_as_of
from .asof_research import gather_research_as_of
from .leakage_screen import EVAL_MODEL_CUTOFF, DualScreenConfig, ScreenConfig, run_screen
from .research_cache import ResearchCache

ROOT = Path(__file__).resolve().parents[1]
CENSUS = ROOT / "data" / "census" / "questions.jsonl"
OUT_DIR = ROOT / "data" / "qa"
GENERATOR = "openai/gpt-6-luna"  # writes the fictional planted sentences; never used as a screen

CANDIDATES = {
    "gemini-2.5-flash": ScreenConfig(),
    "gemini-2.5-flash+reasoning": ScreenConfig(reasoning_effort="medium"),
    "gpt-5-mini": ScreenConfig(model="openai/gpt-5-mini"),
    "gpt-5-mini-low-16k": ScreenConfig(model="openai/gpt-5-mini", reasoning_effort="low", max_tokens=16000),
    "dual (both must flag)": DualScreenConfig(),
    "claude-haiku-4.5": ScreenConfig(model="anthropic/claude-haiku-4.5"),
}

PLANT_PROMPT = """You are helping test a leak detector for a forecasting backtest. Write two short, fictional, news-style sentences
about the question below. The forecaster's "today" is {as_of}. Both sentences must reveal information from AFTER {as_of}.

Question: {question}

OBVIOUS: one sentence that uses hindsight wording ("went on to", "ultimately", "in the end", "it later emerged") and names a
specific date between {d1} and {d2}, stating how the matter turned out.
SUBTLE: one sentence with no hindsight words that plainly reports a specific event dated between {d1} and {d2} which bears on the
question (as a normal news report written on that date would).

Answer exactly in this format and nothing else:
OBVIOUS: <sentence>
SUBTLE: <sentence>
"""


def sample_questions(n: int, seed: int, exclude: set[int] = frozenset()) -> list[dict]:
    rows = [json.loads(line) for line in CENSUS.read_text(encoding="utf-8").splitlines()]
    eligible = [r for r in rows if r["open_time"] and EVAL_MODEL_CUTOFF.isoformat() < r["open_time"][:10] < "2026-08-25"
                and r["type"] in ("binary", "multiple_choice", "numeric", "discrete") and r["question_id"] not in exclude]
    eligible.sort(key=lambda r: r["question_id"])  # stable before sampling
    return random.Random(seed).sample(eligible, n)


async def make_plants(llm: OpenRouterClient, q: QuestionSnapshot, as_of: datetime) -> tuple[str, str] | None:
    prompt = PLANT_PROMPT.format(as_of=as_of.date(), question=q.question_text, d1=(as_of + timedelta(days=3)).date(),
                                 d2=(as_of + timedelta(days=40)).date())
    call = await llm.complete(model=GENERATOR, prompt=prompt, purpose="qa_plant", max_tokens=2000, reasoning_effort="low")
    obvious = subtle = None
    for line in (call.output or "").splitlines():
        if line.strip().upper().startswith("OBVIOUS:"):
            obvious = line.split(":", 1)[1].strip()
        elif line.strip().upper().startswith("SUBTLE:"):
            subtle = line.split(":", 1)[1].strip()
    return (obvious, subtle) if obvious and subtle else None


async def main_async(n: int, seed: int, reuse: Path | None = None, exclude: Path | None = None,
                     screens: list[str] | None = None) -> int:
    """`reuse`: an earlier results file whose planted sentences are reused (same seed = same questions, cached bundles: free).
    `exclude`: an earlier results file whose questions are left out, for a fresh confirmation (holdout) sample."""
    candidates = {k: v for k, v in CANDIDATES.items() if not screens or k in screens}
    old_plants: dict[tuple[int, str], str] = {}
    if reuse:
        for x in json.loads(reuse.read_text(encoding="utf-8"))["results"]:
            if x.get("plant"):
                old_plants[(x["question_id"], x["kind"])] = x["plant"]
    excluded = {d["question_id"] for d in json.loads(exclude.read_text(encoding="utf-8"))["date_checks"]} if exclude else set()
    load_dotenv(ROOT / ".env")
    budget = AskNewsBudget(SystemClock())
    print(budget.status().summary())
    cache, cfg, client = ResearchCache(), ArchiveConfig(), SdkArchiveClient.from_env()
    sample = sample_questions(n, seed, excluded)
    uncached = sum(
        cache.get(r["question_id"], datetime.fromisoformat(r["open_time"].replace("Z", "+00:00")), cfg.config_hash(), r["title"]) is None
        for r in sample
    )
    budget.check(uncached * CREDITS_PER_ARCHIVE_SEARCH)  # the whole batch must fit before the first call; cache hits are free
    llm = OpenRouterClient(SystemClock())
    sem = asyncio.Semaphore(8)

    cases: list[dict] = []
    date_checks: list[dict] = []
    for r in sample:
        as_of = datetime.fromisoformat(r["open_time"].replace("Z", "+00:00"))
        q = QuestionSnapshot(question_id=r["question_id"], post_id=r["post_id"], question_type=r["type"], question_text=r["title"])
        bundle = await gather_research_as_of(q, FixedClock(as_of), cfg, client=client, budget=budget, cache=cache)
        rej = (bundle.calls[0].extra.get("rejected") if bundle.calls else None) or {}
        try:
            assert_bundle_as_of(bundle, as_of)
            guard_ok = True
        except Exception:
            guard_ok = False
        date_checks.append({"question_id": q.question_id, "as_of": as_of.isoformat(), "n_items": len(bundle.items),
                            "after_as_of": rej.get("after_as_of", 0), "undated": rej.get("undated", 0) + rej.get("naive_date", 0),
                            "guard_ok": guard_ok, "error": bundle.calls[0].error if bundle.calls else "no call"})
        if not bundle.items:
            continue
        if (q.question_id, "obvious") in old_plants and (q.question_id, "subtle") in old_plants:
            plants = (old_plants[(q.question_id, "obvious")], old_plants[(q.question_id, "subtle")])
        else:
            plants = await make_plants(llm, q, as_of)
        if plants is None:
            continue
        cases.append({"q": q, "kind": "clean", "bundle": bundle, "planted_index": None})
        for kind, text in zip(("obvious", "subtle"), plants):
            b = copy.deepcopy(bundle)
            b.items.append(ResearchItem(source="planted", title="News report", text=text,
                                        published_at=as_of - timedelta(days=1)))
            cases.append({"q": q, "kind": kind, "bundle": b, "planted_index": len(b.items) - 1, "plant": text})

    async def screen(name: str, scfg, case: dict) -> dict:
        async with sem:
            v = await run_screen(case["q"], case["bundle"], llm, scfg)
        hit = case["planted_index"] is not None and case["planted_index"] in v.flagged_items
        return {"screen": name, "question_id": case["q"].question_id, "kind": case["kind"], "status": v.status,
                "flagged": v.flagged_items, "planted_index": case["planted_index"], "caught": hit, "reason": v.reason,
                "cost_usd": v.call.cost_usd if v.call else None, "plant": case.get("plant")}

    results = await asyncio.gather(*[screen(name, scfg, c) for name, scfg in candidates.items() for c in cases])

    summary = {}
    for name in candidates:
        rs = [x for x in results if x["screen"] == name]
        clean = [x for x in rs if x["kind"] == "clean"]
        out = {"false_positive_rate": sum(x["status"] != "clean" for x in clean) / max(1, len(clean)),
               "screen_failed": sum(x["status"] == "screen_failed" for x in rs),
               "cost_usd": round(sum(x["cost_usd"] or 0 for x in rs), 4), "n_clean": len(clean)}
        for kind in ("obvious", "subtle"):
            ks = [x for x in rs if x["kind"] == kind]
            out[f"misses_{kind}"] = sum(not x["caught"] for x in ks)
            out[f"n_{kind}"] = len(ks)
        out["passes"] = (out["misses_obvious"] <= 1 and out["misses_subtle"] <= 1 and out["false_positive_rate"] <= 0.10
                         and out["screen_failed"] == 0)
        summary[name] = out

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = SystemClock().now().strftime("%Y%m%dT%H%M%SZ")
    (OUT_DIR / f"screen_validation_{stamp}.json").write_text(json.dumps(
        {"seed": seed, "n": n, "prompt_version": ScreenConfig().prompt_version, "reuse": str(reuse) if reuse else None,
         "exclude": str(exclude) if exclude else None, "summary": summary, "date_checks": date_checks, "results": results}, indent=1, default=str),
        encoding="utf-8")
    print(f"\nDate bound: {sum(d['after_as_of'] == 0 and d['undated'] == 0 and d['guard_ok'] for d in date_checks)}/"
          f"{len(date_checks)} bundles clean; {sum(d['n_items'] == 0 for d in date_checks)} empty; "
          f"errors: {sum(bool(d['error']) for d in date_checks)}")
    print(f"Cases: {len(cases)} ({sum(c['kind'] == 'clean' for c in cases)} questions with plants)\n")
    print(f"{'screen':28s} {'FP rate':>8s} {'miss obv':>9s} {'miss sub':>9s} {'failed':>7s} {'cost':>8s}  pass")
    for name, s in summary.items():
        print(f"{name:28s} {s['false_positive_rate']:8.0%} {s['misses_obvious']:>4d}/{s['n_obvious']:<4d} "
              f"{s['misses_subtle']:>4d}/{s['n_subtle']:<4d} {s['screen_failed']:7d} ${s['cost_usd']:7.4f}  {'yes' if s['passes'] else 'no'}")
    print("\n" + budget.status().summary())
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--seed", type=int, default=20261004)
    ap.add_argument("--reuse", type=Path, help="reuse planted sentences from this earlier results file")
    ap.add_argument("--exclude", type=Path, help="leave out the questions of this earlier results file (fresh sample)")
    ap.add_argument("--screens", nargs="*", help=f"subset of {list(CANDIDATES)}")
    args = ap.parse_args()
    return asyncio.run(main_async(args.n, args.seed, args.reuse, args.exclude, args.screens))


if __name__ == "__main__":
    raise SystemExit(main())
