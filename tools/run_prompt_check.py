"""Prompt check (B-79, protocol §8): the baseline prompt (arm A) vs a new prompt (arm B) on resolved live binary records, with
an option to REUSE arm A from an earlier run so a later check pays only for its new arm.

    # a first check: both arms run now (cheap roster by default, §8.1)
    poetry run python tools/run_prompt_check.py --exp EXP-0xx --variant <prompt version> --dry-run
    # a later check: arm A comes from a stored run; only arm B (and arm A on questions the stored run lacks) is paid for
    poetry run python tools/run_prompt_check.py --exp EXP-0xx --variant <v> \\
        --reuse-baseline data/experiments/EXP-002/20261107T225241Z-screen --baseline-arm B
    # free check of the reuse path: both arms read from the stored run, no model calls (must match that run's report)
    poetry run python tools/run_prompt_check.py --exp B-79 --reuse-baseline <run dir> --baseline-arm B --rescore-arm A
    # drift check: the baseline replayed fresh (A2) vs its stored answers (A), same questions; measures the T11 risk
    poetry run python tools/run_prompt_check.py --exp B-79 --reuse-baseline <run dir> --baseline-arm B --drift-check

Arms differ only by `prompt_version` (both with the polarity check off, as in tools/run_exp002_replay.py); arm A is the
baseline prompt (default: the live prompt). Records are picked as in tools/run_aa_replay.py; `--resolved-after` keeps fresh
questions only (§6.3 rerun). The replay calls only the forecasting models: no research, no follow-up search.

Reuse rules (refused, with the reason, if any fails; nothing is reused then):
- the stored arm's prompt renders the SAME TEXT as the baseline prompt on every fixture case (names may differ: EXP-002's
  arm B "dates-yes-2026-11-07" is the text that went live as "dates-yes-live-2026-11-07");
- every stored record's config_hash equals the baseline config's hash with only the prompt name swapped (same roster, models,
  settings);
- per question: the stored record names the live record it replayed (`replayed_from`) and that record is still in the archive;
  arm B then replays that SAME live record. A question failing this, or missing from the stored run, gets both arms fresh.
Every report that reuses answers says so: the arms were not run at the same moment (T11), and providers drift over time.
The run dir keeps arm A's records (fresh and reused) so the next check can reuse all of them.

Cost: estimated before anything runs (archive mean per call × SAFETY), refused above --max-cost (default $5), hard cap on
spend. Outputs: data/experiments/<exp>/<run>/ (runs.csv, reuse.json, report.md, records/). Spend goes to the budget ledger.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import importlib.util
import json
import sys
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals import prompt_archive as pa  # noqa: E402
from evals import report  # noqa: E402
from evals.outcomes import load_outcomes, resolved_date  # noqa: E402
from evals.replay import replay_record  # noqa: E402
from forecast_engine import prompt_versions  # noqa: E402
from forecast_engine.config import DEFAULT_CONFIG, TOURNAMENT_CONFIG, BotConfig  # noqa: E402
from forecast_engine.core import ForecastFailed  # noqa: E402


def _aa():
    """tools/run_aa_replay.py: record picking, cost estimate, the spend cap, the table and the ledger are shared with it."""
    spec = importlib.util.spec_from_file_location("run_aa_replay", ROOT / "tools" / "run_aa_replay.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


aa = _aa()

DATA = ROOT / "data"
OUT = DATA / "experiments"
CONFIGS = {"frontier": TOURNAMENT_CONFIG, "cheap": DEFAULT_CONFIG}
T11_CAVEAT = ("Arm A was reused from an earlier run: the arms were not run at the same moment (T11), and model providers drift "
              "over time, so part of any difference may be drift, not the prompt. Size of that risk: see the B-79 drift check.")


def arm_configs(name: str, baseline_prompt: str, variant: str | None) -> dict[str, BotConfig]:
    base = replace(CONFIGS[name], polarity_check_model=None)
    cfgs = {"A": replace(base, prompt_version=baseline_prompt)}
    if variant is not None:
        cfgs["B"] = replace(base, prompt_version=variant)
    return cfgs


def same_prompt_text(v1: str, v2: str) -> bool:
    """True if two archived prompt versions render byte-identical prompts on every fixture case (question types, dated or not)."""
    if v1 == v2:
        return True
    t1, t2 = pa.template_for(v1), pa.template_for(v2)
    return all(t1.build(q, items, as_of) == t2.build(q, items, as_of) for _, q, items, as_of in pa.fixture_cases())


@dataclass
class Baseline:
    run_dir: Path
    arm: str
    rows: dict[int, list[dict[str, Any]]] = field(default_factory=dict)  # question_id -> its runs.csv rows
    records: dict[int, Path] = field(default_factory=dict)  # question_id -> stored replayed record (json)
    sources: dict[int, str] = field(default_factory=dict)  # question_id -> replayed_from (relative to the runs dir)
    unusable: dict[int, str] = field(default_factory=dict)  # question_id -> why it can't be reused


class ReuseRefused(Exception):
    pass


def load_baseline(run_dir: Path, arm: str, cfg_a: BotConfig, runs_dir: Path) -> Baseline:
    """Read arm `arm` of a stored run and check it can stand in for a fresh arm A (rules in the module docstring)."""
    table, rec_dir = run_dir / "runs.csv", run_dir / "records" / arm
    if not table.exists() or not rec_dir.is_dir():
        raise ReuseRefused(f"{run_dir} has no runs.csv or no records/{arm}/")
    b = Baseline(run_dir, arm)
    with open(table, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if row["arm"] == arm:
                b.rows.setdefault(int(row["question_id"]), []).append(row)
    if not b.rows:
        raise ReuseRefused(f"arm {arm!r} has no rows in {table}")
    want_members = sorted(f.model for f in cfg_a.forecasters)
    checked_versions: dict[str, bool] = {}
    for path in sorted(rec_dir.glob("q*.json")):
        rec = json.loads(path.read_text(encoding="utf-8"))
        qid = int(rec["question"]["question_id"])
        pv = rec.get("prompt_version")
        if pv is None:
            raise ReuseRefused(f"{path.name} does not name its prompt_version (made before B-79's replay tools?)")
        if pv not in checked_versions:
            try:
                checked_versions[pv] = same_prompt_text(pv, cfg_a.prompt_version)
            except KeyError as e:
                raise ReuseRefused(str(e)) from e
        if not checked_versions[pv]:
            raise ReuseRefused(f"stored prompt {pv!r} does not render the same text as the baseline {cfg_a.prompt_version!r}")
        want_hash = replace(cfg_a, prompt_version=pv).config_hash()
        if rec.get("config_hash") != want_hash:
            raise ReuseRefused(f"{path.name}: config_hash {rec.get('config_hash')} != {want_hash} (the baseline config with "
                               f"prompt {pv!r}): roster or settings differ")
        if sorted(f["model"] for f in rec["forecasters"]) != want_members:
            raise ReuseRefused(f"{path.name}: members differ from the baseline roster")
        src = rec.get("replayed_from")
        if qid not in b.rows:
            b.unusable[qid] = "record without rows in runs.csv"
        elif not src or not (runs_dir / src).exists():
            b.unusable[qid] = f"its live record ({src}) is not in the archive"
        else:
            b.records[qid], b.sources[qid] = path, src
    for qid in b.rows:
        if qid not in b.records and qid not in b.unusable:
            b.unusable[qid] = "no stored record json"
    return b


def _resolved_after(row: dict[str, Any], day: date) -> bool:
    d = resolved_date(row)
    return d is not None and date.fromisoformat(str(d)[:10]) > day


def plan(picked, base: Baseline | None, runs_dir: Path):
    """[(path, record, outcome, resolved_at, arms to run fresh)], reused question ids. A reused question replays the very live
    record its stored arm A was made from, so both arms see the same inputs."""
    jobs, reused = [], []
    for path, rec, outcome, resolved_at in picked:
        qid = rec["question"]["question_id"]
        if base is not None and qid in base.records:
            src = runs_dir / base.sources[qid]
            jobs.append((src, json.loads(src.read_text(encoding="utf-8")), outcome, resolved_at, None))
            reused.append(qid)
        else:
            jobs.append((path, rec, outcome, resolved_at, "both"))
    return jobs, reused


def reused_rows(base: Baseline, qid: int, arm: str, outcome: int, resolved_at: str) -> list[dict[str, Any]]:
    """The stored rows, relabelled; outcome and resolved_at from today's outcomes file (they can't differ for a resolved
    question, but the current file is the one scored)."""
    return [{**{k: r.get(k, "") for k in aa.COLUMNS}, "arm": arm, "outcome": outcome, "resolved_at": resolved_at}
            for r in base.rows[qid]]


async def replay_jobs(work, cfgs: dict[str, BotConfig], llm, parallel: int, run_dir: Path, runs_dir: Path):
    """work: [(path, record, outcome, resolved_at, arm)] -> table rows. Each replayed record is saved under records/<arm>."""
    sem = asyncio.Semaphore(parallel)

    async def one(path, rec, outcome, resolved_at, arm):
        cfg = cfgs[arm]
        async with sem:
            try:
                out, failed = await replay_record(rec, cfg, llm), False
            except ForecastFailed as e:
                out, failed = e.record, True
        dest = run_dir / "records" / arm / f"q{out.question.question_id}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        src = path.relative_to(runs_dir).as_posix() if path.is_relative_to(runs_dir) else path.name
        dest.write_text(json.dumps({**out.to_dict(), "replayed_from": src, "prompt_version": cfg.prompt_version},
                                   indent=1, ensure_ascii=False), encoding="utf-8")
        return aa.table_rows(out, arm, outcome, resolved_at, failed)

    return [row for rows in await asyncio.gather(*(one(*w) for w in work)) for row in rows]


def drift_lines(rows: list[dict[str, Any]], a: str = "A", a2: str = "A2") -> list[str]:
    """Mean |p(A2) − p(A)| per member and for the aggregate, over questions where both arms have a number."""
    by: dict[tuple[str, str], dict[str, float]] = {}
    for r in rows:
        if r["p"] not in ("", None):
            by.setdefault((str(r["question_id"]), r["member"]), {})[r["arm"]] = float(r["p"])
    per: dict[str, list[float]] = {}
    for (_, member), arms in by.items():
        if a in arms and a2 in arms:
            per.setdefault(member, []).append(abs(arms[a2] - arms[a]))
    return [f"{m}: mean |Δp| {sum(v) / len(v):.3f}, max {max(v):.3f} (n {len(v)})" for m, v in sorted(per.items())]


def run(args, llm=None, *, runs_dir: Path = aa.RUNS, outcomes_path: Path = aa.OUTCOMES, out_root: Path = OUT,
        ledger: Path = aa.LEDGER) -> int:
    mode = "drift" if args.drift_check else "rescore" if args.rescore_arm else "check"
    if mode != "check" and not args.reuse_baseline:
        print("--drift-check and --rescore-arm need --reuse-baseline")
        return 2
    if mode == "check" and not args.variant:
        print("--variant is required (the prompt version to test)")
        return 2
    if args.variant and mode != "check":
        print("--variant can't be combined with --drift-check or --rescore-arm")
        return 2
    if args.variant and args.variant not in prompt_versions.known_versions():
        print(f"unknown prompt version {args.variant!r}; known: {prompt_versions.known_versions()}")
        return 2
    if args.variant == args.baseline_prompt:
        print("the variant is the baseline prompt")
        return 2
    cfgs = arm_configs(args.config, args.baseline_prompt, args.variant)
    if mode == "drift":
        cfgs["A2"] = cfgs["A"]
    base = None
    if args.reuse_baseline:
        try:
            base = load_baseline(Path(args.reuse_baseline), args.baseline_arm, cfgs["A"], runs_dir)
        except ReuseRefused as e:
            print(f"REUSE REFUSED: {e}")
            return 2
    outcomes = load_outcomes(outcomes_path)
    after = date.fromisoformat(args.resolved_after) if args.resolved_after else None
    if after is not None:
        outcomes = {q: row for q, row in outcomes.items() if _resolved_after(row, after)}
    picked, skips = aa.pick_records(runs_dir, outcomes, cfgs["A"], args.n)
    if mode != "check":  # both arms on the reused questions only
        picked = [p for p in picked if base and p[1]["question"]["question_id"] in base.records]
    jobs, reused = plan(picked, base, runs_dir)
    fresh_arm = {"check": "B", "drift": "A2", "rescore": None}[mode]
    work = []
    for path, rec, outcome, resolved_at, which in jobs:
        if which == "both":
            work += [(path, rec, outcome, resolved_at, "A"), (path, rec, outcome, resolved_at, "B")]
        elif fresh_arm:
            work.append((path, rec, outcome, resolved_at, fresh_arm))
    history = aa.call_costs(runs_dir)
    per_call = {f.model: max(history.get(f.model, aa.FALLBACK_CALL_USD), aa.MIN_CALL_USD) for f in cfgs["A"].forecasters}
    est = len(work) * sum(per_call.values()) * aa.SAFETY
    print(f"{args.exp} prompt check ({mode}): config {args.config}, {len(cfgs['A'].forecasters)} forecasters, polarity check off; "
          f"arm A prompt {cfgs['A'].prompt_version}" + (f", arm B prompt {args.variant}" if args.variant else ""))
    print(f"{len(picked)} resolved binary records picked" + (f", resolved after {after}" if after else ""))
    for reason, k in sorted(skips.items()):
        print(f"  skipped {k}: {reason}")
    if base is not None:
        print(f"baseline reused from {base.run_dir} arm {base.arm}: {len(reused)} questions reused, "
              f"{len(picked) - len(reused)} run fresh in both arms; {len(base.unusable)} stored questions not reusable")
        for qid, why in sorted(base.unusable.items()):
            print(f"  not reusable q{qid}: {why}")
    print(f"estimated cost: ${est:.2f} = {len(work)} record replays × ${sum(per_call.values()):.4f} × {aa.SAFETY} safety "
          f"(cap ${args.max_cost:.2f})")
    if not picked:
        print("nothing to compare")
        return 1
    if est > args.max_cost:
        print(f"REFUSED: the estimate ${est:.2f} is above --max-cost ${args.max_cost:.2f}; lower --n or raise --max-cost")
        return 2
    if args.dry_run:
        print("dry run: no model was called")
        return 0

    other = None
    if mode == "rescore":
        try:
            other = load_baseline(base.run_dir, args.rescore_arm, replace(
                cfgs["A"], prompt_version=_stored_version(base.run_dir, args.rescore_arm)), runs_dir)
        except ReuseRefused as e:
            print(f"RESCORE REFUSED: {e}")
            return 2
    rows: list[dict[str, Any]] = []
    for path, rec, outcome, resolved_at, which in jobs:
        if which is None:
            qid = rec["question"]["question_id"]
            rows += reused_rows(base, qid, "A", outcome, resolved_at)
            if other is not None and qid in other.rows:
                rows += reused_rows(other, qid, "B", outcome, resolved_at)
    if mode == "rescore":
        return _rescore_report(args, rows)

    if llm is None:
        from dotenv import load_dotenv

        from forecast_engine.clock import SystemClock
        from forecast_engine.llm import OpenRouterClient

        load_dotenv(ROOT / ".env")
        llm = OpenRouterClient(SystemClock())
    capped = aa.CappedLlm(llm, args.max_cost, per_call)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = out_root / args.exp / f"{run_id}-{'drift' if mode == 'drift' else args.run_kind}"
    run_dir.mkdir(parents=True, exist_ok=True)
    rows += asyncio.run(replay_jobs(work, cfgs, capped, args.parallel, run_dir, runs_dir))
    for qid in reused:  # keep the reused arm A in this run dir, so the next check can reuse every question
        src = json.loads(base.records[qid].read_text(encoding="utf-8"))
        dest = run_dir / "records" / "A" / f"q{qid}.json"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps({**src, "reused_from": f"{base.run_dir.name}/records/{base.arm}"}, indent=1,
                                   ensure_ascii=False), encoding="utf-8")
    table = run_dir / "runs.csv"
    aa.write_table(rows, table)
    (run_dir / "reuse.json").write_text(json.dumps({
        "baseline_run": str(base.run_dir) if base else None, "baseline_arm": base.arm if base else None,
        "reused_questions": sorted(reused), "fresh_questions": sorted(
            p[1]["question"]["question_id"] for p in picked if p[1]["question"]["question_id"] not in reused),
    }, indent=1), encoding="utf-8")
    print(f"done: {capped.calls} calls, ${capped.spent:.4f} spent, {capped.refused} calls refused by the cap")
    cand = "A2" if mode == "drift" else "B"
    title = f"{args.exp} " + ("B-79 drift check (fresh baseline A2 vs stored A)" if mode == "drift" else f"prompt check {args.variant}")
    an = report.analyse(report.read_table(table), "A", name=title, decision_arms=[cand], run_kind=args.run_kind,
                        meta=report.provenance(table))
    md = report.render_report(an)
    lines = []
    if reused:
        lines.append(f"{len(reused)} of {len(picked)} questions reuse arm A from {base.run_dir.name} (arm {base.arm}); "
                     f"{len(picked) - len(reused)} ran both arms now.")
        lines.append(T11_CAVEAT if mode == "check" else
                     "This run measures that drift: arm A2 is the same baseline config replayed now on the same records.")
    if mode == "drift":
        lines += drift_lines(rows)
    if lines:
        md += "\n## Baseline reuse (B-79)\n\n" + "".join(f"- {ln}\n" for ln in lines)
    (run_dir / "report.md").write_text(md, encoding="utf-8", newline="\n")
    c = an.comparisons[cand][an.metric]
    print(f"{cand} vs A: N {c.n}, log Δ {report._f(c.mean_diff)}, 95% CI [{report._f(c.ci_low)}, {report._f(c.ci_high)}]: "
          f"{c.verdict_words()}")
    for ln in lines:
        print(ln)
    print(f"report: {run_dir / 'report.md'}")
    if capped.calls:
        what = "B-79 drift check" if mode == "drift" else f"prompt check {args.variant}"
        aa.append_ledger(run_id, capped.calls, capped.spent,
                         f"{what}, {len(picked)} binary records ({len(reused)} with reused arm A), {args.config} config",
                         ledger, experiment=args.exp)
    return 0


def _stored_version(run_dir: Path, arm: str) -> str:
    first = next((run_dir / "records" / arm).glob("q*.json"), None)
    if first is None:
        raise ReuseRefused(f"{run_dir} has no records/{arm}/")
    return json.loads(first.read_text(encoding="utf-8"))["prompt_version"]


def _rescore_report(args, rows: list[dict[str, Any]]) -> int:
    """Free check: score stored arm A vs stored arm B through the reuse path. Prints only; writes nothing."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        table = Path(tmp) / "runs.csv"
        aa.write_table(rows, table)
        an = report.analyse(report.read_table(table), "A", name="B-79 rescore", decision_arms=["B"], run_kind=args.run_kind)
    c = an.comparisons["B"][an.metric]
    print(f"rescore (no model calls): stored arm {args.rescore_arm} as B vs stored arm {args.baseline_arm} as A")
    print(f"B vs A: N {c.n}, log Δ {report._f(c.mean_diff)}, 95% CI [{report._f(c.ci_low)}, {report._f(c.ci_high)}]: "
          f"{c.verdict_words()}")
    print("compare with the stored run's report.md (swap the sign if its arms were the other way round)")
    return 0


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--exp", required=True, help="experiment id; outputs go to data/experiments/<exp>/ (e.g. EXP-012)")
    ap.add_argument("--variant", help="the prompt version to test (arm B); registered in forecast_engine/prompt_versions.py")
    ap.add_argument("--baseline-prompt", default=prompt_versions.LIVE, help="arm A's prompt (default: the live prompt)")
    ap.add_argument("--config", choices=sorted(CONFIGS), default="cheap",
                    help="roster in both arms (default cheap: screens run on the cheap roster, protocol §8)")
    ap.add_argument("--reuse-baseline", metavar="RUN_DIR", help="a stored run dir whose arm (--baseline-arm) is reused as arm A")
    ap.add_argument("--baseline-arm", default="A", help="which arm of --reuse-baseline is the baseline (EXP-002's live prompt: B)")
    ap.add_argument("--rescore-arm", help="free check: read arm B from this arm of the same stored run; no model calls")
    ap.add_argument("--drift-check", action="store_true",
                    help="replay the baseline now (arm A2) on the reused questions and compare with the stored answers")
    ap.add_argument("--n", type=int, default=1000, help="at most this many resolved binary records (default: all eligible)")
    ap.add_argument("--resolved-after", help="only questions resolved after this date, YYYY-MM-DD (the §6.3 rerun)")
    ap.add_argument("--run-kind", choices=report.RUN_KINDS, default="screen", help="labels the run (default screen)")
    ap.add_argument("--max-cost", type=float, default=5.0, help="refuse above this estimate; hard cap on spend in $ (default 5)")
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true", help="pick records and estimate the cost; no model calls")
    return ap


def main(argv: list[str] | None = None) -> int:
    return run(parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
