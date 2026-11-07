"""EXP-002 replay (B-09b): the live prompt (arm A) vs the dates + "resolves YES" prompt (arm B) on resolved live binary records.

EXP-002 is a FIX with a no-harm check (Christian, 2026-11-07), not a test that must show a gain: arm B may go live if it fixes the
polarity bug and is not clearly worse (`no_harm`, card v2). It then runs as Tier 1, with the old prompt as the unpublished shadow.

    poetry run python tools/run_exp002_replay.py --dry-run                     # pick records, estimate the cost; no model calls
    poetry run python tools/run_exp002_replay.py --max-cost 20                 # the screen (frontier roster, every eligible record)

Both arms are replayed now, on each record's frozen research and as_of (`evals.replay.replay_record`), with the same config except
`prompt_version`: arm A the live prompt, arm B the variant "dates-yes-2026-11-07" (forecast_engine/prompt_variants). The new prompt
adds one line and no calls, so arm A is also the same-compute control. The old live answers are never used as the baseline: they
were made at another time, under other provider conditions. Records are picked as in tools/run_aa_replay.py (one per question,
the earliest non-shadow record, oldest first, release-date check T1, both replay guards); `--resolved-after` keeps only questions
resolved after a date (for a later check on fresh questions). Both arms of a question run at the same time (T11).

The replay calls only the forecasting models: no research, no follow-up search (that runs only in main.py), so nothing can see
today's web. The polarity check is off in both arms (its model has no verified release date, T1), so it can't change any score.
The secondary metric is a polarity AUDIT afterwards: the same auditor reads every member's answer in both arms and counts answers
whose number refers to NO or another event (expected: arm B ~0). The audit changes no forecast; `--no-audit` skips it (and then
the no-harm check can't pass, since the fix itself isn't checked).

Cost: estimated before anything runs from the archive's mean cost per call (as run_aa_replay.py), × SAFETY; refused above
--max-cost (default $20); a hard cap stops further calls once spent + one call's reserve would cross it. Outputs go to
data/experiments/EXP-002/<run>/ (private data repo: runs.csv, polarity.csv, report.md, replayed records). Spend is appended to
docs/experiments/budget-ledger.md. Nothing printed here contains research or article text.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import importlib.util
import json
import sys
from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals import report  # noqa: E402
from evals.outcomes import load_outcomes, resolved_date  # noqa: E402
from evals.replay import replay_record  # noqa: E402
from forecast_engine import polarity, prompt_versions  # noqa: E402
from forecast_engine.config import DEFAULT_CONFIG, TOURNAMENT_CONFIG, BotConfig  # noqa: E402
from forecast_engine.core import ForecastFailed  # noqa: E402
from forecast_engine.prompt_variants import dates_yes_2026_11_07 as variant  # noqa: E402
from forecast_engine.schema import ForecastRecord  # noqa: E402


def _aa():
    """tools/run_aa_replay.py: record picking, cost estimate, the spend cap, the table and the ledger are shared with it."""
    spec = importlib.util.spec_from_file_location("run_aa_replay", ROOT / "tools" / "run_aa_replay.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


aa = _aa()

DATA = ROOT / "data"
OUT = DATA / "experiments" / "EXP-002"
VARIANT = variant.VERSION
CONFIGS = {"frontier": TOURNAMENT_CONFIG, "cheap": DEFAULT_CONFIG}
ARMS = ("A", "B")
AUDIT_MODEL = DEFAULT_CONFIG.polarity_check_model or "openai/gpt-5-mini"
POLARITY_COLUMNS = ("question_id", "arm", "member", "verdict")


OLD_PROMPT = "template-2026-09-26-old"  # the pre-EXP-002 live prompt (same text as "template-2026-09-26"), a variant since go-live


def arm_configs(name: str) -> dict[str, BotConfig]:
    """Arm A: the roster with the pre-EXP-002 prompt, polarity check off. Arm B: the same with the EXP-002 prompt. Nothing else
    differs. (Before the go-live on 2026-11-07 arm A named the then-live "template-2026-09-26"; same text.)"""
    base = replace(CONFIGS[name], polarity_check_model=None)
    assert OLD_PROMPT in prompt_versions.known_versions()
    return {"A": replace(base, prompt_version=OLD_PROMPT), "B": replace(base, prompt_version=VARIANT)}


def pick(runs_dir: Path, outcomes: dict[int, dict[str, Any]], cfg: BotConfig, n: int, resolved_after: date | None):
    if resolved_after is not None:
        outcomes = {q: row for q, row in outcomes.items() if _resolved_after(row, resolved_after)}
    picked, skips = aa.pick_records(runs_dir, outcomes, cfg, n)
    return picked, skips


def _resolved_after(row: dict[str, Any], day: date) -> bool:
    d = resolved_date(row)
    return d is not None and date.fromisoformat(str(d)[:10]) > day


def audit_costs(runs_dir: Path) -> float:
    """Mean reported cost of one polarity-check call in the archive (FALLBACK if none)."""
    seen = []
    for _, rec in aa.live_records(runs_dir):
        for f in rec.get("forecasters", []):
            c = f.get("check") or {}
            if c.get("cost_usd") is not None and not c.get("error"):
                seen.append(float(c["cost_usd"]))
    return sum(seen) / len(seen) if seen else aa.FALLBACK_CALL_USD


async def replay_arms(picked, cfgs: dict[str, BotConfig], llm, parallel: int, run_dir: Path, runs_dir: Path):
    sem = asyncio.Semaphore(parallel)
    replayed: list[tuple[str, ForecastRecord]] = []

    async def one(path: Path, rec: dict[str, Any], outcome: int, resolved_at: str, arm: str) -> list[dict[str, Any]]:
        async with sem:
            try:
                out, failed = await replay_record(rec, cfgs[arm], llm), False
            except ForecastFailed as e:
                out, failed = e.record, True
        dest = run_dir / "records" / arm / f"q{out.question.question_id}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        src = path.relative_to(runs_dir).as_posix() if path.is_relative_to(runs_dir) else path.name
        dest.write_text(json.dumps({**out.to_dict(), "replayed_from": src, "prompt_version": cfgs[arm].prompt_version},
                                   indent=1, ensure_ascii=False), encoding="utf-8")
        replayed.append((arm, out))
        return aa.table_rows(out, arm, outcome, resolved_at, failed)

    jobs = [one(p, r, o, d, arm) for p, r, o, d in picked for arm in ARMS]
    rows = [row for rows in await asyncio.gather(*jobs) for row in rows]
    return rows, replayed


async def audit(replayed: list[tuple[str, ForecastRecord]], llm, parallel: int) -> list[dict[str, Any]]:
    """Secondary metric: what each member's final number refers to (YES / NO / OTHER / UNCLEAR, or FAILED if the audit failed)."""
    sem = asyncio.Semaphore(parallel)

    async def one(arm: str, rec: ForecastRecord, f) -> dict[str, Any]:
        async with sem:
            verdict, _ = await polarity.check_member(rec.question, f.call.output, llm, AUDIT_MODEL)
        return {"question_id": rec.question.question_id, "arm": arm, "member": f.model, "verdict": verdict or "FAILED"}

    jobs = [one(arm, rec, f) for arm, rec in replayed for f in rec.forecasters if f.prediction is not None]
    return sorted(await asyncio.gather(*jobs), key=lambda r: (r["question_id"], r["arm"], r["member"]))


def polarity_lines(rows: list[dict[str, Any]]) -> list[str]:
    out = []
    for arm in ARMS:
        c = Counter(r["verdict"] for r in rows if r["arm"] == arm)
        n = sum(c.values())
        wrong = c["NO"] + c["OTHER"]
        out.append(f"arm {arm}: {wrong}/{n} member answers refer to NO or another event "
                   f"(NO {c['NO']}, OTHER {c['OTHER']}, UNCLEAR {c['UNCLEAR']}, audit failed {c['FAILED']})")
    return out


# No-harm check (card v2, 2026-11-07): all must hold for arm B to go live (Tier 1, old prompt in shadow). Fixed before any run.
MAX_RELIABILITY_INCREASE = 0.005  # protocol default: calibration may not get worse by more than this
MAX_FAILURE_RATE_INCREASE = 0.02  # protocol default: at most 2 more points of member forecasts without a usable number


def no_harm(an: report.Analysis, pol: list[dict[str, Any]] | None) -> tuple[bool, list[str]]:
    """(go live?, one line per condition). "Clearly worse" = the whole 95% CI of B − A log score is below 0."""
    c = an.comparisons["B"]["log"]
    lo, hi = c.improvement_ci
    rel_b, rel_a = an.calibration_paired.get("B", (float("nan"), float("nan")))
    fail_b, fail_a = an.summaries["B"].failure_rate, an.summaries["A"].failure_rate
    checks = [
        (c.n > 0 and hi >= 0, f"score not clearly worse (95% CI of B − A log score not entirely below 0): "
                              f"Δ {report._f(c.improvement)}, CI [{report._f(lo)}, {report._f(hi)}], N {c.n}"),
        (rel_b - rel_a <= MAX_RELIABILITY_INCREASE, f"calibration not worse by more than {MAX_RELIABILITY_INCREASE}: "
                                                    f"reliability B {report._f(rel_b)} vs A {report._f(rel_a)}"),
        (fail_b - fail_a <= MAX_FAILURE_RATE_INCREASE + 1e-12,
         f"failure rate not up by more than {MAX_FAILURE_RATE_INCREASE:.0%}: B {fail_b:.1%} vs A {fail_a:.1%}"),
    ]
    if pol is None:
        checks.append((False, "polarity audit not run (--no-audit): the fix itself is unchecked"))
    else:
        wrong = {arm: sum(1 for r in pol if r["arm"] == arm and r["verdict"] in ("NO", "OTHER")) for arm in ARMS}
        checks.append((wrong["B"] <= wrong["A"], f"wrong-sided answers not more common in B: B {wrong['B']} vs A {wrong['A']}"))
    ok = all(m for m, _ in checks)
    lines = [f"[{'x' if m else ' '}] {t}" for m, t in checks]
    lines.append("VERDICT: " + ("GO LIVE as Tier 1 (old prompt as unpublished shadow; switch back if live results turn worse)"
                                if ok else "DO NOT SHIP: a no-harm condition failed (see above)"))
    return ok, lines


def run(args, llm=None, *, runs_dir: Path = aa.RUNS, outcomes_path: Path = aa.OUTCOMES, out_root: Path = OUT,
        ledger: Path = aa.LEDGER) -> int:
    cfgs = arm_configs(args.config)
    after = date.fromisoformat(args.resolved_after) if args.resolved_after else None
    picked, skips = pick(runs_dir, load_outcomes(outcomes_path), cfgs["A"], args.n, after)
    history = aa.call_costs(runs_dir)
    per_call = {f.model: max(history.get(f.model, aa.FALLBACK_CALL_USD), aa.MIN_CALL_USD) for f in cfgs["A"].forecasters}
    audit_call = 0.0 if args.no_audit else max(audit_costs(runs_dir), aa.MIN_CALL_USD)
    n_members = len(cfgs["A"].forecasters)
    est = len(ARMS) * len(picked) * (sum(per_call.values()) + n_members * audit_call) * aa.SAFETY
    dated = sum(1 for _, rec, *_ in picked if (rec.get("question") or {}).get("scheduled_resolution_time"))
    print(f"EXP-002 {args.run_kind}: config {args.config}, {n_members} forecasters, polarity check off in both arms; "
          f"arm A prompt {cfgs['A'].prompt_version}, arm B prompt {cfgs['B'].prompt_version}")
    print(f"{len(picked)} resolved binary records picked" + (f" (asked for {args.n})" if len(picked) < args.n else "")
          + (f", resolved after {after}" if after else ""))
    print(f"  {dated}/{len(picked)} have a scheduled resolution date (the others get no date line in arm B)")
    for reason, k in sorted(skips.items()):
        print(f"  skipped {k}: {reason}")
    for m, c in per_call.items():
        print(f"  {m}: ${c:.4f}/call" + (" (no cost history: fallback)" if m not in history else " (mean of archive)"))
    if not args.no_audit:
        print(f"  polarity audit ({AUDIT_MODEL}): ${audit_call:.4f}/call, one per member answer")
    print(f"estimated cost: ${est:.2f} = {len(ARMS)} arms × {len(picked)} records × "
          f"${sum(per_call.values()) + n_members * audit_call:.4f} × {aa.SAFETY} safety (cap ${args.max_cost:.2f})")
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
    capped = aa.CappedLlm(llm, args.max_cost, {**per_call, AUDIT_MODEL: audit_call or aa.FALLBACK_CALL_USD})
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = out_root / f"{run_id}-{args.run_kind}"
    run_dir.mkdir(parents=True, exist_ok=True)
    rows, replayed = asyncio.run(replay_arms(picked, cfgs, capped, args.parallel, run_dir, runs_dir))
    table = run_dir / "runs.csv"
    aa.write_table(rows, table)
    pol_lines: list[str] = []
    pol: list[dict[str, Any]] | None = None
    if not args.no_audit:
        pol = asyncio.run(audit(replayed, capped, args.parallel))
        with open(run_dir / "polarity.csv", "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(POLARITY_COLUMNS))
            w.writeheader()
            w.writerows(pol)
        pol_lines = polarity_lines(pol)
    print(f"done: {capped.calls} calls, ${capped.spent:.4f} spent, {capped.refused} calls refused by the cap")
    an = report.analyse(report.read_table(table), "A", name=f"EXP-002 {args.run_kind}", decision_arms=["B"],
                        run_kind=args.run_kind, tier=1, meta=report.provenance(table))
    _, verdict = no_harm(an, pol)
    md = report.render_report(an)
    if pol_lines:
        md += "\n## Polarity audit (what the fix should fix)\n\n" + "".join(f"- {ln}\n" for ln in pol_lines)
    md += "\n## No-harm check (EXP-002 card v2, fixed before the run)\n\n" + "".join(f"- {ln}\n" for ln in verdict)
    (run_dir / "report.md").write_text(md, encoding="utf-8", newline="\n")
    c = an.comparisons["B"][an.metric]
    print(f"B vs A: N {c.n}, log Δ {report._f(c.mean_diff)}, 95% CI [{report._f(c.ci_low)}, {report._f(c.ci_high)}]: "
          f"{c.verdict_words()}")
    for ln in pol_lines + verdict:
        print(ln)
    print(f"report: {run_dir / 'report.md'}")
    if capped.calls:
        aa.append_ledger(run_id, capped.calls, capped.spent,
                         f"EXP-002 {args.run_kind}, {len(picked)} binary records × 2 arms, {args.config} config"
                         + ("" if args.no_audit else " + polarity audit"), ledger, experiment="EXP-002")
    return 0


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=1000, help="at most this many resolved binary records (default: all eligible)")
    ap.add_argument("--config", choices=sorted(CONFIGS), default="frontier",
                    help="roster replayed in both arms (default frontier: the live published roster)")
    ap.add_argument("--resolved-after", help="only questions resolved after this date, YYYY-MM-DD (the §6.3 rerun)")
    ap.add_argument("--run-kind", choices=report.RUN_KINDS, default="screen", help="labels the run (default screen)")
    ap.add_argument("--max-cost", type=float, default=20.0, help="refuse above this estimate; hard cap on spend in $ (default 20)")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--no-audit", action="store_true", help="skip the polarity audit (secondary metric)")
    ap.add_argument("--dry-run", action="store_true", help="pick records and estimate the cost; no model calls")
    return ap


def main(argv: list[str] | None = None) -> int:
    return run(parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
