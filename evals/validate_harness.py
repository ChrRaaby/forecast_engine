"""Harness validation (B-32; evaluation protocol §7 "Harness validation before first use", threat T13).

Before any experiment result is trusted, the measuring tool itself must be shown to work. Four checks, one PASS/FAIL line each:

1. Reproducibility: the report generator (`evals.report`) gives identical numbers on the same input, on the same rows in another
   order, and a changed bootstrap seed moves only the bootstrap CIs and SEs, within a stated tolerance. The live-replay prompt
   check (`evals.replay --check`) runs on a synthetic record offline and on every archived record with `--real`.
2. A/A: two arms with the same config and independent noise. The paired CI must contain 0 in about 95% of seeded trials (exact
   binomial band) and the decision rule must say "adopt" no more often than its false-alarm rate (also with 3 candidate arms at
   the Bonferroni level, protocol §5).
3. Leakage injection: a planted document dated after as_of is blocked by the bundle guard, the AskNews archive date filter, the
   research cache's read-time recheck, and the replay path (refused before any model call); prompts rendered for an as-of
   fixture contain no date after as_of (T5).
4. Trivial baselines: always-50% scores exactly 0 on the Metaculus baseline score, the base-rate forecaster's Brier equals
   base·(1−base), and a forecaster that knows the true probability beats both with a CI excluding 0.

    poetry run python -m evals.validate_harness                  # = --offline: synthetic data only, no network, no keys (CI)
    poetry run python -m evals.validate_harness --real runs.csv  # a real long table in evals.report's input format (PC)

`--real` runs checks 1, 2 and 4 on the table (check 2 needs an A/A pair of arms, `A` and `A2` by default, as written by
`tools/run_aa_replay.py`), the leakage checks on synthetic fixtures, and the replay hooks on `data/runs/` when it exists. It never
prints research or article text: only counts, scores, dates and file paths. Exit code 0 = every check passed, 1 = a check
failed, 2 = nothing failed but a check was skipped (e.g. no A/A pair in the table).

Hook for B-30: the runner calls `run_checks(mode)` (or the single `check_*` functions) after any harness change and refuses to
run an experiment unless it returns no FAIL. When B-30 adds its own run records, add a `check_runner_*` function here that
re-scores a finished run from its stored records and compares it with the stored report (protocol §7 reproducibility).
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import random
import re
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, fields, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np

from forecast_engine.clock import FixedClock
from forecast_engine.config import DEFAULT_CONFIG
from forecast_engine.core import ForecastFailed, forecast
from forecast_engine.schema import LlmCall, QuestionSnapshot, ResearchBundle, ResearchItem

from . import replay, report, scoring
from .report_demo import synthetic_rows, write_csv

PASS, FAIL, SKIP, INFO = "PASS", "FAIL", "SKIP", "INFO"

SHUFFLE_REL_TOL = 1e-12  # row order changes the summation order of floats, nothing else
SEED_TOLERANCE = 0.05  # a changed seed may move a CI end by at most 5% of the CI width (expected ≈ 0.7% at 10,000 reps)
AA_TRIALS = 200
AA_FAMILY_TRIALS = 100
BAND_TAIL = 0.001  # each side of the exact binomial band: a correct harness fails a band check about 1 time in 500
EXACT_TOL = 1e-12
FIXTURE_AS_OF = datetime(2025, 3, 14, 12, 0, tzinfo=timezone.utc)  # as-of fixture: far from any real "today"
ALWAYS_50, BASE_RATE, TRUTH = "_always50", "_baserate", "_truth"

# The bare statistical part of every decision rule: adopt needs only a positive point estimate and a CI excluding 0. Used on A/A
# tables, where the extra conditions of a real rule (calibration, failure rate) would only hide a statistics bug.
CI_ONLY_RULE = report.DecisionRule(
    name="CI only (A/A check)", max_reliability_increase=None, max_failure_rate_increase=None, kill_if_point_at_most=None,
)


@dataclass
class Result:
    check: str  # "1 reproducibility", ...
    status: str  # PASS / FAIL / SKIP / INFO
    headline: str
    details: list[str] = field(default_factory=list)


def _sub(check: str, name: str, fn: Callable[[], tuple[bool, str] | Result]) -> Result:
    """Run one sub-check; an exception is a FAIL (type and message only)."""
    try:
        out = fn()
    except Exception as e:  # noqa: BLE001 - any crash in a check means the check did not pass
        return Result(check, FAIL, f"{name}: crashed with {type(e).__name__}: {str(e)[:300]}")
    if isinstance(out, Result):
        return out
    ok, words = out
    return Result(check, PASS if ok else FAIL, f"{name}: {words}")


# --------------------------------------------------------------------------- binomial bands (no scipy)
def binom_cdf(k: int, n: int, p: float) -> float:
    return sum(math.comb(n, i) * p**i * (1.0 - p) ** (n - i) for i in range(0, k + 1))


def binom_band(n: int, p: float, tail: float = BAND_TAIL) -> tuple[int, int]:
    """Smallest lo with P(X ≤ lo) ≥ tail and smallest hi with P(X ≤ hi) ≥ 1 − tail, for X ~ Binomial(n, p)."""
    lo = next(k for k in range(n + 1) if binom_cdf(k, n, p) >= tail)
    hi = next(k for k in range(n + 1) if binom_cdf(k, n, p) >= 1.0 - tail)
    return lo, hi


# --------------------------------------------------------------------------- helpers shared by the checks
def _rows(dicts: Sequence[dict[str, Any]]) -> list[report.Row]:
    return [report.parse_row(d, i + 1) for i, d in enumerate(dicts)]


def _all_comparisons(an: report.Analysis) -> dict[str, report.Comparison]:
    out: dict[str, report.Comparison] = {}
    for arm, by_metric in an.comparisons.items():
        for key, c in by_metric.items():
            out[f"{arm}/{key}"] = c
    for arm, c in an.other_mode.items():
        out[f"{arm}/other-mode"] = c
    for arm, c in an.control_comparisons.items():
        out[f"{arm}/vs-control"] = c
    for r in an.member_rows:
        if r["vs_baseline"] is not None:
            out[f"{r['arm']}/member {r['member']}"] = r["vs_baseline"]
    for col, rows in an.breakdowns.items():
        for r in rows:
            out[f"{r['comparison'].arm}/{col}={r['group']}"] = r["comparison"]
    return out


_CI_FIELDS = ("ci_low", "ci_high", "se_boot")


def _numbers(an: report.Analysis, skip: Sequence[str] = ()) -> dict[str, Any]:
    """Every number the analysis produced (comparisons, per-arm summaries, verdicts), optionally without some Comparison fields."""
    out: dict[str, Any] = {}
    for name, c in _all_comparisons(an).items():
        for f in fields(c):
            if f.name not in skip:
                out[f"cmp {name} {f.name}"] = getattr(c, f.name)
    for arm, s in an.summaries.items():
        for f in fields(s):
            out[f"arm {arm} {f.name}"] = getattr(s, f.name)
    for arm, d in an.decisions.items():
        out[f"decision {arm}"] = d.verdict
    return out


def _exact(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Bit-for-bit equal (repr, so nan == nan)."""
    return {k: repr(v) for k, v in a.items()} == {k: repr(v) for k, v in b.items()}


def _max_rel_diff(x: Any, y: Any) -> float:
    """Largest relative difference between two nested structures of numbers; inf if their shape or non-numbers differ."""
    if isinstance(x, dict) and isinstance(y, dict):
        if x.keys() != y.keys():
            return math.inf
        return max((_max_rel_diff(x[k], y[k]) for k in x), default=0.0)
    if isinstance(x, (list, tuple)) and isinstance(y, (list, tuple)):
        if len(x) != len(y):
            return math.inf
        return max((_max_rel_diff(u, v) for u, v in zip(x, y)), default=0.0)
    if isinstance(x, float) and isinstance(y, float):
        if math.isnan(x) or math.isnan(y):
            return 0.0 if math.isnan(x) and math.isnan(y) else math.inf
        return 0.0 if x == y else abs(x - y) / max(abs(x), abs(y), 1e-300)
    return 0.0 if x == y else math.inf


def _seed_moves(a: report.Analysis, b: report.Analysis) -> tuple[float, int, list[str]]:
    """(largest CI-end move as a fraction of the CI width, comparisons that moved at all, offenders over the tolerance).
    Only comparisons with at least FEW_CLUSTERS clusters are held to the tolerance: with fewer, percentile quantiles jump."""
    worst, moved, bad = 0.0, 0, []
    cb = _all_comparisons(b)
    for name, c in _all_comparisons(a).items():
        d = cb[name]
        if c.n == 0:
            continue
        if (c.ci_low, c.ci_high, c.se_boot) != (d.ci_low, d.ci_high, d.se_boot):
            moved += 1
        if c.n_clusters < report.FEW_CLUSTERS:
            continue
        width = c.ci_high - c.ci_low
        move = max(abs(c.ci_low - d.ci_low), abs(c.ci_high - d.ci_high))
        frac = move / width if width > 0 else (0.0 if move == 0 else math.inf)
        se_frac = abs(c.se_boot - d.se_boot) / c.se_boot if c.se_boot > 0 else 0.0
        worst = max(worst, frac)
        if frac > SEED_TOLERANCE or se_frac > SEED_TOLERANCE:
            bad.append(f"{name}: CI end moved {frac:.1%} of the width, SE {se_frac:.1%}")
    return worst, moved, bad


def _shuffled_copy(src: Path, dst: Path, seed: int) -> None:
    """The same table with its data rows in another order (CSV via the csv module, JSON lines line by line)."""
    rng = random.Random(seed)
    text = src.read_text(encoding="utf-8-sig")
    if src.suffix.lower() == ".csv":
        reader = csv.DictReader(text.splitlines())
        rows = list(reader)
        rng.shuffle(rows)
        with open(dst, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(reader.fieldnames or []))
            w.writeheader()
            w.writerows(rows)
    else:
        lines = [ln for ln in text.splitlines() if ln.strip()]
        rng.shuffle(lines)
        dst.write_text("\n".join(lines) + "\n", encoding="utf-8")


def trivial_rows(prep: report.Prepared, names: Sequence[str] = (ALWAYS_50, BASE_RATE)) -> tuple[list[report.Row], float]:
    """Always-50% and in-sample base-rate forecasts on the scored questions of a prepared table, and the base rate."""
    qids = sorted(prep.outcomes)
    base = sum(prep.outcomes[q] for q in qids) / len(qids)
    p = {ALWAYS_50: 0.5, BASE_RATE: base}
    rows = [
        report.Row(question_id=q, cluster_id=prep.clusters[q], arm=name, member=report.AGGREGATE, repeat=1, p=p[name],
                   outcome=prep.outcomes[q], question_type="binary")
        for name in names for q in qids
    ]
    return rows, base


def _analyse(rows: Sequence[report.Row], baseline: str, **kw: Any) -> report.Analysis:
    kw.setdefault("by", ())
    kw.setdefault("forward_source", "")
    return report.analyse(rows, baseline, **kw)


def _decide(an: report.Analysis, arm: str, rule: report.DecisionRule) -> str:
    rel_a, rel_b = an.calibration_paired.get(arm, (float("nan"), float("nan")))
    return report.apply_decision_rule(
        rule, an.comparisons[arm][rule.metric], reliability_arm=rel_a, reliability_base=rel_b,
        failure_rate_arm=an.summaries[arm].failure_rate, failure_rate_base=an.summaries[an.baseline].failure_rate,
    ).verdict


# --------------------------------------------------------------------------- synthetic live records (fake LLM, no network)
class FakeLlm:
    """Answers every prompt with a parseable forecast for its question type; counts calls and keeps the prompts."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    @property
    def calls(self) -> int:
        return len(self.prompts)

    async def complete(self, *, model, prompt, purpose, max_tokens, reasoning_effort=None) -> LlmCall:
        self.prompts.append(prompt)
        if "Percentile 10" in prompt:
            out = "\n".join(f"Percentile {p}: {p}" for p in (10, 20, 40, 60, 80, 90))
        elif "The options are" in prompt:
            out = "Red: 60%\nBlue: 40%"
        else:
            out = "Probability: 40%"
        return LlmCall(purpose=purpose, provider="fake", model=model, params={}, prompt=prompt, requested_at=FIXTURE_AS_OF,
                       output=out, cost_usd=0.0)


def fixture_question(qtype: str = "binary", qid: int = 9001) -> QuestionSnapshot:
    common = dict(question_id=qid, post_id=qid + 1, question_text="Will the synthetic event happen?",
                  background_info="Synthetic background.", resolution_criteria="Resolves per the synthetic rule.",
                  open_time=FIXTURE_AS_OF - timedelta(days=2))
    if qtype == "multiple_choice":
        return QuestionSnapshot(question_type="multiple_choice", options=("Red", "Blue"), **common)
    if qtype == "numeric":
        return QuestionSnapshot(question_type="numeric", unit_of_measure="units", lower_bound=0.0, upper_bound=100.0, **common)
    return QuestionSnapshot(question_type="binary", **common)


def fixture_bundle(as_of: datetime = FIXTURE_AS_OF) -> ResearchBundle:
    items = [
        ResearchItem(source="synthetic", text="Synthetic article one.", title="Synthetic headline one",
                     url="https://news.example/one", published_at=as_of - timedelta(days=3)),
        ResearchItem(source="synthetic", text="Synthetic article two.", title="Synthetic headline two", url=None,
                     published_at=None),  # undated, like a Gemini-grounded item in a live record
    ]
    return ResearchBundle(as_of=as_of, providers=["synthetic"], items=items, calls=[])


def synthetic_record(qtype: str = "binary", as_of: datetime = FIXTURE_AS_OF, qid: int = 9001) -> dict[str, Any]:
    """What the record writer stores for one live forecast, built offline with the fake LLM."""
    try:
        q, clock, bundle = fixture_question(qtype, qid), FixedClock(as_of), fixture_bundle(as_of)
        rec = asyncio.run(forecast(q, clock, bundle, DEFAULT_CONFIG, FakeLlm()))
    except ForecastFailed as e:
        rec = e.record
    return json.loads(json.dumps(rec.to_dict()))


# --------------------------------------------------------------------------- 1. reproducibility
C1 = "1 reproducibility"


def _repro_on_file(path: Path, baseline: str, extra: Callable[[list[report.Row]], list[report.Row]], tmp: Path,
                   rule: report.DecisionRule | None, reps: int, seed: int) -> list[Result]:
    def run(p: Path, s: int = seed) -> report.Analysis:
        rows = report.read_table(p)
        return _analyse(rows + extra(rows), baseline, reps=reps, seed=s, rule=rule, by=("question_type", "source"),
                        forward_source=report.FORWARD_SOURCE)

    out: list[Result] = []
    a1, a2 = run(path), run(path)
    meta = report.provenance(path)

    def same_twice():
        m1 = report.render_report(replace(a1, meta=meta))
        m2 = report.render_report(replace(a2, meta=meta))
        return m1 == m2 and _exact(_numbers(a1), _numbers(a2)), (
            f"same input twice gives the same report ({len(m1):,} bytes incl. the sha256 header) and bit-identical numbers"
        )

    out.append(_sub(C1, "same input", same_twice))

    def shuffled():
        shuf = tmp / f"shuffled{path.suffix}"
        _shuffled_copy(path, shuf, seed=seed)
        a3 = run(shuf)
        rel = _max_rel_diff(_numbers(a1), _numbers(a3))
        same = report.render_report(a1) == report.render_report(a3) and rel <= SHUFFLE_REL_TOL
        return same, (
            f"rows in another order give the same report bytes (input hash aside); unrounded numbers differ by at most "
            f"{rel:.0e} relative (floating-point summation order; allowed {SHUFFLE_REL_TOL:.0e})"
        )

    out.append(_sub(C1, "shuffled rows", shuffled))

    def seed_change():
        b = run(path, seed + 1)
        same_rest = _exact(_numbers(a1, skip=_CI_FIELDS), _numbers(b, skip=_CI_FIELDS))
        worst, moved, bad = _seed_moves(a1, b)
        ok = same_rest and moved > 0 and not bad
        words = (
            f"seed {seed} → {seed + 1}: everything but bootstrap CIs/SEs identical: {'yes' if same_rest else 'NO'}; "
            f"{moved} CI(s) moved, largest move {worst:.1%} of the CI width (allowed {SEED_TOLERANCE:.0%})"
        )
        if moved == 0:
            words += "; the seed changed nothing, so it is not being used"
        r = Result(C1, PASS if ok else FAIL, f"seed change: {words}", bad[:5])
        return r

    out.append(_sub(C1, "seed change", seed_change))
    return out


def check_reproducibility_offline(tmp: Path) -> list[Result]:
    dicts = synthetic_rows(
        120, 50, arm_noise={"A": 0.8, "B": 0.5, "C": 0.8}, arm_scale={"A": 1.6, "B": 1.0, "C": 1.6},
        arm_failure={"A": 0.01, "B": 0.03, "C": 0.08}, arm_cost={"A": 0.01, "B": 0.03, "C": 0.05}, k=3, seed=11,
    )
    path = tmp / "synthetic.csv"
    write_csv(dicts, path)
    out = _repro_on_file(path, "A", lambda rows: [], tmp, report.EXP005_RULE, report.DEFAULT_REPS, report.DEFAULT_SEED)

    def prompts():
        rec = synthetic_record("binary")
        tampered = json.loads(json.dumps(rec))
        tampered["research"]["items"][0]["text"] = "Edited after the fact."
        ok = replay.check_record(rec) and not replay.check_record(tampered)
        return ok, "replay prompt check (`evals.replay --check`) reproduces a synthetic live record and catches a tampered copy"

    out.append(_sub(C1, "replay prompts", prompts))
    return out


def check_replay_archive(runs_dir: Path) -> list[Result]:
    """The `evals.replay --check` hook plus the replay leakage guard over every archived live record (no text printed)."""
    if not runs_dir.exists():
        return [Result(C1, SKIP, f"replay prompts: {runs_dir} not found, so `evals.replay --check` was not run "
                                 "(it needs the private data repo; run it on the PC)")]
    recs = replay.live_records(runs_dir)
    if not recs:
        return [Result(C1, SKIP, f"replay prompts: no live records under {runs_dir}")]
    bad = [p for p, r in recs if not replay.check_record(r)]
    refused: list[Path] = []
    for p, r in recs:
        try:
            replay.assert_record_as_of(r)
        except Exception:  # noqa: BLE001 - LeakageError or a malformed date: either way not replayable
            refused.append(p)
    res = Result(
        C1, PASS if not bad else FAIL,
        f"replay prompts: {len(recs) - len(bad)}/{len(recs)} archived live records reproduce their prompts exactly",
        [f"MISMATCH {_rel(p, runs_dir)}" for p in bad[:20]],
    )
    guard = Result(
        C3, INFO,
        f"replay guard on the archive: {len(refused)}/{len(recs)} live records would be refused (research dated after as_of)",
        [f"refused {_rel(p, runs_dir)}" for p in refused[:20]],
    )
    return [res, guard]


def _rel(p: Path, root: Path) -> str:
    try:
        return p.relative_to(root).as_posix()
    except ValueError:
        return p.name


# --------------------------------------------------------------------------- 2. A/A
C2 = "2 A/A"


def aa_trials(trials: int = AA_TRIALS, n_questions: int = 80, n_clusters: int = 40, reps: int = 2000) -> dict[str, int]:
    """Seeded A/A tables (report_demo, the same arm settings twice, independent noise): CI coverage and adopt counts."""
    covered = adopt_ci = adopt_exp005 = 0
    for t in range(trials):
        dicts = synthetic_rows(
            n_questions, n_clusters, arm_noise={"A": 0.8, "A2": 0.8}, arm_scale={"A": 1.3, "A2": 1.3},
            arm_failure={"A": 0.02, "A2": 0.02}, k=1, seed=10_000 + t,
        )
        an = _analyse(_rows(dicts), "A", reps=reps, seed=t, rule=CI_ONLY_RULE)
        c = an.comparisons["A2"][an.metric]
        covered += c.ci_low <= 0.0 <= c.ci_high
        adopt_ci += an.decisions["A2"].verdict == "adopt"
        adopt_exp005 += _decide(an, "A2", report.EXP005_RULE) == "adopt"
    return {"trials": trials, "covered": covered, "adopt_ci": adopt_ci, "adopt_exp005": adopt_exp005}


def aa_family_trials(trials: int = AA_FAMILY_TRIALS, n_questions: int = 80, n_clusters: int = 40, reps: int = 6000) -> dict[str, int]:
    """A baseline and 3 candidate arms, all the same config: how often does at least one candidate get adopted?"""
    any_adopt = 0
    arms = ("A", "B", "C", "D")
    for t in range(trials):
        dicts = synthetic_rows(
            n_questions, n_clusters, members=("m1",), arm_noise={a: 0.8 for a in arms}, arm_scale={a: 1.3 for a in arms},
            k=1, seed=20_000 + t,
        )
        an = _analyse(_rows(dicts), "A", reps=reps, seed=t, rule=CI_ONLY_RULE)
        any_adopt += any(d.verdict == "adopt" for d in an.decisions.values())
    return {"trials": trials, "any_adopt": any_adopt}


def check_aa_offline(trials: int = AA_TRIALS, family_trials: int = AA_FAMILY_TRIALS) -> list[Result]:
    out: list[Result] = []
    stats: dict[str, int] = {}

    def coverage():
        stats.update(aa_trials(trials))
        lo, hi = binom_band(trials, 0.95)
        k = stats["covered"]
        return lo <= k <= hi, (
            f"paired 95% CI contains 0 in {k}/{trials} seeded A/A trials ({k / trials:.1%}); allowed {lo}–{hi} "
            f"(exact binomial band around 95%, {BAND_TAIL:.1%} per side)"
        )

    out.append(_sub(C2, "CI coverage", coverage))

    def false_adopt():
        if not stats:
            stats.update(aa_trials(trials))
        _, hi = binom_band(trials, 0.025)
        k, k5 = stats["adopt_ci"], stats["adopt_exp005"]
        rate = report.FAMILY_ALPHA
        ok = k <= hi and k5 <= k
        return ok, (
            f"decision rule said adopt in {k}/{trials} A/A trials ({k / trials:.1%}; EXP-005 rule {k5}). Stated false-alarm "
            f"rate {rate:.0%} two-sided, so adopt (one side) should be ~{rate / 2:.1%}; allowed ≤ {hi} ({hi / trials:.1%})"
        )

    out.append(_sub(C2, "false adopt", false_adopt))

    def family():
        f = aa_family_trials(family_trials)
        _, hi = binom_band(family_trials, 0.025)
        k = f["any_adopt"]
        level = report._level_words(report.family_level(3))
        return k <= hi, (
            f"3 identical candidate arms judged at {level} (Bonferroni): at least one adopted in {k}/{family_trials} trials; "
            f"allowed ≤ {hi} (family-wide one-sided rate ~2.5%)"
        )

    out.append(_sub(C2, "3 arms", family))
    return out


def check_aa_real(rows: list[report.Row], arms: tuple[str, str]) -> list[Result]:
    present = {r.arm for r in rows}
    a, b = arms
    if a not in present or b not in present:
        return [Result(C2, SKIP, f"no A/A pair ({a}, {b}) in this table (arms: {', '.join(sorted(present))}); write one with "
                                 "tools/run_aa_replay.py, then rerun --real on it")]

    def real():
        sub = [r for r in rows if r.arm in arms]
        an = _analyse(sub, a, decision_arms=[b], rule=CI_ONLY_RULE)
        c = an.comparisons[b][an.metric]
        verdict = an.decisions[b].verdict
        fa, fb = an.arms[a].forecasts(), an.arms[b].forecasts()
        gaps = [abs(fa[q] - fb[q]) for q in fa if q in fb and fa[q] is not None and fb[q] is not None]
        ok = c.ci_low <= 0.0 <= c.ci_high and verdict != "adopt"
        r = Result(
            C2, PASS if ok else FAIL,
            f"real A/A {b} vs {a}: N {c.n} ({c.n_clusters} clusters), log Δ {report._f(c.mean_diff)}, 95% CI "
            f"[{report._f(c.ci_low)}, {report._f(c.ci_high)}], rule says {verdict}",
            [
                f"mean |p({b}) − p({a})| per question {np.mean(gaps):.3f} (replay noise between identical runs)" if gaps else "",
                f"member failure rate {a} {an.summaries[a].failure_rate:.1%}, {b} {an.summaries[b].failure_rate:.1%}",
                "one real A/A fails about 1 time in 20 by chance: before calling the harness broken, rerun on other questions",
            ],
        )
        r.details = [d for d in r.details if d]
        return r

    return [_sub(C2, "real A/A", real)]


# --------------------------------------------------------------------------- 3. leakage injection
C3 = "3 leakage"
_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_YEAR = re.compile(r"\b(19\d\d|20\d\d)\b")


def dates_after(text: str, as_of: datetime) -> list[str]:
    """ISO dates and bare years in `text` that are later than as_of (protocol T5)."""
    late = [m.group(0) for m in _DATE.finditer(text) if datetime(*map(int, m.groups())).date() > as_of.date()]
    late += [m.group(0) for m in _YEAR.finditer(text) if int(m.group(0)) > as_of.year]
    return late


def _planted(as_of: datetime) -> ResearchItem:
    return ResearchItem(source="synthetic", text="PLANTED future text.", title="PLANTED-FUTURE",
                        url="https://news.example/planted", published_at=as_of + timedelta(hours=1))


def check_leakage(tmp: Path) -> list[Result]:
    from .asknews_archive import ArchiveConfig, archive_search
    from .asknews_budget import AskNewsBudget, BudgetConfig
    from .asof import LeakageError, assert_bundle_as_of
    from .leakage_screen import screen_prompt
    from .research_cache import ResearchCache, content_hash

    as_of = FIXTURE_AS_OF
    out: list[Result] = []

    def bundle_guard():
        clean = fixture_bundle(as_of)
        clean.items = [i for i in clean.items if i.published_at]  # the backtest guard refuses undated items too
        assert_bundle_as_of(clean)
        leaky = fixture_bundle(as_of)
        leaky.items.append(_planted(as_of))
        try:
            assert_bundle_as_of(leaky)
        except LeakageError:
            return True, "assert_bundle_as_of refuses a bundle with an item dated 1 h after as_of (clean bundle passes)"
        return False, "assert_bundle_as_of let a planted future item through"

    out.append(_sub(C3, "bundle guard", bundle_guard))

    def archive():
        def art(published, title):
            return SimpleNamespace(summary="Synthetic summary.", article_url="https://news.example/x", eng_title=title,
                                   pub_date=published)

        class Archive:
            async def search_news(self, **params):
                return SimpleNamespace(as_dicts=[art(as_of - timedelta(days=1), "Synthetic ok"),
                                                 art(as_of + timedelta(days=1), "PLANTED-FUTURE")])

        budget = AskNewsBudget(FixedClock(as_of), BudgetConfig(), ledger_path=tmp / "asknews-ledger.jsonl", runs_dir=None)
        bundle = asyncio.run(archive_search(fixture_question(), FixedClock(as_of), ArchiveConfig(min_interval_s=0), Archive(),
                                            budget))
        blob = json.dumps(bundle.to_dict())
        ok = [i.title for i in bundle.items] == ["Synthetic ok"] and "PLANTED" not in blob
        return ok, "AskNews archive search drops an article dated after as_of and keeps no trace of its title"

    out.append(_sub(C3, "archive date filter", archive))

    def cache():
        c = ResearchCache(tmp / "cache")
        good = fixture_bundle(as_of)
        good.items = [i for i in good.items if i.published_at]
        leaky = fixture_bundle(as_of)
        leaky.items = [_planted(as_of)]
        try:
            c.put(1, as_of, "h", "Q?", leaky)
            return False, "the cache stored a bundle with a future item"
        except LeakageError:
            pass
        key = c.put(1, as_of, "h", "Q?", good)
        path = c.path(key)
        entry = json.loads(path.read_text(encoding="utf-8"))
        entry["bundle"]["items"][0]["published_at"] = (as_of + timedelta(days=1)).isoformat()
        entry["content_sha256"] = content_hash(entry["bundle"])  # a consistent hash: only the date recheck can catch it
        path.write_text(json.dumps(entry), encoding="utf-8")
        try:
            c.get(1, as_of, "h", "Q?")
        except LeakageError:
            return True, "research cache refuses to store a future item and re-checks dates on read (re-dated entry caught)"
        return False, "the cache served an entry re-dated after as_of"

    out.append(_sub(C3, "cache recheck", cache))

    def replay_guard():
        rec = synthetic_record("binary")
        leaky = json.loads(json.dumps(rec))
        leaky["research"]["items"].append(
            {"source": "synthetic", "text": "PLANTED future text.", "url": None, "title": "PLANTED-FUTURE",
             "published_at": (as_of + timedelta(hours=1)).isoformat()}
        )
        late_bundle = json.loads(json.dumps(rec))
        late_bundle["research"]["as_of"] = (as_of + timedelta(days=1)).isoformat()
        for bad in (leaky, late_bundle):
            llm = FakeLlm()
            try:
                asyncio.run(replay.replay_record(bad, DEFAULT_CONFIG, llm))
                return False, f"replay_record ran a record with research after as_of ({llm.calls} model call(s))"
            except LeakageError:
                if llm.calls:
                    return False, f"replay_record refused only after {llm.calls} model call(s)"
        llm = FakeLlm()
        asyncio.run(replay.replay_record(rec, DEFAULT_CONFIG, llm))
        ok = llm.calls >= len(DEFAULT_CONFIG.forecasters)
        return ok, ("replay refuses a record with an item dated after its as_of, or a bundle made after it, before any model "
                    "call (an undated item is allowed; the clean record replays)")

    out.append(_sub(C3, "replay guard", replay_guard))

    def prompts():
        bad: list[str] = []
        n = 0
        for qtype in ("binary", "multiple_choice", "numeric"):
            llm = FakeLlm()
            try:
                asyncio.run(replay.replay_record(synthetic_record(qtype), DEFAULT_CONFIG, llm))
            except ForecastFailed:
                pass
            today = f"Today is {as_of:%Y-%m-%d}"
            for p in llm.prompts:
                n += 1
                late = dates_after(p, as_of)
                if late:
                    bad.append(f"{qtype} prompt has later date(s) {sorted(set(late))[:5]}")
            if not any(today in p for p in llm.prompts):
                bad.append(f"{qtype}: no '{today}' in the forecast prompt (is the clock wired through?)")
        sp = screen_prompt(fixture_question(), fixture_bundle(as_of))
        n += 1
        if dates_after(sp, as_of):
            bad.append(f"leakage-screen prompt has later date(s) {dates_after(sp, as_of)[:5]}")
        r = Result(C3, PASS if not bad else FAIL,
                   f"T5 prompts: {n} prompts rendered through the replay path for an as-of fixture ({as_of:%Y-%m-%d}; binary, "
                   "multiple choice, numeric, polarity check, leakage screen) contain no date or year after as_of", bad)
        return r

    out.append(_sub(C3, "T5 prompts", prompts))
    return out


# --------------------------------------------------------------------------- 4. trivial baselines
C4 = "4 trivial baselines"


def truth_table(n: int = 400, per_cluster: int = 4, seed: int = 4) -> tuple[list[report.Row], np.ndarray, np.ndarray]:
    """Synthetic questions with a known true probability q: arms _truth (p = q), _always50 and _baserate (in-sample)."""
    rng = np.random.default_rng(seed)
    q = np.clip(1.0 / (1.0 + np.exp(-rng.normal(-0.8, 1.5, n))), 0.02, 0.98)  # base rate ≈ 0.35: unlike 50%
    y = (rng.random(n) < q).astype(int)
    base = float(y.mean())
    rows = []
    for i in range(n):
        for arm, p in ((TRUTH, float(q[i])), (ALWAYS_50, 0.5), (BASE_RATE, base)):
            rows.append(report.Row(question_id=f"T{i:04d}", cluster_id=f"TC{i // per_cluster:03d}", arm=arm,
                                   member=report.AGGREGATE, repeat=1, p=p, outcome=int(y[i]), question_type="binary"))
    return rows, q, y


def _exact_trivial(outcomes: Sequence[int]) -> list[tuple[bool, str]]:
    """Scorer-level facts about the two trivial forecasters on these outcomes (through `evals.scoring`, looked up at call time)."""
    ys = list(outcomes)
    b = sum(ys) / len(ys)
    bs50 = [scoring.binary_baseline_score(0.5, o) for o in ys]
    brier_b = float(np.mean([scoring.binary_brier(b, o) for o in ys]))
    log_b = float(np.mean([scoring.binary_log_score(b, o) for o in ys]))
    ent = b * math.log(b) + (1 - b) * math.log(1 - b) if 0 < b < 1 else 0.0
    bsb = float(np.mean([scoring.binary_baseline_score(b, o) for o in ys]))
    bsb_expect = 100.0 * (1.0 + (b * math.log2(b) + (1 - b) * math.log2(1 - b))) if 0 < b < 1 else 100.0
    return [
        (all(s == 0.0 for s in bs50), f"always-50% Metaculus baseline score is exactly 0 on all {len(ys)} questions"),
        (abs(float(np.mean([scoring.binary_brier(0.5, o) for o in ys])) - 0.25) < EXACT_TOL, "always-50% Brier is 0.25"),
        (abs(brier_b - b * (1 - b)) < EXACT_TOL, f"base-rate Brier {brier_b:.6f} = base·(1−base) = {b * (1 - b):.6f} (base {b:.4f})"),
        (abs(log_b - ent) < EXACT_TOL, f"base-rate log score {log_b:.6f} = b·ln b + (1−b)·ln(1−b)"),
        (abs(bsb - bsb_expect) < 1e-9, f"base-rate baseline score {bsb:.4f} = 100·(1 − H₂(base))"),
    ]


def check_trivial_offline(reps: int = 2000) -> list[Result]:
    out: list[Result] = []
    rows, q, y = truth_table()

    def scorer():
        facts = _exact_trivial(y)
        truth_bs = float(np.mean([scoring.binary_baseline_score(float(p), int(o)) for p, o in zip(q, y)]))
        facts.append((truth_bs > 0, f"the true-probability forecaster's mean baseline score {truth_bs:.1f} > 0"))
        bad = [w for ok, w in facts if not ok]
        return Result(C4, PASS if not bad else FAIL,
                      "scorer: " + ("; ".join(w for _, w in facts[:3]) if not bad else f"{len(bad)} fact(s) wrong"),
                      [f"WRONG: {w}" for w in bad])

    out.append(_sub(C4, "scorer", scorer))

    def via_report():
        an = _analyse(rows, ALWAYS_50, reps=reps)
        s50, sb = an.summaries[ALWAYS_50], an.summaries[BASE_RATE]
        b = float(y.mean())
        checks = [
            (s50.mean_baseline_score == 0.0,
             f"report: always-50% baseline score {s50.mean_baseline_score!r} (must be exactly 0)"),
            (abs(sb.mean_brier - b * (1 - b)) < EXACT_TOL,
             f"report: base-rate Brier {sb.mean_brier:.6f} vs base·(1−base) {b * (1 - b):.6f}"),
        ]
        bad = [w for ok, w in checks if not ok]
        return Result(C4, PASS if not bad else FAIL, "report: always-50% baseline score exactly 0, base-rate Brier = base·(1−base)",
                      [f"WRONG: {w}" for w in bad])

    out.append(_sub(C4, "report", via_report))

    def truth_wins():
        lines, bad = [], []
        for base_arm in (ALWAYS_50, BASE_RATE):
            an = _analyse(rows, base_arm, reps=reps, decision_arms=[TRUTH])
            for key in ("log", "brier"):
                c = an.comparisons[TRUTH][key]
                lo, hi = c.improvement_ci
                words = f"truth vs {base_arm} ({key}): improvement {report._f(c.improvement)} CI [{report._f(lo)}, {report._f(hi)}]"
                (lines if lo > 0 else bad).append(words)
        return Result(C4, PASS if not bad else FAIL,
                      f"truth arm beats always-50% and the base rate on log and Brier, CI excluding 0 ({len(lines)}/4)",
                      [f"NOT BETTER: {w}" for w in bad] + lines)

    out.append(_sub(C4, "truth beats trivial", truth_wins))
    return out


def check_trivial_real(rows: list[report.Row]) -> list[Result]:
    out: list[Result] = []
    prep = report.prepare(rows)
    if not prep.outcomes:
        return [Result(C4, SKIP, "no resolved binary questions in the table")]
    triv, base = trivial_rows(prep)

    def exact():
        facts = _exact_trivial([prep.outcomes[q] for q in sorted(prep.outcomes)])
        an = _analyse(triv, ALWAYS_50, reps=200)
        facts.append((an.summaries[ALWAYS_50].mean_baseline_score == 0.0, "report: always-50% baseline score exactly 0"))
        facts.append((abs(an.summaries[BASE_RATE].mean_brier - base * (1 - base)) < EXACT_TOL, "report: base-rate Brier = b(1−b)"))
        bad = [w for ok, w in facts if not ok]
        return Result(C4, PASS if not bad else FAIL,
                      f"on the table's {len(prep.outcomes)} resolved binary questions (base rate {base:.3f}): always-50% scores "
                      "exactly 0, base-rate Brier = base·(1−base)", [f"WRONG: {w}" for w in bad])

    out.append(_sub(C4, "real outcomes", exact))

    def sanity():
        arms = sorted(a for a in prep.arms)
        details = []
        an50 = _analyse(rows + triv, ALWAYS_50)
        anb = _analyse(rows + triv, BASE_RATE)
        s50, sb = an50.summaries[ALWAYS_50], an50.summaries[BASE_RATE]
        details.append(f"always-50%: log {report._f(s50.mean_log)}, Brier {report._f(s50.mean_brier)}, baseline score 0.0")
        details.append(f"base rate {base:.3f}: log {report._f(sb.mean_log)}, Brier {report._f(sb.mean_brier)}, "
                       f"baseline score {report._f(sb.mean_baseline_score, 1)}")
        for a in arms:
            s = an50.summaries[a]
            c50, cb = an50.comparisons[a]["log"], anb.comparisons[a]["log"]
            details.append(
                f"arm {a} (N {s.n}): log {report._f(s.mean_log)}, Brier {report._f(s.mean_brier)}, baseline score "
                f"{report._f(s.mean_baseline_score, 1)}; log Δ vs always-50% {report._f(c50.mean_diff)} "
                f"[{report._f(c50.ci_low)}, {report._f(c50.ci_high)}], vs base rate {report._f(cb.mean_diff)} "
                f"[{report._f(cb.ci_low)}, {report._f(cb.ci_high)}]"
            )
        return Result(C4, INFO, "where the table's forecasts sit relative to the trivial baselines (a sanity line, not a verdict)",
                      details)

    out.append(_sub(C4, "sanity", sanity))
    return out


# --------------------------------------------------------------------------- run
def _safe(check: str, fn: Callable[..., list[Result]], *args: Any) -> list[Result]:
    """A check that crashes before its sub-checks run is one FAIL, not a traceback that hides the other checks."""
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001
        return [Result(check, FAIL, f"crashed with {type(e).__name__}: {str(e)[:300]}")]


def run_checks(mode: str = "offline", real_path: Path | None = None, *, baseline: str | None = None,
               aa_arms: tuple[str, str] = ("A", "A2"), runs_dir: Path = replay.RUNS, aa_trials_n: int = AA_TRIALS,
               aa_family_n: int = AA_FAMILY_TRIALS) -> list[Result]:
    results: list[Result] = []
    with tempfile.TemporaryDirectory(prefix="b32-") as d:
        tmp = Path(d)
        if mode == "offline":
            results += _safe(C1, check_reproducibility_offline, tmp)
            results += _safe(C2, check_aa_offline, aa_trials_n, aa_family_n)
            results += _safe(C3, check_leakage, tmp)
            results += _safe(C4, check_trivial_offline)
        elif mode == "real":
            if real_path is None:
                raise ValueError("--real needs the path of a long table")
            rows = report.read_table(real_path)
            prep = report.prepare(rows)
            if not prep.outcomes:
                raise ValueError(f"{real_path}: no resolved binary questions to score")
            extra = trivial_rows(prep, (ALWAYS_50,))[0]
            results += _safe(C1, _repro_on_file, real_path, baseline or ALWAYS_50, lambda _rows: list(extra), tmp, None,
                             report.DEFAULT_REPS, report.DEFAULT_SEED)
            archive = _safe(C1, check_replay_archive, runs_dir)
            results += [r for r in archive if r.check == C1]
            results += _safe(C2, check_aa_real, rows, aa_arms)
            results += _safe(C3, check_leakage, tmp) + [r for r in archive if r.check != C1]
            results += _safe(C4, check_trivial_real, rows)
        else:
            raise ValueError(f"unknown mode {mode!r}")
    return results


def render(results: Sequence[Result], mode: str) -> tuple[str, int]:
    what = "offline: synthetic data only, no network, no keys" if mode == "offline" else "real table"
    lines = [f"B-32 harness validation ({what})", ""]
    for r in results:
        lines.append(f"{r.status:4s}  {r.check}: {r.headline}")
        lines += [f"        {d}" for d in r.details]
    n = {s: sum(r.status == s for r in results) for s in (PASS, FAIL, SKIP, INFO)}
    code = 1 if n[FAIL] else 2 if n[SKIP] else 0
    verdict = {0: "HARNESS VALIDATED", 1: "HARNESS NOT VALIDATED: fix every FAIL before trusting a result",
               2: "no failures, but some checks were skipped (see SKIP lines)"}[code]
    lines += ["", f"Summary: {n[PASS]} passed, {n[FAIL]} failed, {n[SKIP]} skipped, {n[INFO]} info. {verdict}."]
    return "\n".join(lines) + "\n", code


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m evals.validate_harness", description="Harness validation (B-32, protocol §7).")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--offline", action="store_true", help="synthetic data only (default)")
    g.add_argument("--real", metavar="RUNS_CSV", help="a real long table in evals.report's input format")
    ap.add_argument("--baseline", help="--real: baseline arm for the reproducibility check (default: an added always-50%% arm)")
    ap.add_argument("--aa-arms", nargs=2, default=("A", "A2"), metavar=("ARM", "ARM2"), help="--real: the A/A pair of arms")
    ap.add_argument("--runs-dir", default=str(replay.RUNS), help="--real: archived live records for the replay hooks")
    args = ap.parse_args(argv)
    mode = "real" if args.real else "offline"
    results = run_checks(mode, Path(args.real) if args.real else None, baseline=args.baseline, aa_arms=tuple(args.aa_arms),
                         runs_dir=Path(args.runs_dir))
    text, code = render(results, mode)
    sys.stdout.write(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
