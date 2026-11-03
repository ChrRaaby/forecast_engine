"""EXP-010 numeric range sharpening (B-68): offline analysis of stored live numeric/discrete forecasts. No model calls, $0.

Idea (card: docs/experiments/EXP-010-numeric-range-sharpening.md): our numeric ranges may be too wide. Post-processing only: move each
declared percentile towards the median by a factor s (s = 1.0 is today's forecast, s = 0.7 keeps 70% of each distance), rebuild the
CDF with the live code (`main.to_prediction` -> forecasting-tools' `NumericDistribution.get_cdf`, imported, not copied), and score
it against what happened. s is chosen by nested cross-validation over question clusters, so the reported gain is measured on
questions that were not used to pick s.

Two arms (Christian's decision 2, 2026-11-02), m = 2, so every arm's CI is at 97.5% (Bonferroni, protocol §5):
  A  sharpen by s;
  B  sharpen by s, then add P1 and P99 where a normal through the median and the sharpened P10 (or P90) would put them, on the
     question's own axis. At s = 1.0 arm B still adds the tails, so it is never today's forecast.
Both arms are compared with the same baseline, arm A at s = 1.0 (today's forecast, unchanged), on the same questions, folds and seed.

Where the live code comes in (ADR-0005): the CDF the bot submits is built in the live adapter (`main.to_prediction` and
forecasting-tools), and the harness in evals/ never imports the adapter (tests/test_hygiene.py). So this module takes the CDF builder
as a parameter (`CdfBuilder`) and holds everything else; `tools/exp010.py` is the one file that imports `main.to_prediction`
unchanged and passes it in. Counting and fetching need no CDF, so they also run from here.

    poetry run python -m evals.exp010 --count            # how many resolved numeric/discrete forecasts exist (gate: 40 questions)
    poetry run python -m evals.exp010 --fetch-scaling    # once per new question: store each question's Metaculus axis (needs METACULUS_TOKEN)
    poetry run python tools/exp010.py --card docs/experiments/EXP-010-numeric-range-sharpening.md     # the scored run
    poetry run python tools/exp010.py --run-kind rerun --resolved-after 2026-12-01 --card ...       # the one rerun (§6.3)
    poetry run python tools/exp010.py --confirm-s 0.8 --confirm-arm A --resolved-after 2027-01-15   # Tier 1 confirmation (§6.2)
    poetry run python tools/exp010.py --validate         # offline self-check of the tool (synthetic, ~10 s)
    poetry run python tools/exp010.py --demo <dir>       # writes a synthetic data set (made-up questions) to try the tool on
    poetry run python tools/exp010.py --power            # synthetic power check: what the procedure can detect at N = 40 / 80

Inputs: live records `data/runs/**/q*.json` (numeric + discrete; published and shadow; bot-testing-area runs skipped),
`data/outcomes/outcomes.jsonl` (evals.outcomes) and `data/outcomes/scaling.jsonl` (written by --fetch-scaling). The records store
the question's bounds but not its log-scale `zero_point` or a discrete question's bucket count, and the live CDF depends on both, so
they are read from Metaculus once per question and cached (free GET with the bot token, like evals.outcomes). A record without
stored scaling is dropped and counted, never guessed.

Selection: one forecast per (question, source), the earliest usable one; source = the live forecast or a named shadow. Era is the
roster of the record: `frontier` (TOURNAMENT_CONFIG), `cheap` (DEFAULT_CONFIG) or `other`. Clusters are Metaculus posts (the
sub-questions of a group share one). Every dropped record is counted with its reason. The tool never prints research text, question
text or per-question numbers: only counts, means, CIs and the chosen factors.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from forecast_engine.clock import Clock, SystemClock
from forecast_engine.config import DEFAULT_CONFIG, TOURNAMENT_CONFIG

from . import outcomes as oc
from . import report, scoring

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "data" / "runs"
OUTCOMES = oc.OUT
SCALING = ROOT / "data" / "outcomes" / "scaling.jsonl"
CARD = ROOT / "docs" / "experiments" / "EXP-010-numeric-range-sharpening.md"
DEFAULT_OUT = ROOT / "docs" / "experiments" / "EXP-010-report.md"

# ---- pre-registered on the card (v2); change only with a card amendment written before a scored run
GENERATOR_VERSION = "1.1.0"  # 1.1.0: arm B + kill rule without "mean Δ ≤ 0" (Christian's decisions 1 and 2, 2026-11-02)
GRID = (0.5, 0.6, 0.7, 0.8, 0.9, 1.0)
BASELINE_S = 1.0
ARMS = ("A", "B")  # m = 2 candidate arms -> CI level 1 - 0.05/2 = 97.5% for each (protocol §5)
ARM_LABELS = {"A": "sharpen", "B": "sharpen + normal-shaped P1/P99 tails"}
BASELINE_ARM = "A"  # the baseline is arm A at s = 1.0: today's forecast, bit for bit
TAIL_LEVELS = (0.01, 0.99)  # arm B's added percentiles
MIN_QUESTIONS = 40  # screen and rerun: distinct resolved numeric/discrete questions after drops
MIN_CONFIRM_QUESTIONS = 30  # Tier 1 confirmation on questions resolved after the go-live date
OUTER_FOLDS = 5
SEED = 1010
KILL_THRESHOLD_POINTS = 0.75  # Metaculus baseline points per forecast = 0.015 nats of numeric log score (F-12's value) [Christian]
TIER = 1
TYPES = ("numeric", "discrete")
USABLE_STATUSES = ("ok", "shadow")
PRIMARY = report.Metric("numeric_baseline", "Metaculus numeric baseline score (points)", True, lambda p, o: float("nan"))

RULE = report.DecisionRule(
    name="EXP-010 decision rule (card v3: no kill on mean Δ ≤ 0; 97.5% CIs with two arms)",
    metric=PRIMARY.key,
    adopt_requires_ci_excluding_zero=True,
    max_reliability_increase=None,  # binary-only check; the numeric calibration line (share outside 10-90%) is descriptive
    max_failure_rate_increase=None,  # post-processing on stored forecasts: no new failures possible
    kill_if_point_at_most=None,  # decision 1 (Christian, 2026-11-02): a mean Δ ≤ 0 is not a kill; it goes to the one rerun
    kill_if_ci_upper_below=KILL_THRESHOLD_POINTS,  # judged at the arm's own CI level (97.5% with two arms)
    on_conflict="kill",  # as F-12 (3): a real effect smaller than the kill threshold is dropped
)
CONFIRM_RULE = report.DecisionRule(
    name="EXP-010 Tier 1 confirmation rule (card v3): the arm that went live, one-sided 95% lower bound > 0 on fresh questions",
    metric=PRIMARY.key,
    max_reliability_increase=None,
    max_failure_rate_increase=None,
    kill_if_point_at_most=None,
    kill_if_ci_upper_below=None,
)
CONFIRM_LEVEL = 0.90  # the lower end of a two-sided 90% CI is the one-sided 95% bound

DROP_TEST = "bot-testing-area run (not a live record)"
DROP_UNREADABLE = "unreadable record file"
DROP_STATUS = "not a submitted or shadow forecast (status failed / refused)"
DROP_NO_AGGREGATE = "no aggregate forecast stored"
DROP_DUPLICATE = "later record of the same question and source (the earliest is kept)"
DROP_UNRESOLVED = "not resolved yet (or no outcome row)"
DROP_ANNULLED = "annulled or ambiguous"
DROP_NOT_FRESH = report.DROP_NOT_FRESH
DROP_NO_DATE = report.DROP_NO_DATE
DROP_NO_SCALING = "no stored scaling (run --fetch-scaling)"
DROP_SCALING_MISMATCH = "stored scaling disagrees with the record's bounds or type"
DROP_BAD_RESOLUTION = "resolution is not a number or a bound"
DROP_OUTSIDE_CLOSED = "resolution outside a closed bound (inconsistent with the question)"
DROP_CONVERSION = "live CDF conversion failed at some s (the live bot would not have submitted)"
DROP_INVALID_CDF = "the live CDF at some s breaks Metaculus's limits (it would have been refused)"
DROP_CONVERSION_B = ("arm B only: live CDF conversion failed at some s with the P1/P99 tails (dropped from both arms: they run on "
                     "the same questions)")
DROP_INVALID_CDF_B = ("arm B only: the live CDF with the P1/P99 tails breaks the limits at some s (dropped from both arms: they "
                      "run on the same questions)")


# --------------------------------------------------------------------------- the transform and the live CDF path
@dataclass(frozen=True)
class Axis:
    """What the live CDF builder needs to know about a question (no text): bounds and open bounds from the record, the log-scale
    zero point and the bucket count (`inbound_outcome_count`; fewer for discrete questions) from Metaculus's stored scaling."""

    question_type: str
    lower_bound: float
    upper_bound: float
    open_lower_bound: bool
    open_upper_bound: bool
    zero_point: float | None
    cdf_size: int  # inbound_outcome_count + 1: 201 for a continuous question
    nominal_lower_bound: float | None = None
    nominal_upper_bound: float | None = None
    question_id: int | None = None
    post_id: int | None = None


def axis_from(snapshot: dict[str, Any], scaling: dict[str, Any]) -> Axis:
    return Axis(
        question_type=snapshot["question_type"],
        lower_bound=float(snapshot["lower_bound"]),
        upper_bound=float(snapshot["upper_bound"]),
        open_lower_bound=bool(snapshot.get("open_lower_bound")),
        open_upper_bound=bool(snapshot.get("open_upper_bound")),
        zero_point=None if scaling.get("zero_point") is None else float(scaling["zero_point"]),
        cdf_size=int(scaling.get("inbound_outcome_count") or 200) + 1,
        nominal_lower_bound=snapshot.get("nominal_lower_bound"),
        nominal_upper_bound=snapshot.get("nominal_upper_bound"),
        question_id=snapshot.get("question_id"),
        post_id=snapshot.get("post_id"),
    )


class CdfBuilder(Protocol):
    """The live CDF step, passed in from tools/exp010.py (ADR-0005 keeps the adapter out of evals/)."""

    def build(self, axis: Axis, points: list[tuple[float, float]]) -> list[float]:
        """The CDF the live bot submits for these declared percentiles (raises if the live code would fail to convert them)."""

    def max_step(self, cdf_size: int) -> float:
        """The largest per-bucket mass the live code allows (forecasting-tools' cap; depends on the bucket count)."""


def _axis(question: Any | None):
    if question is None:
        return (lambda v: v), (lambda x: x)
    lo, hi, zp = float(question.lower_bound), float(question.upper_bound), question.zero_point
    return (lambda v: scoring.numeric_unscaled_location(v, lo, hi, zp)), (lambda x: scoring.numeric_scaled_location(x, lo, hi, zp))


def sharpen(points: Sequence[Sequence[float]], s: float, question: Any | None = None) -> list[tuple[float, float]]:
    """Scale each declared percentile's distance from the median by `s`; the percentile levels stay the same.

    The median is interpolated between the declared percentiles (ours are 10/20/40/60/80/90, so it is the midpoint of P40 and P60).
    Distances are measured on the question's own axis: plain values for a linear question, the log axis for a log-scaled one
    (`question.zero_point` set), so a log question is narrowed by the same factor on both sides of its median in ratio terms.
    s = 1.0 returns the points unchanged, bit for bit, so s = 1.0 is exactly today's forecast. Order and ties are kept for s > 0.
    """
    pts = [(float(p), float(v)) for p, v in points]
    if s == BASELINE_S:
        return pts
    if not 0 < s:
        raise ValueError(f"s must be > 0, got {s}")
    to_axis, from_axis = _axis(question)
    xs = [to_axis(v) for _, v in pts]
    if any(math.isnan(x) for x in xs):
        raise ValueError("a percentile lies at or below the question's zero point")
    m = float(np.interp(0.5, [p for p, _ in pts], xs))
    return [(p, float(from_axis(m + s * (x - m)))) for (p, _), x in zip(pts, xs)]


def with_normal_tails(points: Sequence[Sequence[float]], s: float, question: Any, tails: bool = True) -> list[tuple[float, float]]:
    """Arm B: sharpen by s, then add P1 and P99 where a normal would put them, on the question's own axis (log axis for a
    log-scaled question). On each side the normal goes through the median m and that side's outermost sharpened percentile
    (P10 below, P90 above): P1 = m + (P10 - m) * z(0.01) / z(0.1), P99 likewise with P90; for a symmetric forecast that is the one
    normal through P10 and P90, and a skewed forecast keeps its skew.

    Nothing is clamped here. A tail beyond a bound goes to the live builder as is, and `main.to_prediction` handles it exactly as
    it handles any percentile beyond a bound: clamped to a closed bound (no mass beyond it), to 1.9x the range beyond an open one
    (`parsing.fit_to_bounds`), and the open-bound mass comes from the standardisation, as live. `tails=False` returns the
    sharpened points only (the --validate check that arm B without its tails is arm A). At s = 1.0 the tails are still added."""
    sp = sharpen(points, s, question)
    if not tails:
        return sp
    to_axis, from_axis = _axis(question)
    ps, xs = [p for p, _ in sp], [to_axis(v) for _, v in sp]
    m = float(np.interp(0.5, ps, xs))
    z = statistics.NormalDist().inv_cdf
    out = list(sp)
    lo_p, hi_p = TAIL_LEVELS
    if ps[0] > lo_p and ps[0] < 0.5:
        out.insert(0, (lo_p, float(from_axis(m + (xs[0] - m) * z(lo_p) / z(ps[0])))))
    if ps[-1] < hi_p and ps[-1] > 0.5:
        out.append((hi_p, float(from_axis(m + (xs[-1] - m) * z(hi_p) / z(ps[-1])))))
    return out


def arm_points(points: Sequence[Sequence[float]], s: float, question: Any | None = None, arm: str = "A", tails: bool = True
               ) -> list[tuple[float, float]]:
    """The declared percentiles an arm hands to the live CDF builder: arm A sharpens, arm B sharpens and adds P1/P99."""
    if arm == "A":
        return sharpen(points, s, question)
    if arm == "B":
        return with_normal_tails(points, s, question, tails)
    raise ValueError(f"unknown arm {arm!r}; arms are {ARMS}")


def submitted_cdf(builder: CdfBuilder, axis: Axis, points: Sequence[Sequence[float]], s: float = BASELINE_S, arm: str = "A",
                  tails: bool = True) -> list[float]:
    """The CDF the live bot would submit for this aggregate under an arm at s. With tools/exp010.py's builder that is
    `main.to_prediction` (fit to the accepted range, break ties, NumericDistribution.from_question) and `get_cdf()`, exactly as
    `NumericReport.publish_report_to_metaculus` does. Arm A at s = 1.0 is today's forecast."""
    return builder.build(axis, arm_points(points, s, axis, arm, tails))


def cdf_problems(cdf: Sequence[float], question: Axis, max_step: float) -> list[str]:
    """Why Metaculus would refuse this CDF (empty = fine): length = the question's cdf_size; values in [0, 1]; every step at least
    0.01 / N (N inner buckets) and at most `max_step` (the live builder's cap); a closed bound has no mass beyond it (cdf[0] = 0,
    cdf[-1] = 1); an open bound keeps at least 0.001 beyond it."""
    c = np.asarray(cdf, dtype=float)
    n = int(question.cdf_size)
    if c.shape != (n,):
        return [f"{c.size} points, the question has {n}"]
    out = []
    if c.min() < 0 or c.max() > 1:
        out.append("values outside [0, 1]")
    steps = np.diff(c)
    if steps.min() < 0.01 / (n - 1) - 1e-9:
        out.append(f"a step of {steps.min():.2e} < 0.01/{n - 1}")
    cap = max_step
    if steps.max() > cap + 1e-9:
        out.append(f"a step of {steps.max():.4f} above the cap {cap:.4f}")
    if question.open_lower_bound and c[0] < 0.001 - 1e-12 or not question.open_lower_bound and c[0] != 0.0:
        out.append(f"lower-bound mass {c[0]:.6f} wrong for a {'open' if question.open_lower_bound else 'closed'} bound")
    if question.open_upper_bound and c[-1] > 0.999 + 1e-12 or not question.open_upper_bound and c[-1] != 1.0:
        out.append(f"upper-bound mass {1 - c[-1]:.6f} wrong for a {'open' if question.open_upper_bound else 'closed'} bound")
    return out


def outcome_location(resolution: Any, question: Any) -> float | None:
    """Metaculus resolution string -> position on the question's axis (formulas.py). None for annulled/ambiguous/unparseable."""
    res = str(resolution if resolution is not None else "").strip().lower()
    if res in ("", "annulled", "ambiguous"):
        return None
    if res == "below_lower_bound":
        return -1.0
    if res == "above_upper_bound":
        return 2.0
    try:
        value = float(res)
    except ValueError:
        return None
    return scoring.numeric_unscaled_location(value, float(question.lower_bound), float(question.upper_bound), question.zero_point)


def score_cdf(cdf: Sequence[float], location: float, question: Any, metrics: Sequence[str] = ("baseline", "log", "crps", "pit")
              ) -> dict[str, float]:
    """Primary and secondary scores of one CDF. Discrete questions have fewer buckets: `cdf_size` comes from the question."""
    n = len(cdf)
    if n != question.cdf_size:
        raise ValueError(f"CDF has {n} points but the question has {question.cdf_size}")
    out: dict[str, float] = {}
    if "baseline" in metrics:
        out["baseline"] = scoring.numeric_baseline_score(cdf, location, question.open_lower_bound, question.open_upper_bound,
                                                         cdf_size=n)
    if "log" in metrics:
        out["log"] = scoring.numeric_log_score(cdf, location, cdf_size=n)
    if "crps" in metrics:
        out["crps"] = scoring.numeric_crps(cdf, location, cdf_size=n)  # in fractions of the question's range (axis units)
    if "pit" in metrics:
        out["pit"] = scoring.numeric_pit(cdf, location, cdf_size=n)
    return out


def outside(pit: float) -> bool:
    return pit < 0.1 or pit > 0.9


# --------------------------------------------------------------------------- loading
FRONTIER_ROSTER = frozenset(f.model for f in TOURNAMENT_CONFIG.forecasters)
CHEAP_ROSTER = frozenset(f.model for f in DEFAULT_CONFIG.forecasters)


def era_of(models: Iterable[str]) -> str:
    roster = frozenset(models)
    return "frontier" if roster == FRONTIER_ROSTER else "cheap" if roster == CHEAP_ROSTER else "other"


def tournament_of(slugs: Iterable[str]) -> str:
    names = sorted({"minibench" if str(s).startswith("minibench") else str(s) for s in slugs or ()})
    return "+".join(names) or "none"


def source_of(path: Path) -> str:
    name = path.parent.name
    return "shadow-" + name.split("-shadow-", 1)[1] if "-shadow-" in name else "live"


@dataclass
class Case:
    """One stored forecast to score."""

    key: str  # "<question_id>:<source>"
    question_id: int
    cluster: str
    qtype: str
    era: str
    roster: str
    config_version: str
    source: str
    tournament: str
    resolved_at: str | None
    points: list[tuple[float, float]]
    question: Axis | None = None  # None until the question's stored scaling is attached
    resolution: str = ""
    location: float = float("nan")
    path: str = ""


@dataclass
class Loaded:
    cases: list[Case]
    dropped: Counter
    seen_records: int
    record_files: list[Path]
    pending: list[Case] = field(default_factory=list)  # resolved but no scaling yet (count mode)

    @property
    def n_questions(self) -> int:
        return len({c.question_id for c in self.cases})


def load_scaling(path: Path = SCALING) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                out[int(row["question_id"])] = row
    return out


def _bounds_match(snapshot: dict[str, Any], sc: dict[str, Any]) -> bool:
    def close(a: Any, b: Any) -> bool:
        return a is not None and b is not None and math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9)

    if not (close(snapshot.get("lower_bound"), sc.get("range_min")) and close(snapshot.get("upper_bound"), sc.get("range_max"))):
        return False
    if sc.get("type") and sc["type"] != snapshot["question_type"]:
        return False
    if snapshot["question_type"] == "discrete":  # its bucket count must be known; a continuous question defaults to 200
        return isinstance(sc.get("inbound_outcome_count"), int) and sc["inbound_outcome_count"] >= 2
    return True


def load_cases(runs_dir: Path = RUNS, outcomes_path: Path = OUTCOMES, scaling_path: Path = SCALING, *,
               resolved_after: date | None = None, need_scaling: bool = True) -> Loaded:
    """Resolved numeric/discrete live forecasts with everything needed to rebuild and score their CDF. Drops are counted by reason.
    With `need_scaling=False` (count mode) resolved forecasts without stored scaling are kept in `pending` instead of dropped."""
    outcomes = oc.load_outcomes(outcomes_path)
    scaling = load_scaling(scaling_path) if need_scaling or scaling_path.exists() else {}
    dropped: Counter = Counter()
    candidates: dict[str, list[tuple[str, Case, dict[str, Any]]]] = defaultdict(list)
    seen = 0
    files: list[Path] = []
    for path in sorted(runs_dir.rglob("q*.json")):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
            snap = rec["question"]
            qtype = snap["question_type"]
        except (OSError, ValueError, KeyError, TypeError):
            dropped[DROP_UNREADABLE] += 1
            continue
        if qtype not in TYPES:
            continue
        seen += 1
        files.append(path)
        if "test_questions" in path.parent.name:
            dropped[DROP_TEST] += 1
            continue
        if rec.get("status") not in USABLE_STATUSES:
            dropped[DROP_STATUS] += 1
            continue
        agg = rec.get("aggregate")
        if not agg or len(agg) < 2:
            dropped[DROP_NO_AGGREGATE] += 1
            continue
        source = source_of(path)
        models = [f.get("model", "") for f in rec.get("forecasters", [])]
        qid = int(snap["question_id"])
        case = Case(
            key=f"{qid}:{source}", question_id=qid, cluster=str(snap.get("post_id") or qid), qtype=qtype, era=era_of(models),
            roster="+".join(sorted(m.split("/")[-1] for m in models)), config_version=str(rec.get("config_version", "")),
            source=source, tournament=tournament_of(snap.get("tournaments") or ()), resolved_at=None,
            points=[(float(p), float(v)) for p, v in agg], path=str(path),
        )
        candidates[case.key].append((str(rec.get("as_of", "")), case, snap))
    cases: list[Case] = []
    pending: list[Case] = []
    for key in sorted(candidates, key=lambda k: (int(k.split(":")[0]), k)):
        group = sorted(candidates[key], key=lambda t: (t[0], t[1].path))
        if len(group) > 1:
            dropped[DROP_DUPLICATE] += len(group) - 1
        _, case, snap = group[0]
        row = outcomes.get(case.question_id)
        res = str((row or {}).get("resolution") or "").strip().lower()
        if row is None or row.get("status") not in oc.SETTLED or not res:
            dropped[DROP_UNRESOLVED] += 1
            continue
        if res in oc.NO_SCORE_RESOLUTIONS:
            dropped[DROP_ANNULLED] += 1
            continue
        case.resolution = res
        case.resolved_at = oc.resolved_date(row)
        if resolved_after is not None:
            if case.resolved_at is None:
                dropped[DROP_NO_DATE] += 1
                continue
            if date.fromisoformat(case.resolved_at) <= resolved_after:
                dropped[DROP_NOT_FRESH] += 1
                continue
        sc = scaling.get(case.question_id)
        if sc is None:
            if need_scaling:
                dropped[DROP_NO_SCALING] += 1
            else:
                pending.append(case)
            continue
        if not _bounds_match(snap, sc):
            dropped[DROP_SCALING_MISMATCH] += 1
            continue
        case.question = axis_from(snap, sc)
        loc = outcome_location(res, case.question)
        if loc is None or math.isnan(loc):
            dropped[DROP_BAD_RESOLUTION] += 1
            continue
        if loc < 0 and not case.question.open_lower_bound or loc > 1 and not case.question.open_upper_bound:
            dropped[DROP_OUTSIDE_CLOSED] += 1
            continue
        case.location = loc
        cases.append(case)
    return Loaded(cases, dropped, seen, files, pending)


# --------------------------------------------------------------------------- scoring and nested cross-validation
Cdfs = dict[str, dict[str, dict[float, list[float]]]]  # case key -> arm -> s -> CDF
Scores = dict[str, dict[str, dict[float, dict[str, float]]]]  # case key -> arm -> s -> metric -> value


def build_cdfs(cases: Sequence[Case], builder: CdfBuilder, grid: Sequence[float] = GRID, dropped: Counter | None = None,
               arms: Sequence[str] = ARMS) -> tuple[list[Case], Cdfs]:
    """Every case's live CDF for every arm at every s. A case whose CDF can't be built, or breaks Metaculus's limits, for some arm
    at some s is dropped from every arm (counted, arm A's reasons first): the live bot would have failed to submit it, and the
    arms are compared on the same questions. Arm A always comes along: its s = 1.0 is the baseline."""
    arms = tuple(dict.fromkeys((BASELINE_ARM, *arms)))
    reasons = {"A": (DROP_CONVERSION, DROP_INVALID_CDF), "B": (DROP_CONVERSION_B, DROP_INVALID_CDF_B)}
    kept, cdfs = [], {}
    for c in cases:
        cap = builder.max_step(c.question.cdf_size)
        built: dict[str, dict[float, list[float]]] = {}
        why = ""
        for arm in arms:
            try:
                built[arm] = {s: submitted_cdf(builder, c.question, c.points, s, arm) for s in grid}
            except Exception:  # noqa: BLE001 - the live path raises ValueError/ValidationError/AssertionError/ZeroDivisionError
                why = reasons[arm][0]
                break
            if any(cdf_problems(cdf, c.question, cap) for cdf in built[arm].values()):
                why = reasons[arm][1]
                break
        if why:
            if dropped is not None:
                dropped[why] += 1
            continue
        cdfs[c.key] = built
        kept.append(c)
    return kept, cdfs


def score_all(cases: Sequence[Case], cdfs: Cdfs, metrics: Sequence[str] = ("baseline", "log", "crps", "pit")) -> Scores:
    return {c.key: {arm: {s: score_cdf(cdf, c.location, c.question, metrics) for s, cdf in by_s.items()}
                    for arm, by_s in cdfs[c.key].items()} for c in cases}


def outer_folds(clusters: Iterable[str], k: int = OUTER_FOLDS, seed: int = SEED) -> dict[str, int]:
    """cluster -> outer fold. Whole clusters are assigned (a cluster never straddles folds), in a seeded random order, round robin,
    so fold sizes differ by at most one cluster. Fewer clusters than k gives one fold per cluster."""
    uniq = sorted(set(clusters))
    k = max(1, min(k, len(uniq)))
    order = np.random.default_rng(seed).permutation(len(uniq))
    return {uniq[i]: j % k for j, i in enumerate(order)}


def choose_s(keys: Iterable[str], primary: dict[str, dict[float, float]], grid: Sequence[float] = GRID) -> tuple[float, dict[float, float]]:
    """The s with the highest mean primary score on `keys`; a tie goes to the s closest to 1.0 (the simpler forecast)."""
    ks = list(keys)
    means = {s: float(np.mean([primary[k][s] for k in ks])) for s in grid}
    best = max(means.values())
    return max(s for s in grid if means[s] >= best - 1e-9), means


@dataclass
class Fold:
    fold: int
    n_train: int
    n_test: int
    clusters_test: int
    chosen_s: float


@dataclass
class CV:
    folds: list[Fold]
    oof_s: dict[str, float]  # case key -> the s chosen without that case's fold
    fold_of_cluster: dict[str, int]


def nested_cv(primary: dict[str, dict[float, float]], clusters: dict[str, str], grid: Sequence[float] = GRID,
              k: int = OUTER_FOLDS, seed: int = SEED) -> CV:
    """Outer folds split by cluster; for each, s is chosen on the other folds and applied to this one.

    Inner folds: the card asks for s to be chosen by cross-validation inside the training part. Nothing is fitted per fold here (the
    transform is fixed and the per-case scores at each s don't depend on the training data), so the inner cross-validated mean of
    each s equals its plain mean over the training part, and choosing on that mean is the same choice. The code uses the mean."""
    fold_of = outer_folds(clusters.values(), k, seed)
    keys = sorted(primary)
    folds, oof = [], {}
    for f in sorted(set(fold_of.values())):
        test = [q for q in keys if fold_of[clusters[q]] == f]
        train = [q for q in keys if fold_of[clusters[q]] != f]
        s, _ = choose_s(train, primary, grid) if train else (BASELINE_S, {})
        for q in test:
            oof[q] = s
        folds.append(Fold(f + 1, len(train), len(test), len({clusters[q] for q in test}), s))
    return CV(folds, oof, fold_of)


# --------------------------------------------------------------------------- analysis
@dataclass
class Group:
    name: str
    n: int
    n_questions: int
    comparison: report.Comparison | None
    best_s_in_sample: float
    outside_base: float
    outside_oof: float


@dataclass
class ArmResult:
    """One arm against the baseline (arm A at s = 1.0)."""

    arm: str
    label: str
    folds: list[Fold]
    s_all: float  # chosen on all the data: what would go live
    means_by_s: dict[float, dict[str, float]]  # in-sample, descriptive
    comparison: report.Comparison
    secondary: dict[str, tuple[float, float]]  # metric -> (baseline, out-of-fold s of this arm)
    decision: report.Decision
    breakdowns: dict[str, list[Group]]
    fold_beats_today: list[bool] = field(default_factory=list)  # descriptive: training mean at the chosen s > today's, per fold


@dataclass
class Analysis:
    run_kind: str
    n_questions: int
    n_cases: int
    n_clusters: int
    dropped: Counter
    seed: int
    reps: int
    k: int
    grid: tuple[float, ...]
    resolved_after: date | None
    level: float  # the CI level of every arm: 1 - 0.05/m (0.975 with two arms), 0.90 in confirm mode
    arms: dict[str, ArmResult]
    goes_live: str | None  # the arm that would go live now (None: none does)
    goes_live_words: str
    fixed_s: float | None = None  # confirm mode

    # arm A's result under the names the single-arm tool used (tests and --validate read them)
    @property
    def folds(self) -> list[Fold]:
        return self.arms["A"].folds if "A" in self.arms else next(iter(self.arms.values())).folds

    @property
    def comparison(self) -> report.Comparison:
        return self._first().comparison

    @property
    def decision(self) -> report.Decision:
        return self._first().decision

    @property
    def s_all(self) -> float:
        return self._first().s_all

    def _first(self) -> ArmResult:
        return self.arms.get("A") or next(iter(self.arms.values()))


def _comparison(primary: dict[str, dict[float, float]], arm_s: dict[str, float], clusters: dict[str, str], *, arm: str,
                reps: int, seed: int, level: float = 0.95, restrict: set[str] | None = None,
                base: dict[str, float] | None = None) -> report.Comparison:
    """Arm (each forecast at its out-of-fold s) against the baseline: `base` per forecast, or this arm's own s = 1.0 when not given
    (arm A, whose s = 1.0 is the baseline)."""
    arm_scores = {q: primary[q][arm_s[q]] for q in primary}
    base_scores = dict(base) if base is not None else {q: primary[q][BASELINE_S] for q in primary}
    return report.paired_compare(arm_scores, base_scores, clusters, PRIMARY, arm=arm, baseline="s = 1.0", reps=reps, seed=seed,
                                 restrict=restrict, level=level)


def _verdict(checks: Sequence[report.Check], on_conflict: str) -> tuple[str, str]:
    adopt = all(c.met for c in checks if c.kind == "adopt")
    kill = any(c.met for c in checks if c.kind == "kill")
    if adopt and kill:
        return on_conflict, ("adopt and kill conditions both hold: the effect is real (CI excludes 0) but below the kill threshold; "
                             f"the rule's on_conflict says {on_conflict}")
    return ("adopt" if adopt else "kill" if kill else "inconclusive"), ""


def _stability_check(arm: str, folds: Sequence[Fold]) -> report.Check:
    chosen = [f.chosen_s for f in folds]
    detail = "chosen s per fold: " + ", ".join(f"{s:g}" for s in chosen)
    if arm == "A":
        return report.Check("adopt", "stable across folds: every outer fold chose s < 1", all(s < BASELINE_S for s in chosen), detail)
    # arm B's lever is the tails, so its s may stay at 1.0 [Christian]: stable = every fold chose an s (had training data)
    return report.Check("adopt", "stable across folds: every outer fold chose an s (1.0 allowed: arm B's lever is the tails)",
                        bool(folds) and all(f.n_train > 0 for f in folds), detail)


def decide(comparison: report.Comparison, folds: Sequence[Fold], era_groups: Sequence[Group], run_kind: str, arm: str = "A"
           ) -> report.Decision:
    """The card's rule as code, for one arm: report.apply_decision_rule (CI excludes 0 at the arm's level, the 0.75-point kill
    threshold on the CI upper bound, conflict -> kill; no kill on a mean Δ ≤ 0, decision 1) plus EXP-010's own adopt conditions:
    stable across folds (arm A: every fold chose s < 1; arm B: every fold chose an s), and the frontier era (the live config) also
    gains out of fold."""
    if run_kind == "confirm":
        return report.apply_decision_rule(CONFIRM_RULE, comparison, reliability_arm=0.0, reliability_base=0.0,
                                          failure_rate_arm=0.0, failure_rate_base=0.0, run_kind="confirm", tier=TIER)
    d = report.apply_decision_rule(RULE, comparison, reliability_arm=0.0, reliability_base=0.0, failure_rate_arm=0.0,
                                   failure_rate_base=0.0, run_kind=run_kind, tier=TIER)
    if comparison.n == 0:
        return d
    own: list[report.Check] = [_stability_check(arm, folds)]
    fr = next((g for g in era_groups if g.name == "frontier"), None)
    if fr is None or fr.comparison is None or fr.comparison.n == 0:
        own.append(report.Check("adopt", "frontier era (live config) gains out of fold (point > 0)", False,
                                "no frontier forecasts in the data"))
    else:
        own.append(report.Check("adopt", "frontier era (live config) gains out of fold (point > 0)",
                                fr.comparison.improvement > 0,
                                f"N {fr.comparison.n}, point {report._f(fr.comparison.improvement, 2)}"))
    checks = [c for c in d.checks if c.kind == "adopt"] + own + [c for c in d.checks if c.kind != "adopt"]
    verdict, note = _verdict(checks, RULE.on_conflict)
    return replace(d, checks=checks, verdict=verdict, note=note,
                   next_step=report.next_step_words(verdict, comparison, RULE, run_kind, TIER))


def narrow_win(result: ArmResult) -> bool:
    """An adopt whose CI lower end is below the smallest effect worth having (§6.3): it counts as a screen, nothing goes live."""
    lo = result.comparison.improvement_ci[0]
    return result.decision.verdict == "adopt" and RULE.kill_if_ci_upper_below is not None and lo < RULE.kill_if_ci_upper_below


def which_goes_live(results: dict[str, ArmResult], run_kind: str) -> tuple[str | None, str]:
    """Which arm goes live now. Only a clean win goes live (a narrow win is a screen). If both arms win, the one with the larger
    out-of-fold mean Δ goes live [Christian]; an exact tie goes to arm A (no tails: the smaller change)."""
    if run_kind == "confirm":
        return None, ""
    wins = [r for r in results.values() if r.decision.verdict == "adopt" and not narrow_win(r)]
    if not wins:
        return None, "No arm goes live: live keeps s = 1.0 (today's forecast). Each arm's own `next:` line says what follows."
    best = max(wins, key=lambda r: (r.comparison.improvement, r.arm == "A"))
    if len(wins) == 1:
        return best.arm, (f"Arm {best.arm} ({best.label}) goes live at s = {best.s_all:g} (the s chosen on all the data); "
                          "the other arm does not.")
    others = ", ".join(f"arm {r.arm} {report._f(r.comparison.improvement, 2)}" for r in wins)
    return best.arm, (f"Both arms win; the card's rule [Christian] sends the one with the larger out-of-fold mean Δ live: arm "
                      f"{best.arm} ({best.label}) at s = {best.s_all:g} (mean Δ: {others}).")


def _analyse_arm(arm: str, cases: Sequence[Case], scores: Scores, clusters: dict[str, str], base: dict[str, float], *,
                 run_kind: str, grid: tuple[float, ...], k: int, seed: int, reps: int, level: float, fixed_s: float | None,
                 sec_metrics: Sequence[str], has_pit: bool) -> ArmResult:
    """One arm: its out-of-fold s per forecast (or the fixed s in confirm mode), compared with the baseline per forecast."""
    primary = {c.key: {s: scores[c.key][arm][s]["baseline"] for s in grid} for c in cases}
    beats: list[bool] = []
    if run_kind == "confirm":
        folds, oof = [], {q: fixed_s for q in primary}
        name = f"{arm} ({ARM_LABELS[arm]}) at s = {fixed_s:g} (fixed, adopted)"
    else:
        cv = nested_cv(primary, clusters, grid, k, seed)
        folds, oof = cv.folds, cv.oof_s
        name = f"{arm} ({ARM_LABELS[arm]}), s chosen out of fold"
        for f in folds:
            train = [q for q in primary if cv.fold_of_cluster[clusters[q]] != f.fold - 1]
            beats.append(bool(train) and float(np.mean([primary[q][f.chosen_s] for q in train]))
                         > float(np.mean([base[q] for q in train])) + 1e-9)
    s_all, _ = choose_s(primary, primary, grid) if primary else (BASELINE_S, {})
    comparison = _comparison(primary, oof, clusters, arm=name, reps=reps, seed=seed, level=level, base=base)

    def mean_of(metric: str, pick: dict[str, float] | None, keys: Iterable[str] | None = None) -> float:
        """pick None = the baseline (arm A at s = 1.0); else this arm at pick[q]."""
        vals = []
        for q in (primary if keys is None else keys):
            sc = scores[q][BASELINE_ARM][BASELINE_S] if pick is None else scores[q][arm][pick[q]]
            vals.append(float(outside(sc["pit"])) if metric == "outside" else sc[metric])
        return float(np.mean(vals)) if vals else float("nan")

    secondary = {m: (mean_of(m, None), mean_of(m, oof)) for m in sec_metrics}
    means_by_s = {s: {m: mean_of(m, {q: s for q in primary}) for m in ["baseline", *sec_metrics]} for s in grid}

    def groups(attr: str) -> list[Group]:
        out = []
        for gname in sorted({getattr(c, attr) for c in cases}):
            ks = [c.key for c in cases if getattr(c, attr) == gname]
            cmp_ = _comparison(primary, oof, clusters, arm=name, reps=reps, seed=seed, level=level, restrict=set(ks), base=base)
            best, _ = choose_s(ks, primary, grid)
            out.append(Group(gname, len(ks), len({c.question_id for c in cases if getattr(c, attr) == gname}), cmp_, best,
                             mean_of("outside", None, ks) if has_pit else float("nan"),
                             mean_of("outside", oof, ks) if has_pit else float("nan")))
        return out

    breakdowns = {"era": groups("era"), "type": groups("qtype"), "tournament": groups("tournament"), "source": groups("source")}
    decision = decide(comparison, folds, breakdowns["era"], run_kind, arm)
    return ArmResult(arm, ARM_LABELS[arm], folds, s_all, means_by_s, comparison, secondary, decision, breakdowns, beats)


def analyse(cases: Sequence[Case], scores: Scores, *, run_kind: str = "screen", dropped: Counter | None = None, seed: int = SEED,
            reps: int = report.DEFAULT_REPS, k: int = OUTER_FOLDS, grid: Sequence[float] = GRID, resolved_after: date | None = None,
            fixed_s: float | None = None, arms: Sequence[str] = ARMS, confirm_arm: str = "A") -> Analysis:
    """Per arm: nested CV (or, in confirm mode, the adopted arm at a fixed s) and the paired comparison against the baseline (arm A
    at s = 1.0) with report's cluster bootstrap, at CI level 1 - 0.05/m for m arms (protocol §5)."""
    grid = tuple(grid)
    if BASELINE_S not in grid:
        raise ValueError("the grid must contain the baseline s = 1.0")
    if run_kind == "confirm":
        if fixed_s is None or fixed_s not in grid:
            raise ValueError(f"confirm mode needs the adopted s, one of {grid}")
        arms = (confirm_arm,)
    arms = tuple(arms)
    if not arms or any(a not in ARMS for a in arms) or len(set(arms)) != len(arms):
        raise ValueError(f"arms must be distinct, from {ARMS}; got {arms}")
    level = CONFIRM_LEVEL if run_kind == "confirm" else report.family_level(len(arms))
    clusters = {c.key: c.cluster for c in cases}
    base = {c.key: scores[c.key][BASELINE_ARM][BASELINE_S]["baseline"] for c in cases}
    first = next(iter(scores.values()), {})
    have = set(first[BASELINE_ARM][BASELINE_S]) if first else set()
    sec_metrics = [m for m in ("log", "crps") if m in have] + (["outside"] if "pit" in have else [])
    results = {arm: _analyse_arm(arm, cases, scores, clusters, base, run_kind=run_kind, grid=grid, k=k, seed=seed, reps=reps,
                                 level=level, fixed_s=fixed_s, sec_metrics=sec_metrics, has_pit="pit" in have) for arm in arms}
    live_arm, live_words = which_goes_live(results, run_kind)
    return Analysis(run_kind, len({c.question_id for c in cases}), len(cases), len(set(clusters.values())), dropped or Counter(),
                    seed, reps, k, grid, resolved_after, level, results, live_arm, live_words, fixed_s)


# --------------------------------------------------------------------------- report
def _sha_file(path: Path) -> str:
    return report.sha256_file(path) if path.exists() else "missing"


def provenance(files: Sequence[Path], outcomes_path: Path, scaling_path: Path, root: Path | None = None) -> dict[str, str]:
    """Commit, hashes of the code that scored and of every input, the forecasting-tools version (it builds the CDF)."""
    commit = report._git("rev-parse", "HEAD") or "unknown (not a git checkout)"
    if not commit.startswith("unknown") and report._git("status", "--porcelain", "--", "evals/exp010.py", "evals/scoring.py",
                                                         "evals/report.py", "main.py", "forecast_engine/parsing.py"):
        commit += " (scoring code has uncommitted changes: not a frozen analysis)"
    h = hashlib.sha256()
    base = root or ROOT
    for f in sorted(files, key=lambda p: str(p)):
        try:
            rel = f.resolve().relative_to(base.resolve()).as_posix()
        except ValueError:
            rel = f.name
        h.update(rel.encode() + b"\0" + report.sha256_file(f).encode() + b"\n")
    try:
        ft = metadata.version("forecasting-tools")
    except metadata.PackageNotFoundError:
        ft = "unknown"
    return {
        "commit": commit,
        "generator": f"evals/exp010.py v{GENERATOR_VERSION}, sha256 {report.sha256_file(Path(__file__), normalise_newlines=True)}",
        "scoring": f"evals/scoring.py sha256 {report.sha256_file(Path(scoring.__file__), normalise_newlines=True)}",
        "report": f"evals/report.py v{report.REPORT_VERSION}",
        "live_cdf": f"main.to_prediction + forecasting-tools {ft} (via tools/exp010.py)",
        "records": f"{len(files)} numeric/discrete record files, combined sha256 {h.hexdigest()}",
        "outcomes": f"sha256 {_sha_file(outcomes_path)}",
        "scaling": f"sha256 {_sha_file(scaling_path)}",
    }


def _ci_cell(c: report.Comparison) -> str:
    lvl = "" if math.isclose(c.level, 0.95) else f" ({report._level_words(c.level)})"
    return f"[{report._f(c.ci_low, 2)}, {report._f(c.ci_high, 2)}]{lvl}"


def _cmp_row(name: str, c: report.Comparison | None, extra: Sequence[str] = ()) -> list[str]:
    if c is None or c.n == 0:
        return [name, "0", "", "", "", "", "", *extra]
    return [name, str(c.n), str(c.n_clusters), report._f(c.mean_diff, 2), _ci_cell(c), report._f(c.median_diff, 2),
            f"{c.win_rate:.0%} ({c.wins}/{c.ties}/{c.losses})", *extra]


def render(an: Analysis, prov: dict[str, str]) -> str:
    title = {"screen": "first run", "rerun": "rerun (§6.3)", "confirm": "Tier 1 confirmation (§6.2)"}[an.run_kind]
    L = [f"# EXP-010 numeric range sharpening: {title}", ""]
    L += ["_Generated by `python -m evals.exp010`; do not edit by hand. Post-processing of stored forecasts: no model calls, $0._", ""]
    L += ["## Provenance", ""]
    L += [f"- {k}: {v}" for k, v in prov.items()]
    seed_note = " (pre-registered)" if an.seed == SEED else f" (NOT the pre-registered seed {SEED})"
    design = (f"fixed s = {an.fixed_s:g} vs s = 1.0" if an.run_kind == "confirm"
              else f"outer folds {an.k}; grid " + ", ".join(f"{s:g}" for s in an.grid))
    L += [f"- seed {an.seed}{seed_note}; bootstrap {an.reps:,} reps over clusters; {design}"]
    m = len(an.arms)
    if an.run_kind == "confirm":
        L += [f"- arm confirmed: {', '.join(an.arms)}; one-sided 95% bound (two-sided {report._level_words(an.level)} CI)"]
    else:
        L += [f"- arms: {', '.join(f'{a} = {r.label}' for a, r in an.arms.items())}; m = {m}, so each arm's CI is at "
              f"{report._level_words(an.level)} (1 − 0.05/{m}, protocol §5), for the adopt condition and the kill rule alike"]
        L += ["- baseline for every arm: arm A at s = 1.0 (today's forecast, unchanged); arm B adds its tails at every s, s = 1.0 "
              "included"]
    L += [f"- resolved after: {an.resolved_after.isoformat() if an.resolved_after else 'no cut-off (first run)'}"]
    L += [f"- N: {an.n_questions} questions, {an.n_cases} forecasts (live + shadow), {an.n_clusters} clusters (Metaculus posts)", ""]
    L += ["### Dropped records", ""]
    if an.dropped:
        L += report._table(["reason", "records"], sorted(an.dropped.items(), key=lambda kv: (-kv[1], kv[0])))
    else:
        L += ["none"]
    L += ["", "## Primary result", ""]
    if an.run_kind == "confirm":
        L += [f"Fixed s = {an.fixed_s:g} of the arm that went live, against s = 1.0 (today) on questions resolved after the go-live "
              "date. Confirmed if the one-sided 95% lower bound (the lower end of the two-sided 90% CI) is above 0.", ""]
    else:
        L += ["Per arm: s chosen on the other folds and applied to each held-out fold (nested cross-validation, folds by cluster; "
              "the same folds and seed for both arms). Δ = Metaculus numeric baseline score of the arm at that s minus today's "
              "forecast (arm A at s = 1.0), per forecast, in points; positive = the arm helps.", ""]
    for a, r in an.arms.items():
        c = r.comparison
        L += [f"### Arm {a}: {r.label}", ""]
        if an.run_kind != "confirm":
            L += report._table(["outer fold", "train forecasts", "test forecasts", "test clusters", "chosen s",
                                "beats today on its training part"],
                               [[f.fold, f.n_train, f.n_test, f.clusters_test, f"{f.chosen_s:g}", "yes" if b else "no"]
                                for f, b in zip(r.folds, r.fold_beats_today)])
            L += ["", f"s chosen on all the data (what would go live on adoption): **{r.s_all:g}**", ""]
        L += report._table(["comparison", "N", "clusters", "mean Δ", "CI", "median Δ", "win rate (W/T/L)", "SD(Δ)", "MDE",
                            "cluster MDE"],
                           [_cmp_row(c.arm + " vs s = 1.0", c, [report._f(c.sd_diff, 2), report._f(c.mde, 2),
                                                                report._f(c.mde_cluster, 2)])])
        if 0 < c.n_clusters < report.FEW_CLUSTERS:
            L += ["", f"Warning: only {c.n_clusters} clusters; the percentile bootstrap is known to give too-narrow intervals "
                  f"below {report.FEW_CLUSTERS}."]
        L += [""]
    L += ["### Decision", ""]
    if an.run_kind == "confirm":
        L += [f"**{'CONFIRMED' if an.decision.verdict == 'adopt' else 'NOT CONFIRMED'}**", ""]
    L += ["```"]
    for r in an.arms.values():
        L += r.decision.lines()
    L += ["```", ""]
    if an.goes_live_words:
        L += [f"**Which arm goes live:** {an.goes_live_words}", ""]
    L += ["## Secondary metrics (descriptive)", ""]
    names = {"log": "numeric log score (nats, higher better)", "crps": "CRPS (fraction of range, lower better)",
             "outside": "share of outcomes outside the 10-90% range (≈20% if calibrated)"}
    first = an._first()

    def fmt(metric: str, x: float) -> str:
        return f"{x:.1%}" if metric == "outside" else report._f(x, 4)

    col = "confirmed s" if an.run_kind == "confirm" else "out-of-fold s"
    L += report._table(["metric", "s = 1.0 (today)", *[f"arm {a}, {col}" for a in an.arms]],
                       [[names[mk], fmt(mk, b), *[fmt(mk, r.secondary[mk][1]) for r in an.arms.values()]]
                        for mk, (b, _) in first.secondary.items()])
    for a, r in an.arms.items():
        L += ["", f"### Arm {a}: every s on all the data (in-sample, descriptive: not the test)", ""]
        hdr = ["s", "baseline score"] + [names[mk].split(" (")[0] for mk in r.secondary]
        L += report._table(hdr, [[f"{s:g}", report._f(v["baseline"], 2)] + [fmt(mk, v[mk]) for mk in r.secondary]
                                 for s, v in r.means_by_s.items()])
    L += ["", "## Breakdowns (descriptive, not decisions)", "",
          "Per arm: the same out-of-fold choice of s as the primary result, restricted to a subset, against today's forecast; the "
          "in-sample best s is that subset's own best on all its forecasts. Small groups have wide intervals.", ""]
    for a, r in an.arms.items():
        for name, gs in r.breakdowns.items():
            L += [f"### By {name} (arm {a})", ""]
            L += report._table([name, "N", "clusters", "mean Δ", "CI", "median Δ", "win rate (W/T/L)", "questions",
                                "in-sample best s", "outside 10-90% at s = 1.0", "at chosen s"],
                               [_cmp_row(g.name, g.comparison, [str(g.n_questions), f"{g.best_s_in_sample:g}",
                                                                 "n/a" if math.isnan(g.outside_base) else f"{g.outside_base:.0%}",
                                                                 "n/a" if math.isnan(g.outside_oof) else f"{g.outside_oof:.0%}"])
                                for g in gs])
            L += [""]
    L += ["## Notes", "",
          "- The CI resamples clusters of forecasts with the out-of-fold choices held fixed; it does not include the extra spread "
          "from which s each fold happened to pick. The fold tables show that spread.",
          "- Live and shadow forecasts of the same question share its outcome; they sit in one cluster, so they enter and leave the "
          "bootstrap together.",
          "- Both arms are scored on the same forecasts: a forecast whose live CDF fails for either arm at any s is dropped from both "
          "(see Dropped records).",
          "- N gate: the verdict is printed only at ≥ "
          f"{MIN_CONFIRM_QUESTIONS if an.run_kind == 'confirm' else MIN_QUESTIONS} resolved questions."]
    return "\n".join(L).rstrip() + "\n"


# --------------------------------------------------------------------------- count, fetch, run
def count_lines(loaded: Loaded, min_n: int = MIN_QUESTIONS) -> list[str]:
    ready = loaded.cases
    resolved = ready + loaded.pending
    n_res = len({c.question_id for c in resolved})
    n_ready = len({c.question_id for c in ready})
    lines = ["EXP-010 data count: resolved numeric/discrete forecasts (no scores are computed in this mode)", ""]
    lines.append(f"resolved questions: {n_res} (ready to score, scaling stored: {n_ready}); gate: {min_n} -> "
                 + ("reached" if n_ready >= min_n else f"not reached ({max(0, min_n - n_ready)} more needed)"))
    if loaded.pending:
        lines.append(f"{len({c.question_id for c in loaded.pending})} resolved question(s) have no stored scaling yet: "
                     "run `python -m evals.exp010 --fetch-scaling`")
    eras = sorted({c.era for c in resolved})
    lines += ["", "forecasts (one per question and source) by era and type:", f"  {'era':10s} {'numeric':>8s} {'discrete':>9s} "
              f"{'total':>6s}"]
    for e in eras:
        n = Counter(c.qtype for c in resolved if c.era == e)
        lines.append(f"  {e:10s} {n['numeric']:8d} {n['discrete']:9d} {sum(n.values()):6d}")
    n = Counter(c.qtype for c in resolved)
    lines.append(f"  {'all':10s} {n['numeric']:8d} {n['discrete']:9d} {sum(n.values()):6d}")
    by_q = Counter(c.qtype for c in {c.question_id: c for c in resolved}.values())
    lines.append(f"  questions: {by_q['numeric']} numeric, {by_q['discrete']} discrete")
    other = sorted({c.config_version for c in resolved if c.era == "other"})
    if other:
        lines.append(f"  'other' era config versions: {', '.join(other)}")
    lines += ["", f"numeric/discrete record files seen: {loaded.seen_records}"]
    for reason, k in sorted(loaded.dropped.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"  not counted: {k:4d}  {reason}")
    return lines


def build_count_lines(loaded: Loaded, builder: CdfBuilder) -> list[str]:
    """Count mode with the live builder: how many ready forecasts build a valid live CDF for both arms at every s, and why the
    others would be dropped. Builds CDFs only: no outcome is compared with anything, no score is computed."""
    dropped: Counter = Counter()
    kept, _ = build_cdfs(loaded.cases, builder, GRID, dropped)
    lines = ["", f"live CDF build check (no scores): {len(kept)} of {len(loaded.cases)} ready forecasts build a valid CDF for "
             f"both arms at every s ({len({c.question_id for c in kept})} questions)"]
    for reason, k in sorted(dropped.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"  would be dropped: {k:4d}  {reason}")
    return lines


def scaling_row(question_id: int, post_id: int, post: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    q = oc.find_question(post, question_id) or {}
    sc = q.get("scaling") or {}
    if "range_min" not in sc or "range_max" not in sc:
        return None
    return {
        "question_id": question_id, "post_id": post_id, "type": q.get("type"),
        "range_min": sc.get("range_min"), "range_max": sc.get("range_max"), "zero_point": sc.get("zero_point"),
        "nominal_min": sc.get("nominal_min"), "nominal_max": sc.get("nominal_max"),
        "inbound_outcome_count": sc.get("inbound_outcome_count"),
        "open_lower_bound": q.get("open_lower_bound"), "open_upper_bound": q.get("open_upper_bound"),
        "fetched_at": now.isoformat(timespec="seconds"),
    }


def fetch_scaling(session: Any, clock: Clock, runs_dir: Path = RUNS, out_path: Path = SCALING, *,
                  gap_s: float = oc.REQUEST_GAP_S) -> dict[str, int]:
    """Store Metaculus's axis (range, zero point, bucket count) once per forecast numeric/discrete question. Free GETs with the bot's
    token; a question already stored is not fetched again (the axis doesn't change once a question is open)."""
    have = load_scaling(out_path)
    todo = {qid: m for qid, m in oc.forecast_questions(runs_dir).items() if m["type"] in TYPES and qid not in have}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    counts = {"fetched": 0, "errors": 0, "already_stored": len(have)}
    with out_path.open("a", encoding="utf-8") as f:
        for qid, meta in sorted(todo.items()):
            resp = None
            for attempt in range(4):
                resp = session.get(oc.API.format(post_id=meta["post_id"]), timeout=60)
                if resp.status_code != 429:
                    break
                time.sleep(30 * (attempt + 1))
            if gap_s:
                time.sleep(gap_s)
            row = scaling_row(qid, meta["post_id"], resp.json(), clock.now()) if resp is not None and resp.ok else None
            if row is None:
                counts["errors"] += 1
                continue
            f.write(json.dumps(row) + "\n")
            counts["fetched"] += 1
    return counts


def not_enough(n: int, min_n: int) -> str:
    return f"not enough data: N = {n} resolved numeric/discrete questions (the card waits for N ≥ {min_n}); no verdict printed"


NEEDS_BUILDER = ("the scored run, the confirmation, --demo and --power rebuild the live CDF, which lives in the live adapter; "
                 "run them with: poetry run python tools/exp010.py <same arguments>")


def run(args: argparse.Namespace, builder: CdfBuilder | None = None) -> int:
    runs_dir, outcomes_path, scaling_path = Path(args.runs_dir), Path(args.outcomes), Path(args.scaling)
    cutoff = date.fromisoformat(args.resolved_after) if args.resolved_after else None
    if args.count:
        loaded = load_cases(runs_dir, outcomes_path, scaling_path, resolved_after=cutoff, need_scaling=False)
        print("\n".join(count_lines(loaded)))
        if builder is not None:
            print("\n".join(build_count_lines(loaded, builder)))
        return 0
    run_kind = "confirm" if args.confirm_s is not None else args.run_kind
    if run_kind in ("rerun", "confirm") and cutoff is None:
        print(f"a {run_kind} uses only questions resolved after a date: pass --resolved-after YYYY-MM-DD", file=sys.stderr)
        return 2
    if builder is None:
        print(NEEDS_BUILDER, file=sys.stderr)
        return 2
    min_n = MIN_CONFIRM_QUESTIONS if run_kind == "confirm" else MIN_QUESTIONS
    loaded = load_cases(runs_dir, outcomes_path, scaling_path, resolved_after=cutoff)
    if loaded.n_questions < min_n:  # gate before any score is computed: no peeking below N
        print(not_enough(loaded.n_questions, min_n))
        return 3
    dropped = Counter(loaded.dropped)
    cases, cdfs = build_cdfs(loaded.cases, builder, GRID, dropped)
    n_q = len({c.question_id for c in cases})
    if n_q < min_n:
        print(not_enough(n_q, min_n))
        return 3
    an = analyse(cases, score_all(cases, cdfs), run_kind=run_kind, dropped=dropped, seed=args.seed, reps=args.reps,
                 resolved_after=cutoff, fixed_s=args.confirm_s, arms=tuple(args.arms), confirm_arm=args.confirm_arm)
    md = render(an, provenance(loaded.record_files, outcomes_path, scaling_path))
    out = Path(args.out) if args.out else DEFAULT_OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8", newline="\n")
    for r in an.arms.values():
        print("\n".join(r.decision.lines()))
    if an.goes_live_words:
        print(f"which arm goes live: {an.goes_live_words}")
    print(f"wrote {out}")
    if args.card:
        card = Path(args.card)
        card.write_text(report.fill_card(card.read_text(encoding="utf-8"), md), encoding="utf-8", newline="\n")
        print(f"filled the results section of {card}")
    return 0


# --------------------------------------------------------------------------- synthetic data (tests, harness, demo): all made up
_Z = {0.1: -1.2815516, 0.2: -0.8416212, 0.4: -0.2533471, 0.6: 0.2533471, 0.8: 0.8416212, 0.9: 1.2815516}
SYN_AS_OF = datetime(2025, 3, 14, 12, 0, tzinfo=timezone.utc)


def synthetic_question(kind: str, qid: int, post_id: int, rng: np.random.Generator) -> tuple[dict[str, Any], dict[str, Any]]:
    """(record snapshot, scaling row) of a made-up question. kind: numeric (linear), log (log-scaled numeric) or discrete."""
    snap: dict[str, Any] = {"question_id": qid, "post_id": post_id, "question_text": "Synthetic question", "tournaments": ["synthetic"],
                            "unit_of_measure": "units", "open_lower_bound": False, "open_upper_bound": False}
    if kind == "discrete":
        n = int(rng.integers(6, 22))
        snap.update(question_type="discrete", lower_bound=-0.5, upper_bound=n - 0.5, nominal_lower_bound=0.0,
                    nominal_upper_bound=float(n - 1))
        sc = {"range_min": -0.5, "range_max": n - 0.5, "zero_point": None, "inbound_outcome_count": n}
    elif kind == "log":
        snap.update(question_type="numeric", lower_bound=1.0, upper_bound=10_000.0, open_upper_bound=bool(rng.random() < 0.5))
        sc = {"range_min": 1.0, "range_max": 10_000.0, "zero_point": 0.0, "inbound_outcome_count": 200}
    else:
        hi = float(rng.choice([50.0, 100.0, 400.0]))
        snap.update(question_type="numeric", lower_bound=0.0, upper_bound=hi, open_lower_bound=bool(rng.random() < 0.5),
                    open_upper_bound=bool(rng.random() < 0.5))
        sc = {"range_min": 0.0, "range_max": hi, "zero_point": None, "inbound_outcome_count": 200}
    sc.update(question_id=qid, post_id=post_id, open_lower_bound=snap["open_lower_bound"], open_upper_bound=snap["open_upper_bound"])
    return snap, sc


def synthetic_points(snap: dict[str, Any], sc: dict[str, Any], rng: np.random.Generator
                     ) -> tuple[list[tuple[float, float]], float, float]:
    """A made-up aggregate: normal percentiles on the question's axis (median 30-70% of the range, sd 6-16% of it).
    Returns (points, median, sd) with median and sd as fractions of the axis."""
    lo, hi, zp = float(snap["lower_bound"]), float(snap["upper_bound"]), sc.get("zero_point")
    mu, sd = rng.uniform(0.3, 0.7), rng.uniform(0.06, 0.16)
    pts = [(p, scoring.numeric_scaled_location(mu + sd * z, lo, hi, zp)) for p, z in _Z.items()]
    if snap["question_type"] == "discrete":
        pts = [(p, float(round(v))) for p, v in pts]  # models answer whole numbers: ties are normal
    else:
        pts = [(p, round(v, 3)) for p, v in pts]
    return pts, mu, sd


def draw_normal_resolution(question: Any, mu: float, sd: float, rng: np.random.Generator) -> str:
    """An outcome from a normal distribution on the question's axis (fractions of the range), stored as Metaculus would: closed
    bounds clip it, open bounds give below_lower_bound / above_upper_bound, a discrete question rounds to a whole value."""
    x = float(rng.normal(mu, sd))
    lo, hi, zp = float(question.lower_bound), float(question.upper_bound), question.zero_point
    if x < 0:
        if question.open_lower_bound:
            return "below_lower_bound"
        x = 0.0
    if x > 1:
        if question.open_upper_bound:
            return "above_upper_bound"
        x = 1.0
    value = scoring.numeric_scaled_location(x, lo, hi, zp)
    if question.question_type == "discrete":
        value = min(max(round(value), question.nominal_lower_bound), question.nominal_upper_bound)
    return repr(float(value))


def draw_resolution(cdf: Sequence[float], question: Any, rng: np.random.Generator) -> str:
    """An outcome drawn from a CDF, as Metaculus would store it: a number, or below_lower_bound / above_upper_bound."""
    n = len(cdf)
    pmf = np.clip(scoring.cdf_to_pmf(cdf, n), 0, None)
    b = int(rng.choice(n + 1, p=pmf / pmf.sum()))
    if b == 0:
        return "below_lower_bound"
    if b == n:
        return "above_upper_bound"
    inner = n - 1
    lo, hi, zp = float(question.lower_bound), float(question.upper_bound), question.zero_point
    if question.question_type == "discrete":
        return repr(float(round(scoring.numeric_scaled_location((b - 0.5) / inner, lo, hi, zp))))
    x = (b - 1 + rng.uniform(0.05, 0.95)) / inner
    return repr(float(scoring.numeric_scaled_location(x, lo, hi, zp)))


def synthetic_cases(builder: CdfBuilder, n_questions: int, *, true_s: float = 1.0, seed: int = 0, per_cluster: int = 2, shadow_share: float = 0.5,
                    kinds: Sequence[str] = ("numeric", "numeric", "log", "discrete"), truth: str = "cdf"
                    ) -> list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]:
    """Made-up (record, outcome row, scaling row) triples. Questions come in clusters (`per_cluster` per post); a share of them also
    gets a cheap-roster shadow forecast, a little wider than the live one.

    truth = "cdf": the outcome is drawn from the CDF the live code builds after sharpening the live forecast by `true_s`. true_s =
    1.0 is an exactly calibrated forecaster (outcomes come from its own submitted distribution, so s = 1.0 is the best s by
    propriety); true_s < 1 is too wide in the middle (the live CDF keeps 10% beyond P10 and P90 whatever s is).
    truth = "normal": the outcome is drawn from the normal the percentiles came from, with its sd times `true_s`: too wide
    everywhere, tails included, the way 0 of 19 outcomes outside our 10-90% ranges suggests."""
    if truth not in ("cdf", "normal"):
        raise ValueError(f"truth must be 'cdf' or 'normal', got {truth!r}")
    rng = np.random.default_rng(seed)
    out = []
    frontier = [f.model for f in TOURNAMENT_CONFIG.forecasters]
    cheap = [f.model for f in DEFAULT_CONFIG.forecasters]
    for i in range(n_questions):
        qid, post = 900_000 + i, 800_000 + i // max(1, per_cluster)
        kind = kinds[int(rng.integers(len(kinds)))]
        snap, sc = synthetic_question(kind, qid, post, rng)
        q = axis_from(snap, sc)
        pts, mu, sd = synthetic_points(snap, sc, rng)
        if truth == "cdf":
            resolution = draw_resolution(submitted_cdf(builder, q, pts, true_s), q, rng)
        else:
            resolution = draw_normal_resolution(q, mu, sd * true_s, rng)
        resolved = (SYN_AS_OF + timedelta(days=10 + i)).isoformat()
        row = {"question_id": qid, "post_id": post, "type": snap["question_type"], "status": "resolved", "resolution": resolution,
               "actual_resolve_time": resolved, "scores": {"baseline_score": 0.0}}
        sources = [("live", frontier, pts)]
        if rng.random() < shadow_share:
            wide = sharpen(pts, 1.15, q)
            if snap["question_type"] == "discrete":
                wide = [(p, float(round(v))) for p, v in wide]
            sources.append(("shadow-cheap", cheap, wide))
        for source, models, agg in sources:
            rec = {"config_version": "synthetic", "config_hash": "synthetic", "question": snap, "as_of": SYN_AS_OF.isoformat(),
                   "aggregation": "median", "aggregate": [list(p) for p in agg], "status": "ok" if source == "live" else "shadow",
                   "published": source == "live", "forecasters": [{"model": m} for m in models],
                   "research": {"as_of": SYN_AS_OF.isoformat(), "providers": [], "items": [], "calls": []}}
            out.append((rec, row, sc | {"_source": source}))
    return out


def write_synthetic(root: Path, triples: Sequence[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]) -> dict[str, Path]:
    """Lay synthetic triples out like the PC's data/: runs/<run>/q<id>.json (shadow under <run>-shadow-cheap), outcomes, scaling."""
    runs, outcomes_path, scaling_path = root / "runs", root / "outcomes.jsonl", root / "scaling.jsonl"
    root.mkdir(parents=True, exist_ok=True)
    seen_out, seen_sc = set(), set()
    with outcomes_path.open("w", encoding="utf-8") as fo, scaling_path.open("w", encoding="utf-8") as fs:
        for rec, row, sc in triples:
            source = sc["_source"]
            run_dir = runs / "1" / ("20250314T120000Z-tournament" + ("" if source == "live" else "-" + source))
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / f"q{rec['question']['question_id']}.json").write_text(json.dumps(rec), encoding="utf-8")
            if row["question_id"] not in seen_out:
                fo.write(json.dumps(row) + "\n")
                seen_out.add(row["question_id"])
            if sc["question_id"] not in seen_sc:
                fs.write(json.dumps({k: v for k, v in sc.items() if k != "_source"}) + "\n")
                seen_sc.add(sc["question_id"])
    return {"runs": runs, "outcomes": outcomes_path, "scaling": scaling_path}


def synthetic_analysis(builder: CdfBuilder, tmp: Path, n_questions: int, *, true_s: float, seed: int, reps: int = 2000,
                       truth: str = "cdf", shadow_share: float = 0.0, metrics: Sequence[str] = ("baseline",),
                       arms: Sequence[str] = ARMS) -> tuple[Analysis, Loaded]:
    """The whole path on made-up data written to `tmp`: records -> load_cases -> live CDFs -> scores -> nested CV -> decision."""
    paths = write_synthetic(tmp, synthetic_cases(builder, n_questions, true_s=true_s, seed=seed, truth=truth,
                                                 shadow_share=shadow_share))
    loaded = load_cases(paths["runs"], paths["outcomes"], paths["scaling"])
    dropped = Counter(loaded.dropped)
    cases, cdfs = build_cdfs(loaded.cases, builder, GRID, dropped, arms)
    return analyse(cases, score_all(cases, cdfs, metrics), dropped=dropped, reps=reps, arms=arms), loaded


# --------------------------------------------------------------------------- synthetic power check (card v2, "what the tool can see")
POWER_TRUTHS = (("cdf", 1.0), ("cdf", 0.7), ("cdf", 0.6), ("normal", 1.0), ("normal", 0.7), ("normal", 0.6))


def power_simulation(builder: CdfBuilder, n_values: Sequence[int] = (40, 80), trials: int = 60, pool_size: int = 160,
                     reps: int = 2000, seed: int = 5) -> list[str]:
    """How often the card's procedure finds a gain on made-up data, at the card's N, for both arms at the card's level (97.5%,
    m = 2). Two kinds of truth, each with the outcome's spread at `true_s` times the forecast's: "cdf" draws the outcome from the
    live CDF of the forecast sharpened by true_s (truth keeps the live CDF's linear tails); "normal" draws it from the normal the
    percentiles came from (true_s = 1.0: the middle is right but the live CDF's tails are too fat; < 1: narrower everywhere).
    Synthetic, so it says what the tool can detect, not what our data will show."""
    rng = np.random.default_rng(seed)
    kinds = ("numeric", "numeric", "log", "discrete")
    level = report.family_level(len(ARMS))
    pool = []
    for i in range(pool_size):
        snap, sc = synthetic_question(kinds[i % len(kinds)], i, i, rng)
        q = axis_from(snap, sc)
        pts, mu, sd = synthetic_points(snap, sc, rng)
        try:
            cdfs = {arm: {s: submitted_cdf(builder, q, pts, s, arm) for s in GRID} for arm in ARMS}
        except Exception:  # noqa: BLE001 - as build_cdfs: a question the live code can't convert for some arm is left out
            continue
        cap = builder.max_step(q.cdf_size)
        if any(cdf_problems(c, q, cap) for by_s in cdfs.values() for c in by_s.values()):
            continue
        pool.append((q, mu, sd, cdfs))

    def resolution(q: Any, mu: float, sd: float, cdfs: dict[str, dict[float, list[float]]], truth: str, true_s: float,
                   r: np.random.Generator) -> float:
        res = draw_resolution(cdfs["A"][true_s], q, r) if truth == "cdf" else draw_normal_resolution(q, mu, sd * true_s, r)
        return outcome_location(res, q)

    def score(c: list[float], q: Any, loc: float) -> float:
        return scoring.numeric_baseline_score(c, loc, q.open_lower_bound, q.open_upper_bound, cdf_size=len(c))

    lines = ["Synthetic power check of the EXP-010 procedure (made-up questions; numeric, log-scaled and discrete; 2 per cluster)",
             f"pool {len(pool)} questions, {trials} trials per row, bootstrap {reps} reps, seed {seed}; arms A (sharpen) and B "
             f"(sharpen + normal P1/P99), each vs today (A at s = 1.0) at the card's {report._level_words(level)} CI (m = {len(ARMS)})",
             "", "truth   true spread  N   arm     CI>0 (gain found)  CI<0  mean Δ  SD(Δ)  cluster MDE  stable"]
    for truth, true_s in POWER_TRUTHS:
        for n in n_values:
            res: dict[str, list[tuple[report.Comparison, list[Fold]]]] = {arm: [] for arm in ARMS}
            for t in range(trials):
                r = np.random.default_rng(seed * 1000 + t)
                idx = r.choice(len(pool), n, replace=False)
                primary: dict[str, dict[str, dict[float, float]]] = {arm: {} for arm in ARMS}
                clusters = {}
                for j, i in enumerate(idx):
                    q, mu, sd, cdfs = pool[i]
                    loc = resolution(q, mu, sd, cdfs, truth, true_s, r)
                    for arm in ARMS:
                        primary[arm][str(j)] = {s: score(c, q, loc) for s, c in cdfs[arm].items()}
                    clusters[str(j)] = str(j // 2)
                base = {q: primary["A"][q][BASELINE_S] for q in clusters}
                for arm in ARMS:
                    cv = nested_cv(primary[arm], clusters)
                    res[arm].append((_comparison(primary[arm], cv.oof_s, clusters, arm=arm, reps=reps, seed=t, level=level,
                                                 base=base), cv.folds))
            for arm in ARMS:
                rows = res[arm]
                stable = np.mean([_stability_check(arm, fs).met for _, fs in rows])
                lines.append(f"{truth:7s} x{true_s:<11g} {n:<3d} {arm:7s} {np.mean([c.ci_low > 0 for c, _ in rows]):>17.0%} "
                             f"{np.mean([c.ci_high < 0 for c, _ in rows]):>5.0%} {np.mean([c.mean_diff for c, _ in rows]):>7.2f} "
                             f"{np.mean([c.sd_diff for c, _ in rows]):>6.1f} {np.mean([c.mde_cluster for c, _ in rows]):>12.1f} "
                             f"{stable:>7.0%}")
            either = np.mean([res["A"][t][0].ci_low > 0 or res["B"][t][0].ci_low > 0 for t in range(trials)])
            lines.append(f"{truth:7s} x{true_s:<11g} {n:<3d} {'A or B':7s} {either:>17.0%}")
    lines += ["", "stable: arm A = every outer fold chose s < 1; arm B = every outer fold chose an s (1.0 allowed).",
              "CI>0 / CI<0: share of trials whose CI lies wholly above / below 0. 'A or B': at least one arm found a gain (on the "
              "calibrated rows that is the family-wide false alarm, ≈ ≤ 5% by design)."]
    return lines


# --------------------------------------------------------------------------- CLI
def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m evals.exp010", description="EXP-010 numeric range sharpening (B-68).")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--count", action="store_true", help="only count resolved numeric/discrete forecasts per type and era")
    mode.add_argument("--fetch-scaling", action="store_true", help="store each question's Metaculus axis (needs METACULUS_TOKEN)")
    mode.add_argument("--confirm-s", type=float, metavar="S", help="Tier 1 confirmation of the adopted S on fresh questions")
    ap.add_argument("--confirm-arm", choices=ARMS, default="A", help="--confirm-s: the arm that went live (default A)")
    ap.add_argument("--arms", nargs="+", choices=ARMS, default=list(ARMS),
                    help="candidate arms (default A B: m = 2, 97.5%% CIs); a rerun leaves out an arm the first run killed")
    mode.add_argument("--demo", metavar="DIR", help="write a synthetic data set (made-up questions) to DIR and exit")
    mode.add_argument("--power", action="store_true", help="synthetic power check of the procedure at the card's N (~15 s)")
    ap.add_argument("--run-kind", choices=("screen", "rerun"), default="screen", help="screen = first run; rerun = the one rerun")
    ap.add_argument("--resolved-after", metavar="YYYY-MM-DD", help="only questions that resolved strictly after this date (§6.3)")
    ap.add_argument("--runs-dir", default=str(RUNS))
    ap.add_argument("--outcomes", default=str(OUTCOMES))
    ap.add_argument("--scaling", default=str(SCALING))
    ap.add_argument("--out", help=f"report path (default {DEFAULT_OUT.relative_to(ROOT).as_posix()})")
    ap.add_argument("--card", help="experiment card whose 'Results (generated)' section gets the report")
    ap.add_argument("--seed", type=int, default=SEED, help=f"fold and bootstrap seed (pre-registered: {SEED})")
    ap.add_argument("--reps", type=int, default=report.DEFAULT_REPS, help="bootstrap reps (default 10,000)")
    ap.add_argument("--demo-kind", choices=("too-wide", "calibrated"), default="too-wide",
                    help="--demo: too-wide (outcomes from a normal with 0.6 x the forecast's spread) or calibrated")
    ap.add_argument("--demo-n", type=int, default=60, help="--demo: number of synthetic questions")
    return ap


def main(argv: Sequence[str] | None = None, builder: CdfBuilder | None = None) -> int:
    args = parser().parse_args(argv)
    if (args.power or args.demo) and builder is None:
        print(NEEDS_BUILDER, file=sys.stderr)
        return 2
    if args.power:
        print("\n".join(power_simulation(builder)))
        return 0
    if args.demo:
        too_wide = args.demo_kind == "too-wide"
        triples = synthetic_cases(builder, args.demo_n, true_s=0.6 if too_wide else 1.0, seed=7, truth="normal" if too_wide else "cdf")
        paths = write_synthetic(Path(args.demo), triples)
        print(f"wrote a synthetic data set ({args.demo_kind}, {args.demo_n} made-up questions): "
              + ", ".join(f"--{k.replace('runs', 'runs-dir')} {v}" for k, v in paths.items()))
        return 0
    if args.fetch_scaling:
        import requests  # noqa: PLC0415
        from dotenv import load_dotenv  # noqa: PLC0415

        load_dotenv(ROOT / ".env")
        s = requests.Session()
        s.headers["Authorization"] = f"Token {os.environ['METACULUS_TOKEN']}"
        c = fetch_scaling(s, SystemClock(), Path(args.runs_dir), Path(args.scaling))
        print(f"scaling: {c['fetched']} fetched, {c['errors']} errors, {c['already_stored']} already stored ({args.scaling})")
        return 0 if not c["errors"] else 1
    return run(args, builder)


if __name__ == "__main__":
    raise SystemExit(main())
