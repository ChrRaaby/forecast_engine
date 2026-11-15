"""B-96: pick GPT-5 mini's replacement as the polarity checker (OpenAI shuts gpt-5-mini down on 2026-12-11).

    poetry run python tools/polarity_swap_check.py --dry-run                         # count prompts, estimate cost, no calls
    poetry run python tools/polarity_swap_check.py --models openai/gpt-6.1-terra,openai/gpt-6-luna --max-cost 3

Replays every stored polarity-check prompt (all live and shadow records under data/runs/) through each candidate, with the same
settings as the live check (max_tokens 4000, reasoning effort low, the prompt text exactly as stored), and compares the verdict
with GPT-5 mini's stored one. Two "known flips" are added: members that answered for NO before the check existed (research/09:
posts 45898 and 45925, Gemini Flash-Lite); their prompts are rebuilt with polarity.check_prompt, so the candidate must catch them.

Pass rule (written 2026-11-14, before any run): a candidate passes if
  (1) it flags (NO or OTHER) every case GPT-5 mini flagged and both known flips,
  (2) it flags at most 1% of the cases GPT-5 mini judged YES, and
  (3) at most 1% of its calls fail or return no verdict.
Every disagreement is printed with its reason, to be read by hand. Among passing candidates, OpenAI's named successor
(gpt-6.1-terra) is preferred; if none passes, the disagreements go to Christian.

Writes data/experiments/B-96/polarity-<timestamp>/results.jsonl and prints a Markdown summary. Cost is checked against
--max-cost before each call (actual spend, from OpenRouter's usage field).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forecast_engine import polarity  # noqa: E402
from forecast_engine.schema import QuestionSnapshot  # noqa: E402

KNOWN_FLIPS = ((45898, "google/gemini-3.5-flash-lite"), (45925, "google/gemini-3.5-flash-lite"))  # research/09
FLAGGED = ("NO", "OTHER")
# USD per 1M tokens (OpenRouter, read 2026-11-14), only for the dry-run estimate; real spend comes from the API.
PRICES = {"openai/gpt-5-mini": (0.25, 2.0), "openai/gpt-6.1-terra": (1.0, 5.0), "openai/gpt-6-luna": (0.10, 0.50),
          "anthropic/claude-haiku-5.5": (0.10, 0.50)}


def collect(runs: Path) -> list[dict]:
    """One case per distinct stored check prompt, plus the known flips. Each: id, prompt, stored verdict, expected."""
    cases: dict[str, dict] = {}
    flips = dict(KNOWN_FLIPS)
    for f in sorted(runs.glob("*/data/forecasts/*/q*.json")):
        rec = json.loads(f.read_text(encoding="utf-8"))
        q = rec.get("question") or {}
        for m in rec.get("forecasters") or []:
            ch = m.get("check")
            if ch and ch.get("prompt") and ch["prompt"] not in cases:
                stored = polarity.parse_check(ch.get("output") or "")
                cases[ch["prompt"]] = {"id": f"{q.get('post_id')}:{m.get('model')}", "prompt": ch["prompt"], "stored": stored,
                                       "expect_flag": stored in FLAGGED, "known_flip": False}
            elif not ch and flips.get(q.get("post_id")) == m.get("model") and (m.get("call") or {}).get("output"):
                snap = QuestionSnapshot(**{k: q.get(k) for k in QuestionSnapshot.__dataclass_fields__ if k in q})
                prompt = polarity.check_prompt(snap, m["call"]["output"])
                cases.setdefault(prompt, {"id": f"{q.get('post_id')}:{m.get('model')}", "prompt": prompt, "stored": None,
                                          "expect_flag": True, "known_flip": True})
    return list(cases.values())


def estimate(cases: list[dict], model: str) -> float:
    usd_in, usd_out = PRICES.get(model, (1.0, 5.0))
    tin = sum(len(c["prompt"]) for c in cases) / 4  # ~4 characters per token
    return (tin * usd_in + len(cases) * 150 * usd_out) / 1e6  # ~150 output tokens per verdict (GPT-5 mini averaged 122)


def judge(cases: list[dict], results: dict[str, list[dict]]) -> dict[str, dict]:
    """Per model: counts and the pass rule."""
    out = {}
    for model, rows in results.items():
        must = [r for r in rows if r["expect_flag"]]
        yes = [r for r in rows if not r["expect_flag"] and r["stored"] == "YES"]
        failed = [r for r in rows if r["verdict"] is None]
        missed = [r for r in must if r["verdict"] not in FLAGGED]
        false_flags = [r for r in yes if r["verdict"] in FLAGGED]
        s = {"calls": len(rows), "failed": len(failed), "must_flag": len(must), "missed": len(missed),
             "yes_cases": len(yes), "false_flags": len(false_flags),
             "agree": sum(1 for r in rows if r["stored"] is not None and r["verdict"] == r["stored"]),
             "comparable": sum(1 for r in rows if r["stored"] is not None)}
        s["passes"] = (not missed and len(false_flags) <= 0.01 * max(len(yes), 1) and len(failed) <= 0.01 * max(len(rows), 1))
        out[model] = s
    return out


def report(cases: list[dict], results: dict[str, list[dict]], spent: dict[str, float]) -> str:
    verdicts = judge(cases, results)
    L = ["# Polarity checker replacement (B-96)", "",
         f"{len(cases)} cases: {sum(c['expect_flag'] and not c['known_flip'] for c in cases)} flagged by GPT-5 mini, "
         f"{sum(c['known_flip'] for c in cases)} known flips, the rest judged YES.", "",
         "| Model | Calls | Agree with GPT-5 mini | Missed flags | False flags | Failed | Spend | Passes |", "|---|---|---|---|---|---|---|---|"]
    for m, s in verdicts.items():
        L.append(f"| {m} | {s['calls']} | {s['agree']}/{s['comparable']} | {s['missed']}/{s['must_flag']} | "
                 f"{s['false_flags']}/{s['yes_cases']} | {s['failed']} | ${spent.get(m, 0):.2f} | {'yes' if s['passes'] else 'NO'} |")
    L += ["", "## Disagreements (read each by hand)", ""]
    for m, rows in results.items():
        for r in rows:
            if r["verdict"] != (r["stored"] if not r["known_flip"] else "NO"):
                L.append(f"- {m} on {r['id']}: stored {r['stored'] or ('known flip' if r['known_flip'] else 'none')}, "
                         f"new {r['verdict']}: {r['why']}")
    return "\n".join(L)


async def run(cases: list[dict], models: list[str], max_cost: float, out: Path, concurrency: int) -> tuple[dict, dict]:
    from forecast_engine.clock import SystemClock
    from forecast_engine.llm import OpenRouterClient

    llm = OpenRouterClient(SystemClock())
    sem = asyncio.Semaphore(concurrency)
    spent = {m: 0.0 for m in models}
    results: dict[str, list[dict]] = {m: [] for m in models}
    out.parent.mkdir(parents=True, exist_ok=True)
    fh = out.open("a", encoding="utf-8")

    async def one(model: str, c: dict) -> None:
        async with sem:
            if sum(spent.values()) >= max_cost:
                return
            call = await llm.complete(model=model, prompt=c["prompt"], purpose="polarity_check", max_tokens=4000,
                                      reasoning_effort="low")
            spent[model] += call.cost_usd or 0.0
            verdict = None if call.error else polarity.parse_check(call.output)
            why = call.error or ""
            if not call.error:
                try:
                    why = json.loads(call.output[call.output.index("{"):call.output.rindex("}") + 1]).get("why", "")
                except (ValueError, AttributeError):
                    why = (call.output or "")[-200:]
            row = {**{k: c[k] for k in ("id", "stored", "expect_flag", "known_flip")}, "model": model, "verdict": verdict,
                   "why": why, "cost_usd": call.cost_usd, "served_model": call.extra.get("served_model")}
            results[model].append(row)
            fh.write(json.dumps(row) + "\n")

    await asyncio.gather(*[one(m, c) for m in models for c in cases])
    fh.close()
    return results, spent


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", type=Path, default=ROOT / "data")
    ap.add_argument("--models", default="openai/gpt-6.1-terra,openai/gpt-6-luna")
    ap.add_argument("--max-cost", type=float, default=3.0, help="stop starting calls once this much USD is spent")
    ap.add_argument("--concurrency", type=int, default=8)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    sys.stdout.reconfigure(encoding="utf-8")
    cases = collect(args.data / "runs")
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    print(f"{len(cases)} cases ({sum(c['expect_flag'] for c in cases)} must be flagged, "
          f"{sum(c['known_flip'] for c in cases)} known flips found)")
    for m in models:
        print(f"Plan: {m} x {len(cases)} calls, estimate ${estimate(cases, m):.2f}")
    if args.dry_run:
        return 0
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = args.data / "experiments" / "B-96" / f"polarity-{stamp}" / "results.jsonl"
    results, spent = asyncio.run(run(cases, models, args.max_cost, out, args.concurrency))
    text = report(cases, results, spent)
    (out.parent / "report.md").write_text(text + "\n", encoding="utf-8")
    print(text)
    print(f"\nRun folder: {out.parent}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
