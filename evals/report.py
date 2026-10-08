"""Experiment report generator (B-31; evaluation protocol §5-§7, threats T9, T12).

Reads a long table of forecasts, scores it with `evals/scoring.py` and writes a Markdown report:

    poetry run python -m evals.report runs.csv --baseline A --name EXP-005 --rule exp005
    poetry run python -m evals.report runs.csv --baseline A --name EXP-005 --rule exp005 --mde 0.04 \\
        --card docs/experiments/EXP-005-graph-first-reasoning.md     # also fills the card's "Results (generated)" section

Input (CSV, or JSONL with one object per line), one row per forecast:
    question_id, cluster_id, arm, member, repeat, p, outcome
- `member` is a model name, or `aggregate` for the arm's combined forecast. Arms without aggregate rows are aggregated here with
  the live rule (median of the members that produced a number, per repeat); `--aggregation median` forces that for every arm.
- `p` is P(YES). Blank `p`, or a truthy `failed` column, is a failed forecast (parse/validation); an optional `error` column names
  the kind. `outcome` is 1/0; a blank outcome means unresolved or annulled, and the question is dropped and counted.
- Optional: `question_type` (non-binary questions are dropped and counted), `source` (`benchmark-forward` marks the EXP-005
  forward set), `cost_usd`, `tokens` (or `prompt_tokens` + `completion_tokens`), and any other column to break results down by
  (`--by domain`).

Method (protocol §5): the k repeats of a question are averaged into one forecast *before* scoring (what we would submit). Each
arm is compared with the baseline arm on the questions both have, as a per-question paired difference. The 95% CI is a
percentile cluster bootstrap (resample `cluster_id`, 10,000 reps, fixed seed). MDE ≈ 2.8·SD(diff)/√N. Calibration is the Brier
reliability term of the Murphy decomposition over 10 bins. `DecisionRule` + `apply_decision_rule` turn a pre-registered rule
into code; the report never calls a difference a win unless its CI excludes 0 (T9).

The report is deterministic: the same input, seed and generator give the same bytes (no timestamps). Its header records the git
commit, the sha256 of the input and of this file, the seed, N per arm and every dropped question with its reason.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

import numpy as np

from . import scoring

REPORT_VERSION = "1.0.0"
DEFAULT_SEED = 3101
DEFAULT_REPS = 10_000
DEFAULT_BINS = 10
AGGREGATE = "aggregate"
FORWARD_SOURCE = "benchmark-forward"
REQUIRED_COLUMNS = ("question_id", "cluster_id", "arm", "member", "repeat", "p", "outcome")
FEW_CLUSTERS = 20  # below this the percentile bootstrap is known to give too-narrow intervals
ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS = ROOT / "docs" / "experiments"

DROP_NO_OUTCOME = "no outcome (unresolved or annulled)"
DROP_NOT_BINARY = "not binary (only binary questions are scored)"


# --------------------------------------------------------------------------- metrics
@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    higher_is_better: bool
    score: Callable[[float, int], float]


METRICS = {
    "log": Metric("log", "log score", True, scoring.binary_log_score),
    "brier": Metric("brier", "Brier", False, scoring.binary_brier),
}


# --------------------------------------------------------------------------- input
@dataclass
class Row:
    question_id: str
    cluster_id: str
    arm: str
    member: str
    repeat: int
    p: float | None  # None = failed forecast
    outcome: int | None  # None = unresolved / annulled
    failed: bool = False
    error: str = ""
    question_type: str = ""
    source: str = ""
    cost_usd: float | None = None
    tokens: float | None = None
    extra: dict[str, str] = field(default_factory=dict)


def _blank(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    return isinstance(v, str) and v.strip().lower() in ("", "nan", "none", "null", "na")


def _truthy(v: Any) -> bool:
    if _blank(v):
        return False
    if isinstance(v, (bool, int, float)):
        return bool(v)
    return str(v).strip().lower() in ("1", "true", "yes", "y", "t")


def _num(v: Any) -> float | None:
    return None if _blank(v) else float(v)


def _outcome(v: Any) -> int | None:
    if _blank(v):
        return None
    s = str(v).strip().lower()
    if s in ("1", "1.0", "yes", "true"):
        return 1
    if s in ("0", "0.0", "no", "false"):
        return 0
    raise ValueError(f"outcome must be 1/0 or blank, got {v!r}")


_KNOWN = {f.name for f in fields(Row)} | {"prompt_tokens", "completion_tokens"}


def parse_row(d: dict[str, Any], line: int = 0) -> Row:
    missing = [c for c in REQUIRED_COLUMNS if c not in d]
    if missing:
        raise ValueError(f"row {line}: missing column(s) {missing}")
    try:
        p = _num(d["p"])
        if p is not None and not 0.0 <= p <= 1.0:
            raise ValueError(f"p out of [0, 1]: {p}")
        failed = _truthy(d.get("failed")) or p is None
        tokens = _num(d.get("tokens"))
        if tokens is None and not (_blank(d.get("prompt_tokens")) and _blank(d.get("completion_tokens"))):
            tokens = (_num(d.get("prompt_tokens")) or 0.0) + (_num(d.get("completion_tokens")) or 0.0)
        return Row(
            question_id=str(d["question_id"]).strip(),
            cluster_id="" if _blank(d["cluster_id"]) else str(d["cluster_id"]).strip(),
            arm=str(d["arm"]).strip(),
            member=str(d["member"]).strip(),
            repeat=int(float(d["repeat"])),
            p=None if failed else p,
            outcome=_outcome(d["outcome"]),
            failed=failed,
            error="" if _blank(d.get("error")) else str(d["error"]).strip(),
            question_type="" if _blank(d.get("question_type")) else str(d["question_type"]).strip().lower(),
            source="" if _blank(d.get("source")) else str(d["source"]).strip(),
            cost_usd=_num(d.get("cost_usd")),
            tokens=tokens,
            extra={k: "" if _blank(v) else str(v) for k, v in d.items() if k not in _KNOWN},
        )
    except (TypeError, ValueError) as e:
        raise ValueError(f"row {line}: {e}") from e


def read_table(path: str | Path) -> list[Row]:
    """CSV (`.csv`) or JSON lines (anything else)."""
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".csv":
        dicts: Iterable[dict[str, Any]] = csv.DictReader(text.splitlines())
    else:
        dicts = (json.loads(line) for line in text.splitlines() if line.strip())
    return [parse_row(d, i + 1) for i, d in enumerate(dicts)]


def sha256_file(path: str | Path, normalise_newlines: bool = False) -> str:
    data = Path(path).read_bytes()
    if normalise_newlines:  # git may check this file out with CRLF on Windows; hash the same text the same way everywhere
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- aggregation, repeats
@dataclass
class ArmData:
    """One arm after aggregation. Per question: a dict repeat -> forecast (None = failed)."""

    name: str
    aggregate: dict[str, dict[int, float | None]] = field(default_factory=dict)
    members: dict[str, dict[str, dict[int, float | None]]] = field(default_factory=dict)
    member_rows: int = 0
    member_failures: int = 0
    aggregate_source: Counter = field(default_factory=Counter)  # "given" / "median of members"
    errors: Counter = field(default_factory=Counter)
    cost_by_question: dict[str, float] = field(default_factory=dict)
    tokens_by_question: dict[str, float] = field(default_factory=dict)
    repeats_by_question: dict[str, int] = field(default_factory=dict)

    def _source(self, member: str) -> dict[str, dict[int, float | None]]:
        return self.aggregate if member == AGGREGATE else self.members.get(member, {})

    def forecasts(self, member: str = AGGREGATE) -> dict[str, float | None]:
        """Question -> mean of the successful repeats (None if every repeat failed). Averaging comes before scoring."""
        out: dict[str, float | None] = {}
        for qid, reps in self._source(member).items():
            ok = [p for p in reps.values() if p is not None]
            out[qid] = float(np.mean(ok)) if ok else None
        return out

    def repeat_values(self, member: str = AGGREGATE) -> dict[str, list[float]]:
        return {qid: [p for p in reps.values() if p is not None] for qid, reps in self._source(member).items()}

    @property
    def aggregate_attempts(self) -> int:
        return sum(len(r) for r in self.aggregate.values())

    @property
    def aggregate_failures(self) -> int:
        return sum(1 for r in self.aggregate.values() for p in r.values() if p is None)

    @property
    def failure_rate(self) -> float:
        """Share of member forecasts that failed (parse/validation); falls back to aggregate failures without member rows."""
        if self.member_rows:
            return self.member_failures / self.member_rows
        return self.aggregate_failures / self.aggregate_attempts if self.aggregate_attempts else float("nan")


@dataclass
class Prepared:
    arms: dict[str, ArmData]
    outcomes: dict[str, int]
    clusters: dict[str, str]
    attrs: dict[str, dict[str, str]]  # question -> {question_type, source, extra columns}
    dropped: dict[str, str]  # question -> reason (dropped before any arm is scored)
    notes: list[str]


def _median(values: list[float]) -> float:
    return float(statistics.median(values))  # same rule as forecast_engine.parsing.median_binary (live)


def prepare(rows: Sequence[Row], aggregation: str = "auto") -> Prepared:
    """Validate, drop unscoreable questions, aggregate members per repeat.

    aggregation: "auto" uses an arm's `aggregate` rows where present and the median of members otherwise; "median" always
    recomputes the median of members (ignores given aggregate rows); "given" uses only the given aggregate rows.
    """
    if aggregation not in ("auto", "median", "given"):
        raise ValueError(f"unknown aggregation {aggregation!r}")
    notes: list[str] = []
    outcomes: dict[str, int | None] = {}
    clusters: dict[str, str] = {}
    attrs: dict[str, dict[str, str]] = defaultdict(dict)
    by_q: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        by_q[r.question_id].append(r)
    no_cluster = 0
    for qid, rs in by_q.items():
        outs = {r.outcome for r in rs if r.outcome is not None}
        if len(outs) > 1:
            raise ValueError(f"question {qid}: conflicting outcomes {sorted(outs)}")
        outcomes[qid] = outs.pop() if outs else None
        cl = {r.cluster_id for r in rs if r.cluster_id}
        if len(cl) > 1:
            raise ValueError(f"question {qid}: in more than one cluster {sorted(cl)}")
        if cl:
            clusters[qid] = cl.pop()
        else:
            clusters[qid] = f"q:{qid}"
            no_cluster += 1
        for key in ("question_type", "source"):
            vals = {getattr(r, key) for r in rs if getattr(r, key)}
            if len(vals) > 1:
                raise ValueError(f"question {qid}: conflicting {key} {sorted(vals)}")
            attrs[qid][key] = vals.pop() if vals else ""
        for r in rs:
            for k, v in r.extra.items():
                if v:
                    attrs[qid].setdefault(k, v)
    if no_cluster:
        notes.append(f"{no_cluster} question(s) had no cluster_id; each was treated as its own cluster.")

    dropped: dict[str, str] = {}
    for qid in by_q:
        qtype = attrs[qid].get("question_type", "")
        if qtype and qtype != "binary":
            dropped[qid] = DROP_NOT_BINARY
        elif outcomes[qid] is None:
            dropped[qid] = DROP_NO_OUTCOME

    arms: dict[str, ArmData] = {}
    given: dict[tuple[str, str], dict[int, float | None]] = defaultdict(dict)
    for r in rows:
        if r.question_id in dropped:
            continue
        a = arms.setdefault(r.arm, ArmData(r.arm))
        if r.cost_usd is not None:
            a.cost_by_question[r.question_id] = a.cost_by_question.get(r.question_id, 0.0) + r.cost_usd
        if r.tokens is not None:
            a.tokens_by_question[r.question_id] = a.tokens_by_question.get(r.question_id, 0.0) + r.tokens
        if r.member == AGGREGATE:
            key = (r.arm, r.question_id)
            if r.repeat in given[key]:
                raise ValueError(f"duplicate aggregate row: arm {r.arm}, question {r.question_id}, repeat {r.repeat}")
            given[key][r.repeat] = r.p
            continue
        reps = a.members.setdefault(r.member, {}).setdefault(r.question_id, {})
        if r.repeat in reps:
            raise ValueError(f"duplicate row: arm {r.arm}, member {r.member}, question {r.question_id}, repeat {r.repeat}")
        reps[r.repeat] = r.p
        a.member_rows += 1
        if r.failed:
            a.member_failures += 1
            a.errors[r.error or "failed (no kind given)"] += 1

    for a in arms.values():
        qids = {q for m in a.members.values() for q in m} | {q for (arm, q) in given if arm == a.name}
        for qid in qids:
            from_members: dict[int, list[float | None]] = defaultdict(list)
            for m in a.members.values():
                for rep, p in m.get(qid, {}).items():
                    from_members[rep].append(p)
            g = given.get((a.name, qid), {})
            out: dict[int, float | None] = {}
            for rep in sorted(set(from_members) | set(g)):
                if aggregation != "median" and rep in g:
                    out[rep] = g[rep]
                    a.aggregate_source["given"] += 1
                elif aggregation != "given" and rep in from_members:
                    ok = [p for p in from_members[rep] if p is not None]
                    out[rep] = _median(ok) if ok else None
                    a.aggregate_source["median of members"] += 1
            if out:
                a.aggregate[qid] = out
                a.repeats_by_question[qid] = len(out)

    scored_outcomes = {q: o for q, o in outcomes.items() if q not in dropped and o is not None}
    return Prepared(arms, scored_outcomes, clusters, dict(attrs), dropped, notes)


# --------------------------------------------------------------------------- statistics
def bootstrap_means(diffs: Sequence[float], clusters: Sequence[str], reps: int, seed: int, chunk: int = 2000) -> np.ndarray:
    """Cluster bootstrap of the mean: each replicate draws whole clusters with replacement, so questions in the same
    cluster always enter (or leave) together. Returns `reps` replicate means (total diff / total questions drawn)."""
    d = np.asarray(diffs, dtype=float)
    if d.size == 0:
        return np.array([])
    _, inv = np.unique(np.asarray(clusters, dtype=str), return_inverse=True)
    sums = np.bincount(inv, weights=d)
    counts = np.bincount(inv).astype(float)
    c = sums.size
    rng = np.random.default_rng(seed)
    out = np.empty(reps)
    for start in range(0, reps, chunk):
        m = min(chunk, reps - start)
        idx = rng.integers(0, c, size=(m, c))
        out[start : start + m] = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
    return out


def cluster_bootstrap_ci(
    diffs: Sequence[float], clusters: Sequence[str], reps: int = DEFAULT_REPS, seed: int = DEFAULT_SEED, level: float = 0.95
) -> tuple[float, float, float]:
    """Percentile CI of the mean difference and the bootstrap standard error: (low, high, se)."""
    means = bootstrap_means(diffs, clusters, reps, seed)
    if means.size == 0:
        return float("nan"), float("nan"), float("nan")
    alpha = (1.0 - level) / 2.0
    lo, hi = np.quantile(means, [alpha, 1.0 - alpha])
    return float(lo), float(hi), float(np.std(means, ddof=1))


def mde(sd_diff: float, n: int) -> float:
    """Minimum detectable effect, protocol §5: 2.8·SD/√N (α = 0.05 two-sided, 80% power)."""
    return 2.8 * sd_diff / math.sqrt(n) if n > 0 and not math.isnan(sd_diff) else float("nan")


@dataclass
class Comparison:
    arm: str
    baseline: str
    metric: str
    higher_is_better: bool
    n: int
    n_clusters: int
    mean_arm: float
    mean_base: float
    mean_diff: float  # arm − baseline, in the metric's own units
    ci_low: float
    ci_high: float
    se_boot: float
    median_diff: float
    wins: int
    ties: int
    losses: int
    sd_diff: float
    mde: float
    dropped: Counter = field(default_factory=Counter)

    @property
    def win_rate(self) -> float:
        """Share of questions where the arm scored better than the baseline (ties count half)."""
        return (self.wins + 0.5 * self.ties) / self.n if self.n else float("nan")

    @property
    def improvement(self) -> float:
        """The mean difference oriented so that positive = the arm is better."""
        return self.mean_diff if self.higher_is_better else -self.mean_diff

    @property
    def improvement_ci(self) -> tuple[float, float]:
        return (self.ci_low, self.ci_high) if self.higher_is_better else (-self.ci_high, -self.ci_low)

    @property
    def ci_excludes_zero(self) -> bool:
        return self.n > 0 and (self.ci_low > 0 or self.ci_high < 0)

    def verdict_words(self) -> str:
        """T9: only call it a difference when the CI excludes 0."""
        if self.n == 0:
            return "no paired questions"
        if not self.ci_excludes_zero:
            return "no detectable difference (CI includes 0)"
        return "arm better (CI excludes 0)" if self.improvement > 0 else "arm worse (CI excludes 0)"


_ABSENT = object()


def paired_compare(
    arm_fc: dict[str, float | None],
    base_fc: dict[str, float | None],
    outcomes: dict[str, int],
    clusters: dict[str, str],
    metric: Metric,
    *,
    arm: str,
    baseline: str,
    reps: int = DEFAULT_REPS,
    seed: int = DEFAULT_SEED,
    restrict: set[str] | None = None,
) -> Comparison:
    """Paired comparison on the questions both sides forecast. Questions missing or failed on one side are dropped and
    counted by reason. `restrict` limits the comparison to a subset of questions (breakdowns)."""
    qids = sorted((set(arm_fc) | set(base_fc)) & set(outcomes))
    if restrict is not None:
        qids = [q for q in qids if q in restrict]
    dropped: Counter = Counter()
    paired = []
    for q in qids:
        a, b = arm_fc.get(q, _ABSENT), base_fc.get(q, _ABSENT)
        if a is _ABSENT:
            dropped[f"not run by {arm}"] += 1
        elif b is _ABSENT:
            dropped[f"not run by {baseline}"] += 1
        elif a is None:
            dropped[f"every forecast failed in {arm}"] += 1
        elif b is None:
            dropped[f"every forecast failed in {baseline}"] += 1
        else:
            paired.append(q)
    sa = np.array([metric.score(arm_fc[q], outcomes[q]) for q in paired])
    sb = np.array([metric.score(base_fc[q], outcomes[q]) for q in paired])
    d = sa - sb
    n = len(paired)
    cl = [clusters[q] for q in paired]
    lo, hi, se = cluster_bootstrap_ci(d, cl, reps, seed)
    better = d > 0 if metric.higher_is_better else d < 0
    worse = d < 0 if metric.higher_is_better else d > 0
    sd = float(np.std(d, ddof=1)) if n > 1 else float("nan")
    nan = float("nan")
    return Comparison(
        arm=arm,
        baseline=baseline,
        metric=metric.key,
        higher_is_better=metric.higher_is_better,
        n=n,
        n_clusters=len(set(cl)),
        mean_arm=float(sa.mean()) if n else nan,
        mean_base=float(sb.mean()) if n else nan,
        mean_diff=float(d.mean()) if n else nan,
        ci_low=lo,
        ci_high=hi,
        se_boot=se,
        median_diff=float(np.median(d)) if n else nan,
        wins=int(better.sum()),
        ties=int(n - better.sum() - worse.sum()),
        losses=int(worse.sum()),
        sd_diff=sd,
        mde=mde(sd, n),
        dropped=dropped,
    )


def murphy_decomposition(probs: Sequence[float], outcomes: Sequence[int], n_bins: int = DEFAULT_BINS) -> dict[str, float]:
    """Brier = reliability − resolution + uncertainty (+ a within-bin term that is 0 when every forecast in a bin is equal).

    reliability: mean squared gap between a bin's mean forecast and its observed rate (0 = perfectly calibrated; lower is better).
    resolution: how far the bins' observed rates spread from the overall rate (higher is better).
    uncertainty: base-rate variance o(1−o); the same for every arm on the same questions.
    """
    rows = scoring.calibration_table(probs, outcomes, n_bins)
    n = sum(r["n"] for r in rows)
    obar = float(np.mean(outcomes))
    rel = sum(r["n"] * (r["mean_forecast"] - r["observed_rate"]) ** 2 for r in rows) / n
    res = sum(r["n"] * (r["observed_rate"] - obar) ** 2 for r in rows) / n
    unc = obar * (1.0 - obar)
    brier = float(np.mean([scoring.binary_brier(p, o) for p, o in zip(probs, outcomes)]))
    return {"brier": brier, "reliability": rel, "resolution": res, "uncertainty": unc, "within_bin": brier - (rel - res + unc)}


# --------------------------------------------------------------------------- decision rule
@dataclass(frozen=True)
class DecisionRule:
    """A pre-registered decision rule. Thresholds are in the primary metric's units, oriented so positive = arm better.

    Adopt needs every adopt condition; kill needs any kill condition; neither → inconclusive. A `None` threshold switches its
    condition off. If both adopt and kill hold (a significant effect smaller than the kill threshold), `on_conflict` decides.
    """

    name: str
    metric: str = "log"
    adopt_requires_ci_excluding_zero: bool = True
    max_reliability_increase: float | None = 0.005
    max_failure_rate_increase: float | None = 0.02
    max_cost_per_question: float | None = None
    kill_if_point_at_most: float | None = 0.0
    kill_if_ci_upper_below_mde_fraction: float | None = 0.5
    on_conflict: str = "inconclusive"


EXP005_RULE = DecisionRule(name="EXP-005 confirmation rule (card of 2026-10-07)")
RULES = {"exp005": EXP005_RULE}


@dataclass
class Check:
    kind: str  # "adopt" or "kill"
    condition: str
    met: bool
    detail: str


@dataclass
class Decision:
    arm: str
    rule: DecisionRule
    verdict: str  # "adopt" / "kill" / "inconclusive"
    checks: list[Check]
    mde_used: float
    mde_source: str
    note: str = ""

    def lines(self) -> list[str]:
        out = [f"Decision for arm {self.arm} under '{self.rule.name}': {self.verdict.upper()}"]
        for c in self.checks:
            out.append(f"  [{'x' if c.met else ' '}] {c.kind:5s} {c.condition}: {c.detail}")
        if self.note:
            out.append(f"  note: {self.note}")
        return out


def _f(x: float | None, nd: int = 4) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{nd}f}"


def apply_decision_rule(
    rule: DecisionRule,
    comparison: Comparison,
    *,
    reliability_arm: float,
    reliability_base: float,
    failure_rate_arm: float,
    failure_rate_base: float,
    cost_per_question_arm: float | None = None,
    mde_value: float | None = None,
) -> Decision:
    """Apply `rule` to one arm-vs-baseline comparison. `mde_value` is the pre-registered MDE (EXP-005: stated after the screen);
    without it the MDE measured on this comparison is used and the decision says so. A condition that cannot be evaluated
    (no data) counts as not met."""
    if comparison.metric != rule.metric:
        raise ValueError(f"rule is on {rule.metric!r}, comparison is on {comparison.metric!r}")
    if mde_value is not None:
        m, m_src = float(mde_value), "pre-registered (passed in)"
    else:
        m, m_src = comparison.mde, "measured on this comparison (no pre-registered MDE was given)"
    point = comparison.improvement
    lo, hi = comparison.improvement_ci
    checks: list[Check] = []
    if comparison.n == 0:
        return Decision(comparison.arm, rule, "inconclusive", checks, m, m_src, "no paired questions")

    checks.append(Check("adopt", "mean improvement > 0", point > 0, f"point {_f(point)}"))
    if rule.adopt_requires_ci_excluding_zero:
        checks.append(Check("adopt", "95% CI excludes 0 (lower bound > 0)", lo > 0, f"CI [{_f(lo)}, {_f(hi)}]"))
    if rule.max_reliability_increase is not None:
        inc = reliability_arm - reliability_base
        checks.append(
            Check(
                "adopt",
                f"reliability not worse than baseline by more than {rule.max_reliability_increase:g}",
                not math.isnan(inc) and inc <= rule.max_reliability_increase,
                f"arm {_f(reliability_arm)} vs baseline {_f(reliability_base)} (Δ {_f(inc)})",
            )
        )
    if rule.max_failure_rate_increase is not None:
        inc = failure_rate_arm - failure_rate_base
        checks.append(
            Check(
                "adopt",
                f"failure rate not higher than baseline by more than {rule.max_failure_rate_increase * 100:g} pp",
                not math.isnan(inc) and inc <= rule.max_failure_rate_increase + 1e-12,
                f"arm {failure_rate_arm:.1%} vs baseline {failure_rate_base:.1%} (Δ {inc * 100:+.1f} pp)",
            )
        )
    if rule.max_cost_per_question is not None:
        ok = cost_per_question_arm is not None and cost_per_question_arm <= rule.max_cost_per_question
        checks.append(
            Check("adopt", f"cost per question ≤ ${rule.max_cost_per_question:g}", ok, f"${_f(cost_per_question_arm, 3)}")
        )
    if rule.kill_if_point_at_most is not None:
        checks.append(
            Check(
                "kill", f"mean improvement ≤ {rule.kill_if_point_at_most:g}", point <= rule.kill_if_point_at_most,
                f"point {_f(point)}",
            )
        )
    if rule.kill_if_ci_upper_below_mde_fraction is not None:
        frac = rule.kill_if_ci_upper_below_mde_fraction
        if math.isnan(m):
            checks.append(Check("kill", f"CI upper < {frac:g}·MDE", False, "not evaluated: no MDE"))
        else:
            checks.append(
                Check("kill", f"CI upper < {frac:g}·MDE", hi < frac * m, f"upper {_f(hi)} vs {frac:g}·{_f(m)} = {_f(frac * m)}")
            )

    adopt = all(c.met for c in checks if c.kind == "adopt")
    kill = any(c.met for c in checks if c.kind == "kill")
    note = ""
    if adopt and kill:
        verdict = rule.on_conflict
        note = "adopt and kill conditions both hold (a significant effect below the kill threshold); on_conflict decides"
    elif adopt:
        verdict = "adopt"
    elif kill:
        verdict = "kill"
    else:
        verdict = "inconclusive"
    return Decision(comparison.arm, rule, verdict, checks, m, m_src, note)


# --------------------------------------------------------------------------- analysis
@dataclass
class ArmSummary:
    arm: str
    n: int
    mean_log: float
    mean_baseline_score: float
    mean_brier: float
    murphy: dict[str, float]
    ece: float
    sharpness: float
    calibration: list[dict]
    failure_rate: float
    aggregate_failure_rate: float
    cost_per_question: float | None  # mean over questions of (total cost / repeats): the cost of one live forecast
    tokens_per_question: float | None
    repeat_sd_p: float  # mean over questions of the SD of the aggregate forecast across repeats
    repeat_sd_log: float
    k: str


@dataclass
class Analysis:
    name: str
    baseline: str
    metric: str
    reps: int
    seed: int
    aggregation: str
    prepared: Prepared
    summaries: dict[str, ArmSummary]
    comparisons: dict[str, dict[str, Comparison]]  # arm -> metric -> comparison
    calibration_paired: dict[str, tuple[float, float]]  # arm -> (reliability arm, reliability baseline) on paired questions
    member_rows: list[dict[str, Any]]
    breakdowns: dict[str, list[dict[str, Any]]]  # grouping -> rows
    decisions: dict[str, Decision]
    meta: dict[str, str]
    warnings: list[str]

    @property
    def arms(self) -> dict[str, ArmData]:
        return self.prepared.arms


def _summarise(a: ArmData, outcomes: dict[str, int], n_bins: int) -> ArmSummary:
    fc = {q: p for q, p in a.forecasts().items() if p is not None and q in outcomes}
    qs = sorted(fc)
    probs = [fc[q] for q in qs]
    outs = [outcomes[q] for q in qs]
    nan = float("nan")
    if qs:
        logs = [scoring.binary_log_score(p, o) for p, o in zip(probs, outs)]
        base = [scoring.binary_baseline_score(p, o) for p, o in zip(probs, outs)]
        mur = murphy_decomposition(probs, outs, n_bins)
        ece = scoring.expected_calibration_error(probs, outs, n_bins)
        sharp = scoring.sharpness(probs)
        cal = scoring.calibration_table(probs, outs, n_bins)
    else:
        logs, base, mur, ece, sharp, cal = [], [], {}, nan, nan, []
    rv = {q: v for q, v in a.repeat_values().items() if len(v) > 1 and q in outcomes}
    sds_p = [float(np.std(v, ddof=1)) for v in rv.values()]
    sds_log = [float(np.std([scoring.binary_log_score(p, outcomes[q]) for p in v], ddof=1)) for q, v in rv.items()]
    ks = Counter(a.repeats_by_question.values())
    k = ", ".join(f"{kk}" if len(ks) == 1 else f"{kk} ({c} q)" for kk, c in sorted(ks.items())) or "n/a"
    costs = [c / a.repeats_by_question.get(q, 1) for q, c in a.cost_by_question.items()]
    toks = [t / a.repeats_by_question.get(q, 1) for q, t in a.tokens_by_question.items()]
    return ArmSummary(
        arm=a.name,
        n=len(qs),
        mean_log=float(np.mean(logs)) if logs else nan,
        mean_baseline_score=float(np.mean(base)) if base else nan,
        mean_brier=mur.get("brier", nan),
        murphy=mur,
        ece=ece,
        sharpness=sharp,
        calibration=cal,
        failure_rate=a.failure_rate,
        aggregate_failure_rate=a.aggregate_failures / a.aggregate_attempts if a.aggregate_attempts else nan,
        cost_per_question=float(np.mean(costs)) if costs else None,
        tokens_per_question=float(np.mean(toks)) if toks else None,
        repeat_sd_p=float(np.mean(sds_p)) if sds_p else nan,
        repeat_sd_log=float(np.mean(sds_log)) if sds_log else nan,
        k=k,
    )


def analyse(
    rows: Sequence[Row],
    baseline: str,
    *,
    name: str = "experiment",
    metric: str = "log",
    reps: int = DEFAULT_REPS,
    seed: int = DEFAULT_SEED,
    aggregation: str = "auto",
    rule: DecisionRule | None = None,
    mde_value: float | None = None,
    decision_arms: Sequence[str] | None = None,
    by: Sequence[str] = ("question_type", "source"),
    forward_source: str = FORWARD_SOURCE,
    n_bins: int = DEFAULT_BINS,
    meta: dict[str, str] | None = None,
) -> Analysis:
    if metric not in METRICS:
        raise ValueError(f"unknown metric {metric!r}; choose from {sorted(METRICS)}")
    if rule is not None and rule.metric != metric:
        raise ValueError(f"the rule's primary metric is {rule.metric!r}, the report's is {metric!r}")
    prep = prepare(rows, aggregation)
    if baseline not in prep.arms:
        raise ValueError(f"baseline arm {baseline!r} not in the data (arms: {sorted(prep.arms)})")
    out, cl = prep.outcomes, prep.clusters
    others = [a for a in sorted(prep.arms) if a != baseline]
    warnings: list[str] = list(prep.notes)
    base_fc = prep.arms[baseline].forecasts()

    def compare(arm_fc: dict[str, float | None], other_fc: dict[str, float | None], arm: str, key: str, restrict=None):
        return paired_compare(
            arm_fc, other_fc, out, cl, METRICS[key], arm=arm, baseline=baseline, reps=reps, seed=seed, restrict=restrict
        )

    summaries = {a: _summarise(prep.arms[a], out, n_bins) for a in [baseline, *others]}
    comparisons: dict[str, dict[str, Comparison]] = {}
    calib_paired: dict[str, tuple[float, float]] = {}
    for a in others:
        fc = prep.arms[a].forecasts()
        comparisons[a] = {key: compare(fc, base_fc, a, key) for key in METRICS}
        paired = [q for q in out if fc.get(q) is not None and base_fc.get(q) is not None]
        if paired:
            outs = [out[q] for q in paired]
            rel_a = murphy_decomposition([fc[q] for q in paired], outs, n_bins)["reliability"]
            rel_b = murphy_decomposition([base_fc[q] for q in paired], outs, n_bins)["reliability"]
            calib_paired[a] = (rel_a, rel_b)
        c = comparisons[a][metric]
        if 0 < c.n_clusters < FEW_CLUSTERS:
            warnings.append(
                f"{a} vs {baseline}: only {c.n_clusters} clusters; percentile bootstrap CIs are too narrow with few clusters."
            )

    # each member alone
    member_rows: list[dict[str, Any]] = []
    base_arm = prep.arms[baseline]
    for a in [baseline, *others]:
        arm = prep.arms[a]
        for member in sorted(arm.members):
            fc = arm.forecasts(member)
            qs = [q for q, p in fc.items() if p is not None and q in out]
            all_reps = [p for q in arm.members[member].values() for p in q.values()]
            member_rows.append(
                {
                    "arm": a,
                    "member": member,
                    "n": len(qs),
                    "log": float(np.mean([scoring.binary_log_score(fc[q], out[q]) for q in qs])) if qs else float("nan"),
                    "brier": float(np.mean([scoring.binary_brier(fc[q], out[q]) for q in qs])) if qs else float("nan"),
                    "failure_rate": sum(p is None for p in all_reps) / len(all_reps) if all_reps else float("nan"),
                    "vs_baseline": (
                        compare(fc, base_arm.forecasts(member), a, metric)
                        if a != baseline and member in base_arm.members
                        else None
                    ),
                }
            )

    # breakdowns by question attribute; the forward set is shown next to our own records whenever it is in the data
    groupings: dict[str, dict[str, set[str]]] = {}
    for col in by:
        groups: dict[str, set[str]] = defaultdict(set)
        for q in out:
            groups[prep.attrs.get(q, {}).get(col, "") or "(blank)"].add(q)
        if len(groups) > 1:  # one value only: nothing to break down
            groupings[col] = groups
    if forward_source and any(prep.attrs.get(q, {}).get("source") == forward_source for q in out):
        fwd: dict[str, set[str]] = defaultdict(set)
        for q in out:
            fwd[forward_source if prep.attrs.get(q, {}).get("source") == forward_source else "own records"].add(q)
        groupings[f"forward set ({forward_source}) vs own records"] = fwd
    breakdowns = {
        col: [
            {"group": g, "comparison": compare(prep.arms[a].forecasts(), base_fc, a, metric, groups[g])}
            for g in sorted(groups)
            for a in others
        ]
        for col, groups in groupings.items()
    }

    decisions: dict[str, Decision] = {}
    if rule is not None:
        targets = list(decision_arms) if decision_arms else others
        if len(targets) > 1:
            warnings.append(
                f"the decision rule was applied to {len(targets)} arms; a pre-registered rule normally has one confirmatory "
                "comparison (protocol §5 multiplicity)."
            )
        for a in targets:
            if a not in comparisons:
                raise ValueError(f"decision arm {a!r} is not a non-baseline arm in the data")
            rel_a, rel_b = calib_paired.get(a, (float("nan"), float("nan")))
            decisions[a] = apply_decision_rule(
                rule,
                comparisons[a][rule.metric],
                reliability_arm=rel_a,
                reliability_base=rel_b,
                failure_rate_arm=summaries[a].failure_rate,
                failure_rate_base=summaries[baseline].failure_rate,
                cost_per_question_arm=summaries[a].cost_per_question,
                mde_value=mde_value,
            )
            if mde_value is None:
                warnings.append(
                    f"{a}: no pre-registered MDE was passed (--mde); the kill rule used the MDE measured on this same data."
                )

    return Analysis(
        name=name,
        baseline=baseline,
        metric=metric,
        reps=reps,
        seed=seed,
        aggregation=aggregation,
        prepared=prep,
        summaries=summaries,
        comparisons=comparisons,
        calibration_paired=calib_paired,
        member_rows=member_rows,
        breakdowns=breakdowns,
        decisions=decisions,
        meta=dict(meta or {}),
        warnings=warnings,
    )


# --------------------------------------------------------------------------- rendering
SectionHook = Callable[[Analysis], str]

# Extra report sections, rendered after the built-in ones. B-47 (diversity metrics: member error correlation, member spread,
# diversity bonus) plugs in here: append a function `(Analysis) -> markdown`. It gets each member's per-question forecasts
# via `analysis.arms[arm].forecasts(member)` and the outcomes via `analysis.prepared.outcomes`.
EXTRA_SECTIONS: list[SectionHook] = []


def _table(header: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out


def _ci(c: Comparison) -> str:
    return f"[{_f(c.ci_low)}, {_f(c.ci_high)}]" if c.n else "n/a"


def _pct(x: float) -> str:
    return "n/a" if math.isnan(x) else f"{x:.1%}"


_CMP_HEADER = ["N", "clusters", "mean Δ", "95% CI", "median Δ", "win rate (W/T/L)", "SD(Δ)", "MDE", "reading"]


def _cmp_cells(c: Comparison) -> list[str]:
    return [
        str(c.n),
        str(c.n_clusters),
        _f(c.mean_diff),
        _ci(c),
        _f(c.median_diff),
        f"{c.win_rate:.0%} ({c.wins}/{c.ties}/{c.losses})" if c.n else "n/a",
        _f(c.sd_diff),
        _f(c.mde),
        c.verdict_words(),
    ]


def _aggregation_words(an: Analysis) -> str:
    parts = []
    for a, d in an.arms.items():
        src = ", ".join(f"{k} ×{v}" for k, v in sorted(d.aggregate_source.items()))
        parts.append(f"{a}: {src or 'none'}")
    return (
        f"mode `{an.aggregation}`; per repeat ({'; '.join(parts)}); median = median of the members that produced a number "
        "(live rule); then the mean over the k repeats, scored once per question"
    )


def render_report(analysis: Analysis, extra_sections: Sequence[SectionHook] | None = None) -> str:
    an, prep = analysis, analysis.prepared
    m = METRICS[an.metric]
    meta = an.meta
    nan = float("nan")
    lines: list[str] = [
        f"# {an.name}: experiment report",
        "",
        f"_Generated by `evals/report.py` v{REPORT_VERSION}. Do not edit by hand; re-run the generator (protocol §7)._",
        "",
        "## Reproducibility",
        "",
    ]
    n_per_arm = ", ".join(f"{a}: {s.n} (k = {s.k})" for a, s in an.summaries.items())
    dropped = Counter(prep.dropped.values())
    dropped_words = "; ".join(f"{r}: {c}" for r, c in sorted(dropped.items())) if dropped else "none"
    lines += _table(
        ["item", "value"],
        [
            ["git commit", meta.get("commit", "unknown")],
            ["generator", f"evals/report.py v{REPORT_VERSION}, sha256 `{meta.get('generator_sha256', 'unknown')}`"],
            ["input", f"`{meta.get('input', 'n/a')}`, sha256 `{meta.get('input_sha256', 'n/a')}`"],
            ["bootstrap", f"{an.reps:,} reps, resampling cluster_id, seed {an.seed}, percentile 95% CI"],
            ["aggregation", _aggregation_words(an)],
            ["baseline arm", an.baseline],
            ["primary metric", f"{m.label} ({'higher' if m.higher_is_better else 'lower'} is better)"],
            ["questions scored per arm", n_per_arm],
            ["questions dropped before scoring", f"{dropped_words} (of {len(prep.outcomes) + len(prep.dropped)} in the input)"],
        ],
    )
    lines += [""]

    if an.decisions:
        lines += ["## Decision (pre-registered rule, applied in code)", ""]
        for d in an.decisions.values():
            lines += [f"**Arm {d.arm}: {d.verdict.upper()}** under _{d.rule.name}_. MDE used: {_f(d.mde_used)}, {d.mde_source}.", ""]
            lines += _table(
                ["type", "condition", "met?", "detail"],
                [[c.kind, c.condition, "yes" if c.met else "no", c.detail] for c in d.checks],
            )
            lines += ["", "Adopt needs every adopt condition; kill needs any kill condition; otherwise inconclusive."]
            if d.note:
                lines += ["", f"Note: {d.note}."]
            lines += [""]

    lines += [
        f"## Paired comparison vs baseline {an.baseline}",
        "",
        (
            "Δ = arm − baseline per question, on the questions both arms forecast, then averaged. "
            "For the log score a positive Δ means the arm is better; for Brier a negative Δ does. "
            "Win rate counts ties as half. MDE = 2.8·SD(Δ)/√N. "
            f"Only the primary metric ({m.label}) is decision-making; the other is descriptive (protocol §5)."
        ),
        "",
    ]
    for key in [an.metric] + [k for k in METRICS if k != an.metric]:
        lines += [f"**{METRICS[key].label}**{' (primary)' if key == an.metric else ''}", ""]
        lines += _table(["arm", *_CMP_HEADER], [[a, *_cmp_cells(c[key])] for a, c in an.comparisons.items()])
        lines += [""]
    drops = {a: c[an.metric].dropped for a, c in an.comparisons.items() if c[an.metric].dropped}
    if drops:
        lines += ["Questions dropped from the pairing:", ""]
        lines += [f"- {a}: " + "; ".join(f"{r}: {n}" for r, n in sorted(dr.items())) for a, dr in drops.items()]
        lines += [""]
    se_lines = [
        f"- {a}: bootstrap SE {_f(c[an.metric].se_boot)} → cluster-aware MDE ≈ 2.8·SE = {_f(2.8 * c[an.metric].se_boot)} "
        f"(√N formula: {_f(c[an.metric].mde)})"
        for a, c in an.comparisons.items()
        if c[an.metric].n
    ]
    if se_lines:
        lines += ["The √N formula treats questions as independent; with clustered questions the bootstrap SE is the honest one:", ""]
        lines += se_lines + [""]

    lines += ["## Scores per arm", ""]
    lines += _table(
        ["arm", "N", "log score", "baseline score", "Brier", "reliability", "resolution", "uncertainty", "ECE", "sharpness"],
        [
            [
                a, s.n, _f(s.mean_log), _f(s.mean_baseline_score, 1), _f(s.mean_brier), _f(s.murphy.get("reliability", nan)),
                _f(s.murphy.get("resolution", nan)), _f(s.murphy.get("uncertainty", nan)), _f(s.ece), _f(s.sharpness),
            ]
            for a, s in an.summaries.items()
        ],
    )
    lines += [
        "",
        (
            "Each arm on all the questions it forecast (N may differ between arms; the paired table above uses the shared ones). "
            "Baseline score is Metaculus's (0 = a 50% forecast, 100 = perfect). Brier = reliability − resolution + uncertainty "
            "(+ a small within-bin term). Sharpness is the variance of the forecasts."
        ),
        "",
    ]
    if an.calibration_paired:
        lines += ["Reliability on the paired questions (what the decision rule compares):", ""]
        lines += _table(
            ["arm", "reliability arm", f"reliability {an.baseline}", "Δ"],
            [[a, _f(ra), _f(rb), _f(ra - rb)] for a, (ra, rb) in an.calibration_paired.items()],
        )
        lines += [""]

    lines += ["## Calibration", "", "_matplotlib is not a project dependency, so no plot; the table is the reliability diagram._", ""]
    for a, s in an.summaries.items():
        lines += [f"**{a}**", ""]
        lines += _table(
            ["bin", "N", "mean forecast", "observed rate", "gap"],
            [
                [
                    f"{r['lo']:.1f}–{r['hi']:.1f}", r["n"], _f(r["mean_forecast"], 3), _f(r["observed_rate"], 3),
                    f"{r['observed_rate'] - r['mean_forecast']:+.3f}",
                ]
                for r in s.calibration
            ],
        )
        lines += [""]

    lines += [
        "## Each member alone",
        "",
        (
            "Each model's own forecast (repeats averaged), scored like the aggregate. The last column pairs the member with the "
            f"same model in arm {an.baseline} ({m.label} Δ). Descriptive: a single model's win does not override the aggregate rule."
        ),
        "",
    ]
    lines += _table(
        ["arm", "member", "N", "log score", "Brier", "failure rate", f"Δ vs same model in {an.baseline} (95% CI, N)"],
        [
            [
                r["arm"], r["member"], r["n"], _f(r["log"]), _f(r["brier"]), _pct(r["failure_rate"]),
                f"{_f(r['vs_baseline'].mean_diff)} {_ci(r['vs_baseline'])}, {r['vs_baseline'].n}" if r["vs_baseline"] else "",
            ]
            for r in an.member_rows
        ],
    )
    lines += [""]

    if an.breakdowns:
        lines += [
            "## Breakdowns",
            "",
            f"Paired {m.label} Δ vs {an.baseline} within each group. Descriptive only; small groups are noisy.",
            "",
        ]
        for col, rows in an.breakdowns.items():
            lines += [f"**By {col}**", ""]
            lines += _table(
                ["group", "arm", *_CMP_HEADER], [[r["group"], r["comparison"].arm, *_cmp_cells(r["comparison"])] for r in rows]
            )
            lines += [""]

    lines += ["## Repeats, failures and cost", ""]
    lines += _table(
        [
            "arm", "k", "SD of p across repeats", "SD of log score across repeats", "member failure rate",
            "aggregate failure rate", "$ per question", "tokens per question",
        ],
        [
            [
                a, s.k, _f(s.repeat_sd_p), _f(s.repeat_sd_log), _pct(s.failure_rate), _pct(s.aggregate_failure_rate),
                "n/a" if s.cost_per_question is None else f"{s.cost_per_question:.4f}",
                "n/a" if s.tokens_per_question is None else f"{s.tokens_per_question:,.0f}",
            ]
            for a, s in an.summaries.items()
        ],
    )
    lines += [
        "",
        (
            "SD across repeats is within-config variance: the mean over questions of the SD of the aggregate forecast between "
            "repeats (n/a when k = 1). Member failure rate = failed member forecasts / all member forecasts; aggregate failure "
            "rate = repeats where no member produced a number. Cost and tokens are per question per repeat (one live forecast), "
            "summed over members."
        ),
        "",
    ]
    errs = {a: d.errors for a, d in prep.arms.items() if d.errors}
    if errs:
        lines += ["Failures by kind:", ""]
        lines += [f"- {a}: " + "; ".join(f"{k}: {n}" for k, n in sorted(e.items())) for a, e in errs.items()]
        lines += [""]

    hooks = list(EXTRA_SECTIONS) + list(extra_sections or [])
    if hooks:
        for h in hooks:
            lines += [h(an).rstrip(), ""]
    else:
        lines += [
            "## Diversity (B-47)",
            "",
            (
                "_Not computed yet. B-47 adds member error correlation, member spread and the diversity bonus here through "
                "`evals.report.EXTRA_SECTIONS`._"
            ),
            "",
        ]

    if an.warnings:
        lines += ["## Warnings", ""] + [f"- {w}" for w in an.warnings] + [""]
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- card, provenance, CLI
BEGIN, END = "<!-- report:begin (generated by evals/report.py; do not edit) -->", "<!-- report:end -->"


def fill_card(card_text: str, report_md: str) -> str:
    """Put the report into the card's '## Results (generated)' section, between markers so a re-run replaces it.
    Headings are demoted two levels so they nest under the section."""
    body = "\n".join(("##" + ln) if ln.startswith("#") else ln for ln in report_md.rstrip().splitlines())
    block = f"{BEGIN}\n{body}\n{END}"
    if BEGIN in card_text and END in card_text:
        pre, rest = card_text.split(BEGIN, 1)
        _, post = rest.split(END, 1)
        return pre + block + post
    mt = re.search(r"^## Results \(generated\)[^\n]*\n", card_text, re.MULTILINE)
    if not mt:
        raise ValueError("card has no '## Results (generated)' heading")
    return card_text[: mt.end()] + block + "\n" + card_text[mt.end() :]


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def provenance(input_path: str | Path) -> dict[str, str]:
    commit = _git("rev-parse", "HEAD") or "unknown (not a git checkout)"
    if not commit.startswith("unknown"):
        if _git("status", "--porcelain", "--", "evals/report.py", "evals/scoring.py"):
            commit += " (evals/report.py or evals/scoring.py has uncommitted changes: not a frozen analysis)"
        elif _git("status", "--porcelain", "--untracked-files=no"):
            commit += " (other tracked files modified)"
    path = Path(input_path)
    try:
        shown = path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        shown = path.name
    return {
        "commit": commit,
        "generator_sha256": sha256_file(__file__, normalise_newlines=True),
        "input": shown,
        "input_sha256": sha256_file(path),
    }


def rule_from_json(text: str) -> DecisionRule:
    """A DecisionRule from JSON. `base` names a rule in RULES to start from; a modified rule never keeps the base's name."""
    data = json.loads(text)
    base_key = data.pop("base", "")
    if base_key and base_key not in RULES:
        raise ValueError(f"unknown base rule {base_key!r}; choose from {sorted(RULES)}")
    base = RULES.get(base_key, DecisionRule(name="custom rule"))
    if "name" not in data and data:
        data["name"] = f"{base.name}, modified: " + ", ".join(f"{k}={v}" for k, v in sorted(data.items()))
    return replace(base, **data)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m evals.report", description="Experiment report generator (B-31).")
    ap.add_argument("input", help="long table (.csv or .jsonl)")
    ap.add_argument("--baseline", required=True, help="name of the baseline arm")
    ap.add_argument("--name", default="experiment", help="experiment id used in the title and the default output name")
    ap.add_argument("--metric", choices=sorted(METRICS), help="primary metric (default: the rule's metric, else log)")
    ap.add_argument("--reps", type=int, default=DEFAULT_REPS)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--aggregation", default="auto", choices=("auto", "median", "given"))
    ap.add_argument("--rule", choices=sorted(RULES), help="pre-registered decision rule to apply")
    ap.add_argument("--rule-json", help="custom DecisionRule as JSON or a path to a JSON file; 'base' names a rule to start from")
    ap.add_argument("--mde", type=float, help="pre-registered MDE for the kill rule (EXP-005: from the screen)")
    ap.add_argument("--arm", action="append", dest="arms", help="arm(s) the decision rule applies to (default: every non-baseline)")
    ap.add_argument("--by", action="append", help="column(s) to break results down by (default: question_type, source)")
    ap.add_argument("--forward-source", default=FORWARD_SOURCE, help="source value of the forward set ('' to skip)")
    ap.add_argument("--out", help="output path (default: docs/experiments/<name>-report.md)")
    ap.add_argument("--card", help="experiment card whose 'Results (generated)' section gets the report")
    args = ap.parse_args(argv)

    rule = None
    if args.rule_json:
        p = Path(args.rule_json)
        rule = rule_from_json(p.read_text(encoding="utf-8") if p.exists() else args.rule_json)
    elif args.rule:
        rule = RULES[args.rule]
    metric = args.metric or (rule.metric if rule else "log")
    an = analyse(
        read_table(args.input),
        args.baseline,
        name=args.name,
        metric=metric,
        reps=args.reps,
        seed=args.seed,
        aggregation=args.aggregation,
        rule=rule,
        mde_value=args.mde,
        decision_arms=args.arms,
        by=args.by or ("question_type", "source"),
        forward_source=args.forward_source,
        meta=provenance(args.input),
    )
    md = render_report(an)
    out = Path(args.out) if args.out else EXPERIMENTS / f"{args.name}-report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8", newline="\n")
    print(f"wrote {out}")
    if args.card:
        card = Path(args.card)
        card.write_text(fill_card(card.read_text(encoding="utf-8"), md), encoding="utf-8", newline="\n")
        print(f"filled the results section of {card}")
    for d in an.decisions.values():
        print("\n".join(d.lines()))
    for w in an.warnings:
        print(f"warning: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
