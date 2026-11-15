"""Number mode of the prompt check (EXP-015): the baseline prompt (arm A) vs a new prompt (arm B) on resolved NUMERIC and DISCRETE
live records, both arms replayed fresh at the same moment on frozen research. The sibling of tools/run_prompt_check.py, which
picks binary records only.

    # count the questions and estimate the cost; no model calls
    poetry run python tools/run_number_check.py --exp EXP-015 --variant range-calibrated-2026-11-14 \\
        --pin-hosts deepseek/deepseek-v4.1-flash=<host> --dry-run
    # the scored run (only after the card is frozen and Christian's "go")
    poetry run python tools/run_number_check.py --exp EXP-015 --variant range-calibrated-2026-11-14 \\
        --pin-hosts deepseek/deepseek-v4.1-flash=<host>

Records: one per question, the published live tournament one (status ok, run dir "<time>-tournament": no wide-mode run,
shadow or test run; the earliest if a question was published more than once), resolved (not annulled), with every model in the
config released before the record's as_of (T1), and passing the replay guards (research dated before as_of; stored inputs rebuild the stored prompt). The question's axis (bounds,
zero point, bucket count) comes from the record's `submitted.axis` (records since B-71), else from the scaling file that
`tools/exp010.py --fetch-scaling` writes (data/outcomes/scaling.jsonl), checked against the record's bounds. A record with
neither, or whose resolution is not a number on its axis, is dropped and counted.

Scoring: each arm's aggregate goes through the live submission step (`main.to_prediction` -> `get_cdf()`, via tools/exp010.py's
LiveCdf) and is scored with the Metaculus baseline score (evals/exp010.score_cdf -> evals/scoring.py). Paired B − A on the
questions where both arms produced a CDF, cluster bootstrap by Metaculus post (10,000 reps, seed 1015, 95% CI). Also reported:
numeric and discrete separately, the share of answers outside each arm's 10–90 range (PIT of the submitted CDF below 0.1 or above
0.9), the median ratio of 10–90 widths B/A (on the question's axis), parse failures and $ per arm.

Arms differ only by `prompt_version` (polarity check off, as in run_prompt_check). The replay calls only the forecasting models.
Cost: estimated before anything runs (archive mean per call × SAFETY), refused above --max-cost (default $5), hard cap on spend,
spend appended to the budget ledger. Outputs: data/experiments/<exp>/<run>/ (scores.csv, report.md, records/A, records/B).
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import importlib.util
import json
import math
import statistics
import sys
from collections import Counter
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals import exp010, hosts, report  # noqa: E402
from evals import outcomes as oc  # noqa: E402
from evals.asof import LeakageError  # noqa: E402
from evals.model_release import IneligiblePair, check_eligible  # noqa: E402
from evals.replay import (  # noqa: E402
    TamperedRecord, UnknownPromptVersion, assert_record_as_of, assert_record_intact, live_records, replay_record,
)
from forecast_engine import prompt_versions  # noqa: E402
from forecast_engine.config import DEFAULT_CONFIG, TOURNAMENT_CONFIG, BotConfig  # noqa: E402
from forecast_engine.core import ForecastFailed  # noqa: E402


def _load_tool(name: str):
    spec = importlib.util.spec_from_file_location(f"_{name}_for_number_check", ROOT / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


aa = _load_tool("run_aa_replay")  # cost estimate, spend cap and ledger, shared with the binary checks

DATA = ROOT / "data"
OUT = DATA / "experiments"
SCALING = exp010.SCALING
CONFIGS = {"frontier": TOURNAMENT_CONFIG, "cheap": DEFAULT_CONFIG}
TYPES = ("numeric", "discrete")
LIVE_RUN_SUFFIX = "-tournament"  # live tournament runs only: not "-wide", "-tournament-shadow-*" or "-test_questions"
REPS = 10_000
SEED = 1015
LEVEL = 0.95
GO_FLOOR = -2.0  # fast rule b (Christian 2026-11-14): go if mean Δ > 0 and the CI's lower end is above −2 baseline points
ARMS = ("A", "B")
SCORE_COLUMNS = ("question_id", "cluster_id", "question_type", "arm", "ok", "error", "baseline", "pit", "outside", "width",
                 "members_ok", "members", "parse_failures", "call_failures", "cost_usd", "replayed_from")

SKIP_UNRESOLVED = "not resolved yet (or no outcome row)"
SKIP_ANNULLED = "annulled or ambiguous"
SKIP_NO_AXIS = "no axis (no submitted.axis and no matching scaling row; run tools/exp010.py --fetch-scaling)"
SKIP_BAD_RESOLUTION = "resolution is not a number or a bound on the question's axis"
SKIP_OUTSIDE_CLOSED = "resolution outside a closed bound (inconsistent with the question)"
SKIP_T1 = "a model in the config was released too late for this record (T1)"
SKIP_PROMPT = "prompt version not in evals/prompt_archive.py (integrity can't be checked)"
SKIP_TAMPERED = "stored inputs don't rebuild the live prompt (integrity check)"
SKIP_LEAK = "research dated after as_of (replay guard)"
SKIP_RESOLVED_BEFORE = "resolved on or before --resolved-after"


def _live_cdf():
    """tools/exp010.py's LiveCdf: the live adapter's submission step (main.to_prediction), kept out of evals/ (ADR-0005)."""
    return _load_tool("exp010").LIVE


@dataclass
class Job:
    path: Path
    rec: dict[str, Any]
    axis: exp010.Axis
    location: float
    resolution: str
    resolved_at: str | None
    cluster: str
    qtype: str

    @property
    def qid(self) -> int:
        return int(self.rec["question"]["question_id"])


def axis_for(rec: dict[str, Any], scaling: dict[int, dict[str, Any]]) -> exp010.Axis | None:
    """The question's axis: the record's own submitted.axis (since B-71), else its stored scaling row if it matches the bounds."""
    snap = rec["question"]
    ax = (rec.get("submitted") or {}).get("axis")
    if ax and ax.get("cdf_size"):
        return exp010.Axis(
            question_type=snap["question_type"], lower_bound=float(ax["lower_bound"]), upper_bound=float(ax["upper_bound"]),
            open_lower_bound=bool(ax.get("open_lower_bound")), open_upper_bound=bool(ax.get("open_upper_bound")),
            zero_point=None if ax.get("zero_point") is None else float(ax["zero_point"]), cdf_size=int(ax["cdf_size"]),
            nominal_lower_bound=ax.get("nominal_lower_bound"), nominal_upper_bound=ax.get("nominal_upper_bound"),
            question_id=snap.get("question_id"), post_id=snap.get("post_id"))
    sc = scaling.get(int(snap["question_id"]))
    if sc is not None and exp010._bounds_match(snap, sc):
        return exp010.axis_from(snap, sc)
    return None


def is_published_live(path: Path, rec: dict[str, Any]) -> bool:
    """A published forecast of the live tournament run (run dir "<time>-tournament"). Wide-mode runs (stopped 2026-10-08, a different
    question mix), shadows and test-question runs are left out (EXP-015 card, 2026-11-15)."""
    return (path.parent.name.endswith(LIVE_RUN_SUFFIX) and rec.get("status") == "ok" and rec.get("published") is not False)


def pick_records(runs_dir: Path, outcomes: dict[int, dict[str, Any]], scaling: dict[int, dict[str, Any]], cfg: BotConfig,
                 n: int, resolved_after: date | None = None) -> tuple[list[Job], Counter]:
    """(jobs, skip counts by reason). One record per question: the earliest published live one."""
    by_q: dict[int, list[tuple[Path, dict[str, Any]]]] = {}
    for path, rec in live_records(runs_dir):
        q = rec.get("question") or {}
        if q.get("question_type") in TYPES and is_published_live(path, rec):
            by_q.setdefault(int(q["question_id"]), []).append((path, rec))
    skips: Counter = Counter()
    jobs: list[Job] = []
    for qid in sorted(by_q):
        path, rec = min(by_q[qid], key=lambda x: (x[1]["as_of"], str(x[0])))
        row = outcomes.get(qid)
        res = str((row or {}).get("resolution") or "").strip().lower()
        if row is None or row.get("status") not in oc.SETTLED or not res:
            skips[SKIP_UNRESOLVED] += 1
            continue
        if res in oc.NO_SCORE_RESOLUTIONS:
            skips[SKIP_ANNULLED] += 1
            continue
        resolved_at = oc.resolved_date(row)
        if resolved_after is not None and (resolved_at is None or date.fromisoformat(str(resolved_at)[:10]) <= resolved_after):
            skips[SKIP_RESOLVED_BEFORE] += 1
            continue
        axis = axis_for(rec, scaling)
        if axis is None:
            skips[SKIP_NO_AXIS] += 1
            continue
        loc = exp010.outcome_location(res, axis)
        if loc is None or math.isnan(loc):
            skips[SKIP_BAD_RESOLUTION] += 1
            continue
        if loc < 0 and not axis.open_lower_bound or loc > 1 and not axis.open_upper_bound:
            skips[SKIP_OUTSIDE_CLOSED] += 1
            continue
        try:
            for f in cfg.forecasters:
                check_eligible(f.model, rec["as_of"])
        except IneligiblePair:
            skips[SKIP_T1] += 1
            continue
        try:
            assert_record_as_of(rec)
            assert_record_intact(rec)
        except UnknownPromptVersion:
            skips[SKIP_PROMPT] += 1
            continue
        except TamperedRecord:
            skips[SKIP_TAMPERED] += 1
            continue
        except LeakageError:
            skips[SKIP_LEAK] += 1
            continue
        snap = rec["question"]
        jobs.append(Job(path, rec, axis, float(loc), res, resolved_at, f"post:{snap.get('post_id') or qid}",
                        snap["question_type"]))
    jobs.sort(key=lambda j: (j.rec["as_of"], j.qid))
    if len(jobs) > n:
        skips[f"beyond --n {n}"] += len(jobs) - n
    return jobs[:n], skips


def manifest(jobs: list[Job], runs_dir: Path) -> tuple[list[str], str]:
    """The picked record paths (relative to the runs dir, sorted) and their sha256 (first 16 hex): freezes the question set."""
    paths = sorted(j.path.relative_to(runs_dir).as_posix() if j.path.is_relative_to(runs_dir) else j.path.name for j in jobs)
    return paths, hashlib.sha256("\n".join(paths).encode("utf-8")).hexdigest()[:16]


def arm_configs(name: str, baseline_prompt: str, variant: str) -> dict[str, BotConfig]:
    base = replace(CONFIGS[name], polarity_check_model=None)
    return {"A": replace(base, prompt_version=baseline_prompt), "B": replace(base, prompt_version=variant)}


# --------------------------------------------------------------------------- scoring one replayed record
def width_10_90(points: list[tuple[float, float]], axis: exp010.Axis) -> float:
    """Distance between the aggregate's P10 and P90 on the question's axis (0..1 = the range; log axis for a log question)."""
    to_axis, _ = exp010._axis(axis)
    ps, xs = [p for p, _ in points], [to_axis(v) for _, v in points]
    return float(np.interp(0.9, ps, xs) - np.interp(0.1, ps, xs))


def score_record(rec: dict[str, Any], failed: bool, job: Job, builder) -> dict[str, Any]:
    """One row of scores.csv for an arm's replayed record. `ok` = the live builder made a CDF from its aggregate."""
    fs = rec.get("forecasters") or []
    call_fail = sum(1 for f in fs if (f.get("call") or {}).get("error"))
    parse_fail = sum(1 for f in fs if not (f.get("call") or {}).get("error") and f.get("parse_error"))
    cost = sum(float((f.get("call") or {}).get("cost_usd") or 0.0) for f in fs)
    row: dict[str, Any] = {
        "question_id": job.qid, "cluster_id": job.cluster, "question_type": job.qtype, "ok": 0, "error": "",
        "baseline": "", "pit": "", "outside": "", "width": "", "members": len(fs),
        "members_ok": sum(1 for f in fs if f.get("prediction") is not None and not f.get("excluded")),
        "parse_failures": parse_fail, "call_failures": call_fail, "cost_usd": round(cost, 6),
    }
    agg = rec.get("aggregate")
    if failed or not agg:
        row["error"] = "no aggregate (forecast failed)"
        return row
    points = [(float(p), float(v)) for p, v in agg]
    try:
        cdf = builder.build(job.axis, points)
    except Exception as e:  # the live bot would not have submitted
        row["error"] = f"CDF conversion failed: {type(e).__name__}"
        return row
    s = exp010.score_cdf(cdf, job.location, job.axis, metrics=("baseline", "pit"))
    try:
        width = width_10_90(points, job.axis)
    except (ValueError, ZeroDivisionError):
        width = float("nan")
    row.update(ok=1, baseline=s["baseline"], pit=s["pit"], outside=int(exp010.outside(s["pit"])), width=width)
    return row


# --------------------------------------------------------------------------- analysis
@dataclass
class Paired:
    label: str
    n: int
    clusters: int
    mean_a: float
    mean_b: float
    mean_diff: float
    ci_low: float
    ci_high: float


def paired(rows: list[dict[str, Any]], label: str, reps: int = REPS, seed: int = SEED, level: float = LEVEL) -> Paired:
    by: dict[int, dict[str, dict[str, Any]]] = {}
    for r in rows:
        by.setdefault(int(r["question_id"]), {})[r["arm"]] = r
    both = [(v["A"], v["B"]) for _, v in sorted(by.items()) if "A" in v and "B" in v and int(v["A"]["ok"]) and int(v["B"]["ok"])]
    if not both:
        nan = float("nan")
        return Paired(label, 0, 0, nan, nan, nan, nan, nan)
    a = [float(x["baseline"]) for x, _ in both]
    b = [float(y["baseline"]) for _, y in both]
    diffs = [y - x for x, y in zip(a, b)]
    clusters = [str(x["cluster_id"]) for x, _ in both]
    lo, hi, _ = report.cluster_bootstrap_ci(diffs, clusters, reps=reps, seed=seed, level=level)
    return Paired(label, len(both), len(set(clusters)), statistics.fmean(a), statistics.fmean(b), statistics.fmean(diffs), lo, hi)


def verdict(p: Paired) -> str:
    """Fast rule b (card EXP-015)."""
    if p.n == 0 or math.isnan(p.ci_low):
        return "no paired questions: no verdict"
    if p.mean_diff > 0 and p.ci_low > GO_FLOOR:
        return f"GO (mean Δ > 0 and the 95% CI's lower end {p.ci_low:+.2f} is above {GO_FLOOR:+.0f})"
    if p.ci_high < 0:
        return "KILL (the 95% CI is wholly below 0)"
    return "INCONCLUSIVE (keep today's prompt; one rerun on questions resolved after the run date)"


def arm_lines(rows: list[dict[str, Any]]) -> list[str]:
    by: dict[int, dict[str, dict[str, Any]]] = {}
    for r in rows:
        by.setdefault(int(r["question_id"]), {})[r["arm"]] = r
    both = {q: v for q, v in by.items() if int(v.get("A", {}).get("ok", 0)) and int(v.get("B", {}).get("ok", 0))}
    out = []
    for arm in ARMS:
        mine = [r for r in rows if r["arm"] == arm]
        ok = [r for r in mine if int(r["ok"])]
        on_both = [v[arm] for v in both.values()]
        outside = sum(int(r["outside"]) for r in on_both)
        out.append(
            f"| {arm} | {len(mine)} | {len(ok)} | {sum(1 for r in mine if not int(r['ok']))} | "
            f"{sum(int(r['parse_failures']) for r in mine)} | {sum(int(r['call_failures']) for r in mine)} | "
            f"{outside}/{len(on_both)} ({(outside / len(on_both) if on_both else float('nan')):.0%}) | "
            f"${sum(float(r['cost_usd'] or 0) for r in mine):.3f} |")
    return out


def width_ratio(rows: list[dict[str, Any]]) -> tuple[float, int]:
    by: dict[int, dict[str, dict[str, Any]]] = {}
    for r in rows:
        by.setdefault(int(r["question_id"]), {})[r["arm"]] = r
    ratios = []
    for v in by.values():
        a, b = v.get("A"), v.get("B")
        if a and b and int(a["ok"]) and int(b["ok"]):
            wa, wb = float(a["width"]), float(b["width"])
            if wa > 0 and not math.isnan(wb):
                ratios.append(wb / wa)
    return (statistics.median(ratios) if ratios else float("nan")), len(ratios)


def render(exp: str, variant: str, baseline: str, config: str, rows: list[dict[str, Any]], skips: Counter, pins: dict[str, str],
           host_lines: list[str], spent: float, calls: int, reps: int = REPS, seed: int = SEED) -> str:
    allp = paired(rows, "all", reps, seed)
    split = [paired([r for r in rows if r["question_type"] == t], t, reps, seed) for t in TYPES]
    ratio, n_ratio = width_ratio(rows)

    def prow(p: Paired) -> str:
        return (f"| {p.label} | {p.n} | {p.clusters} | {p.mean_a:+.2f} | {p.mean_b:+.2f} | {p.mean_diff:+.2f} | "
                f"[{p.ci_low:+.2f}, {p.ci_high:+.2f}] |")

    lines = [
        f"# {exp} number check: {variant} (B) vs {baseline} (A)", "",
        f"Config {config}, polarity check off, both arms replayed now on frozen research. Metric: Metaculus baseline score of the "
        f"CDF the live builder would submit (higher is better), paired B − A, cluster bootstrap by post ({reps:,} reps, seed "
        f"{seed}, {LEVEL:.0%} CI).", "",
        f"**Verdict (fast rule b): {verdict(allp)}**", "",
        "| Questions | N paired | Clusters | Mean A | Mean B | Mean Δ (B − A) | 95% CI |", "|---|---|---|---|---|---|---|",
        prow(allp), *(prow(p) for p in split), "",
        f"Median 10–90 width ratio B/A: {ratio:.3f} (n {n_ratio}).", "",
        "| Arm | Records | CDF built | No CDF | Parse failures (members) | Call failures (members) | Outside 10–90 (paired) | $ |",
        "|---|---|---|---|---|---|---|---|", *arm_lines(rows), "",
        f"Spend: ${spent:.4f} over {calls} calls.", "",
        "## Records not used", "", *(f"- {k}: {v}" for k, v in sorted(skips.items())), "",
        "## Hosts (B-93)", "",
        ("Pinned: " + ", ".join(f"{m} → {h}" for m, h in pins.items()) + "; every call checked.") if pins else "No host pinned.",
        "", *(f"- {ln}" for ln in host_lines), "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- replay
async def replay_jobs(jobs: list[Job], cfgs: dict[str, BotConfig], llm, parallel: int, run_dir: Path, runs_dir: Path, builder
                      ) -> list[dict[str, Any]]:
    sem = asyncio.Semaphore(parallel)

    async def one(job: Job, arm: str) -> dict[str, Any]:
        cfg = cfgs[arm]
        async with sem:
            try:
                out, failed = await replay_record(job.rec, cfg, llm), False
            except ForecastFailed as e:
                out, failed = e.record, True
        src = job.path.relative_to(runs_dir).as_posix() if job.path.is_relative_to(runs_dir) else job.path.name
        stored = {**out.to_dict(), "replayed_from": src, "prompt_version": cfg.prompt_version}
        dest = run_dir / "records" / arm / f"q{job.qid}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(stored, indent=1, ensure_ascii=False), encoding="utf-8")
        stored = json.loads(json.dumps(stored))
        return {**score_record(stored, failed, job, builder), "arm": arm, "replayed_from": src}

    return list(await asyncio.gather(*(one(j, arm) for j in jobs for arm in ARMS)))


def write_scores(rows: list[dict[str, Any]], path: Path) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(SCORE_COLUMNS))
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (int(r["question_id"]), r["arm"])))


def run(args, llm=None, *, runs_dir: Path = aa.RUNS, outcomes_path: Path = aa.OUTCOMES, scaling_path: Path = SCALING,
        out_root: Path = OUT, ledger: Path = aa.LEDGER, builder=None) -> int:
    if args.variant not in prompt_versions.known_versions():
        print(f"unknown prompt version {args.variant!r}; known: {prompt_versions.known_versions()}")
        return 2
    if args.variant == args.baseline_prompt:
        print("the variant is the baseline prompt")
        return 2
    cfgs = arm_configs(args.config, args.baseline_prompt, args.variant)
    try:
        pins = hosts.parse_pins(args.pin_hosts)
    except ValueError as e:
        print(f"REFUSED: {e}")
        return 2
    unknown = sorted(set(pins) - {f.model for f in cfgs["A"].forecasters})
    if unknown:
        print(f"REFUSED: --pin-hosts names models that are not in the {args.config} roster: {unknown}")
        return 2
    outcomes = oc.load_outcomes(outcomes_path)
    scaling = exp010.load_scaling(scaling_path)
    after = date.fromisoformat(args.resolved_after) if args.resolved_after else None
    jobs, skips = pick_records(runs_dir, outcomes, scaling, cfgs["A"], args.n, after)
    history = aa.call_costs(runs_dir)
    per_call = {f.model: max(history.get(f.model, aa.FALLBACK_CALL_USD), aa.MIN_CALL_USD) for f in cfgs["A"].forecasters}
    n_replays = len(jobs) * len(ARMS)
    est = n_replays * sum(per_call.values()) * aa.SAFETY
    kinds = Counter(j.qtype for j in jobs)
    print(f"{args.exp} number check: config {args.config}, {len(cfgs['A'].forecasters)} forecasters, polarity check off; "
          f"arm A prompt {cfgs['A'].prompt_version}, arm B prompt {args.variant}")
    print(f"{len(jobs)} resolved number records picked ({kinds.get('numeric', 0)} numeric, {kinds.get('discrete', 0)} discrete; "
          f"{len({j.cluster for j in jobs})} posts)" + (f", resolved after {after}" if after else ""))
    print(f"  axis from submitted.axis: {sum(1 for j in jobs if (j.rec.get('submitted') or {}).get('axis'))}, "
          f"from the scaling file: {sum(1 for j in jobs if not (j.rec.get('submitted') or {}).get('axis'))}")
    for reason, k in sorted(skips.items()):
        print(f"  skipped {k}: {reason}")
    paths, man_sha = manifest(jobs, runs_dir)
    print(f"manifest sha256 {man_sha} ({len(paths)} records; sources: "
          + ", ".join(f"{k} {v}" for k, v in sorted(Counter(j.path.parent.name.split("-", 1)[-1] for j in jobs).items())) + ")")
    print(f"estimated cost: ${est:.2f} = {n_replays} record replays × ${sum(per_call.values()):.4f} × {aa.SAFETY} safety "
          f"(cap ${args.max_cost:.2f}; {n_replays * len(per_call)} model calls)")
    if not pins:
        print("note: no host pinned; the EXP-015 card pins DeepSeek (B-93). Hosts that served the cheap roster in the archive:")
        for model, c in sorted(hosts.host_counts(p for p, _ in live_records(runs_dir)).items()):
            if model in per_call:
                print(f"  {model}: " + ", ".join(f"{h} {k}" for h, k in c.most_common(5)))
    if not jobs:
        print("nothing to compare")
        return 1
    if est > args.max_cost:
        print(f"REFUSED: the estimate ${est:.2f} is above --max-cost ${args.max_cost:.2f}; lower --n or raise --max-cost")
        return 2
    if args.dry_run:
        print("dry run: no model was called")
        return 0

    builder = builder or _live_cdf()
    if llm is None:
        from dotenv import load_dotenv

        from forecast_engine.clock import SystemClock
        from forecast_engine.llm import OpenRouterClient

        load_dotenv(ROOT / ".env")
        llm = OpenRouterClient(SystemClock(), pinned_hosts=pins)
    capped = aa.CappedLlm(llm, args.max_cost, per_call)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = out_root / args.exp / f"{run_id}-{args.run_kind}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.txt").write_text("\n".join(paths) + "\n", encoding="utf-8")
    rows = asyncio.run(replay_jobs(jobs, cfgs, capped, args.parallel, run_dir, runs_dir, builder))
    write_scores(rows, run_dir / "scores.csv")
    print(f"done: {capped.calls} calls, ${capped.spent:.4f} spent, {capped.refused} calls refused by the cap")

    def ledger_row() -> None:
        if capped.calls:
            aa.append_ledger(run_id, capped.calls, capped.spent,
                             f"number check {args.variant}, {len(jobs)} numeric/discrete records × 2 arms, {args.config} config"
                             + (f", hosts pinned {pins}" if pins else ""), ledger, experiment=args.exp)

    problems, host_lines = hosts.check_pins({arm: sorted((run_dir / "records" / arm).glob("q*.json")) for arm in ARMS}, pins)
    if problems and not args.allow_mixed_hosts:
        (run_dir / "host_check.txt").write_text("\n".join(problems + [""] + host_lines) + "\n", encoding="utf-8")
        print(f"HOST CHECK FAILED: {len(problems)} calls of a pinned model were not served by its pinned host (e.g. "
              f"{problems[0]}); no report written, see {run_dir / 'host_check.txt'} (B-93)")
        ledger_row()
        return 3
    md = render(args.exp, args.variant, args.baseline_prompt, args.config, rows, skips, pins, host_lines, capped.spent,
                capped.calls, args.reps, args.seed)
    md += f"\nManifest sha256 {man_sha} ({len(paths)} records, manifest.txt).\n"
    if problems:
        md += f"\n**{len(problems)} calls ran on another host; compared anyway (--allow-mixed-hosts).**\n"
    (run_dir / "report.md").write_text(md, encoding="utf-8", newline="\n")
    p = paired(rows, "all", args.reps, args.seed)
    print(f"B vs A: N {p.n}, baseline Δ {p.mean_diff:+.2f}, 95% CI [{p.ci_low:+.2f}, {p.ci_high:+.2f}]: {verdict(p)}")
    print(f"report: {run_dir / 'report.md'}")
    ledger_row()
    return 0


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exp", required=True, help="experiment id; outputs go to data/experiments/<exp>/ (e.g. EXP-015)")
    ap.add_argument("--variant", required=True, help="the prompt version to test (arm B)")
    ap.add_argument("--baseline-prompt", default=prompt_versions.LIVE, help="arm A's prompt (default: the live prompt)")
    ap.add_argument("--config", choices=sorted(CONFIGS), default="cheap", help="roster in both arms (default cheap)")
    ap.add_argument("--n", type=int, default=1000, help="at most this many records (default: all eligible)")
    ap.add_argument("--resolved-after", help="only questions resolved after this date, YYYY-MM-DD (the rerun)")
    ap.add_argument("--run-kind", choices=report.RUN_KINDS, default="screen", help="labels the run (default screen)")
    ap.add_argument("--max-cost", type=float, default=5.0, help="refuse above this estimate; hard cap on spend in $ (default 5)")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--reps", type=int, default=REPS, help=f"bootstrap replicates (default {REPS})")
    ap.add_argument("--seed", type=int, default=SEED, help=f"bootstrap seed (default {SEED})")
    ap.add_argument("--dry-run", action="store_true", help="pick records and estimate the cost; no model calls")
    ap.add_argument("--pin-hosts", action="append", metavar="MODEL=HOST",
                    help="serve MODEL only from OpenRouter host HOST, fallbacks off (B-93; repeatable)")
    ap.add_argument("--allow-mixed-hosts", action="store_true", help="write the report even if the host check fails (flagged)")
    return ap


def main(argv: list[str] | None = None) -> int:
    return run(parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
