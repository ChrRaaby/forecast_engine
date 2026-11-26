"""The one look of a FAST lane experiment (EXP-017 card): paired Metaculus baseline scores against arm A, cluster bootstrap.

Locked until the card's question count or date: before that `look` prints counts only, so nobody can peek. Number questions are
scored on the CDF the live bot would submit (tools/exp010.py's builder, the live adapter's own submission step, loaded only when a
number question has to be scored). A question counts only if every arm of the experiment has a valid forecast and it resolved
(voided questions are out and counted). Clusters as fixed on the card: Polymarket questions that share a theme tag and close in
the same ISO week; code-made questions by family (temperature: city) and ISO week of the close.
"""
from __future__ import annotations

import importlib.util
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np

from evals import exp010, scoring
from evals.report import cluster_bootstrap_ci

from .experiments import Experiment
from .picker import PickerConfig

FLAGS_LEFT_OUT = ("market odds scrubbed", "research incomplete")
ROOT = Path(__file__).resolve().parents[1]


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.exists() else []


def load_records(root: Path) -> dict[tuple[str, int], dict[str, Any]]:
    """(arm, question id) -> its record; if a question was forecast twice by an arm, the later run wins."""
    out: dict[tuple[str, int], dict[str, Any]] = {}
    for path in sorted((root / "forecasts").glob("*-fast-*/q*.json")):
        arm = path.parent.name.split("-fast-", 1)[1]
        rec = json.loads(path.read_text(encoding="utf-8"))
        out[(arm, int(rec["question"]["question_id"]))] = rec
    return out


def live_builder():
    spec = importlib.util.spec_from_file_location("_exp010_for_fastlane", ROOT / "tools" / "exp010.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.LIVE


def axis_of(snap: dict[str, Any]) -> exp010.Axis:
    lo, hi = float(snap["lower_bound"]), float(snap["upper_bound"])
    size = 201 if snap["question_type"] == "numeric" else int(round(hi - lo)) + 1  # discrete: one bucket per whole value
    return exp010.Axis(question_type=snap["question_type"], lower_bound=lo, upper_bound=hi,
                       open_lower_bound=bool(snap.get("open_lower_bound")), open_upper_bound=bool(snap.get("open_upper_bound")),
                       zero_point=None, cdf_size=size, nominal_lower_bound=snap.get("nominal_lower_bound"),
                       nominal_upper_bound=snap.get("nominal_upper_bound"), question_id=snap.get("question_id"))


def score(rec: dict[str, Any], outcome: float, builder: Callable[[], Any]) -> float | None:
    """Metaculus baseline score of what the arm would have submitted; None if it had no valid forecast."""
    if rec.get("status") != "fastlane" or rec.get("aggregate") is None:
        return None
    snap, agg = rec["question"], rec["aggregate"]
    if snap["question_type"] == "binary":
        return scoring.binary_baseline_score(float(agg), int(outcome))
    axis = axis_of(snap)
    try:
        cdf = builder().build(axis, [(float(p), float(v)) for p, v in agg])
    except Exception:
        return None  # the live bot would not have submitted
    loc = scoring.numeric_unscaled_location(float(outcome), axis.lower_bound, axis.upper_bound, None)
    return exp010.score_cdf(cdf, loc, axis, metrics=("baseline",))["baseline"]


def _week(iso: str) -> str:
    y, w, _ = datetime.fromisoformat(iso).isocalendar()
    return f"{y}-W{w:02d}"


def clusters(picks: list[dict[str, Any]], broad: tuple[str, ...] = PickerConfig().broad_tags) -> dict[int, str]:
    """question id -> cluster id (rules on the card)."""
    out: dict[int, str] = {}
    pm = [p for p in picks if p["source"] == "polymarket"]
    parent = list(range(len(pm)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    by_theme: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, p in enumerate(pm):
        for t in set(p["meta"].get("tags") or []) - set(broad) - {None}:
            by_theme[(t, _week(p["closes"]))].append(i)
    for members in by_theme.values():
        for j in members[1:]:
            parent[find(j)] = find(members[0])
    for i, p in enumerate(pm):
        out[int(p["question_id"])] = f"pm:{pm[find(i)]['key']}"
    for p in picks:
        if p["source"] != "polymarket":
            m = p["meta"]
            fam = f"weather:{m.get('city')}" if m.get("family") == "weather" else m.get("family")
            out[int(p["question_id"])] = f"series:{fam}:{_week(p['closes'])}"
    return out


@dataclass
class Compared:
    arm: str
    level: float
    n: int
    clusters: int
    mean: float
    lo: float
    hi: float
    se: float

    @property
    def mde(self) -> float:
        return 2.8 * self.se

    def line(self, label: str) -> str:
        return (f"| {label} | {self.arm} − A | {self.n} | {self.clusters} | {self.mean:+.2f} | [{self.lo:+.2f}, {self.hi:+.2f}] "
                f"({self.level:.1%}) | {self.mde:.1f} |")


def compare(scores: dict[str, dict[int, float]], base: str, arm: str, qids: list[int], cl: dict[int, str], level: float,
            reps: int, seed: int) -> Compared:
    d = [scores[arm][q] - scores[base][q] for q in qids]
    c = [cl[q] for q in qids]
    if not d:
        return Compared(arm, level, 0, 0, float("nan"), float("nan"), float("nan"), float("nan"))
    lo, hi, se = cluster_bootstrap_ci(d, c, reps=reps, seed=seed, level=level)
    return Compared(arm, level, len(d), len(set(c)), float(np.mean(d)), lo, hi, se)


def look(exp: Experiment, root: Path, now: datetime, builder: Callable[[], Any] = live_builder, reps: int = 10_000
         ) -> tuple[str, bool]:
    """(markdown, ok). ok = False when the look is refused (too early), so a script can't mistake counts for a result."""
    picks = [p for p in _rows(root / "picks.jsonl") if p.get("experiment") == exp.name]
    outs = {int(r["question_id"]): r for r in _rows(root / "outcomes.jsonl")}
    recs = load_records(root)
    cache: dict[str, Any] = {}

    def get_builder():
        if "b" not in cache:
            cache["b"] = builder()
        return cache["b"]

    why_out: Counter = Counter()
    scores: dict[str, dict[int, float]] = {a: {} for a in exp.arms}
    flagged: set[int] = set()
    complete: list[int] = []
    for p in picks:
        q = int(p["question_id"])
        o = outs.get(q)
        if o is None:
            why_out["not resolved yet"] += 1
            continue
        if o.get("void"):
            why_out["voided (never resolved)"] += 1
            continue
        ok = True
        for a in exp.arms:
            rec = recs.get((a, q))
            s = score(rec, o["outcome"], get_builder) if rec else None
            if s is None:
                ok = False
                why_out[f"no valid forecast from {a}"] += 1
                continue
            scores[a][q] = s
            if any(any(f in fl for f in FLAGS_LEFT_OUT) for fl in rec.get("flags", [])):
                flagged.add(q)
        if ok:
            complete.append(q)
    n = len(complete)
    head = [f"# {exp.name} look, {now:%Y-%m-%d %H:%M} UTC", "",
            f"Picked {len(picks)}, complete {n} (card: look at {exp.look_n} or on {exp.look_day}; no verdict below {exp.min_n}).", ""]
    head += [f"- {k}: {v}" for k, v in sorted(why_out.items())]
    if n < exp.look_n and now.date() < exp.look_day:
        return "\n".join(head + ["", "**Locked:** the look is not due yet. Counts only."]), False
    if n < exp.min_n:
        return "\n".join(head + ["", f"**Too few questions** ({n} < {exp.min_n}): no verdict, counts only."]), True
    cl = clusters(picks)
    base = exp.arms[0]
    src = {int(p["question_id"]): p["source"] for p in picks}
    rows, res = [], {}
    for a in exp.arms[1:]:
        level = exp.level if a in exp.candidates else 0.95
        res[a] = compare(scores, base, a, complete, cl, level, reps, exp.seed)
        rows.append(res[a].line("all"))
        for s in ("polymarket", "series"):
            rows.append(compare(scores, base, a, [q for q in complete if src[q] == s], cl, level, reps, exp.seed).line(s))
        rows.append(compare(scores, base, a, [q for q in complete if q not in flagged], cl, level, reps, exp.seed)
                    .line("without flagged"))
    out = head + ["", "| Questions | Difference | N | Clusters | Mean | CI | MDE |", "|---|---|---|---|---|---|---|", *rows, ""]
    verdict = []
    aa = res.get(exp.aa_control) if exp.aa_control else None
    sens = res.get(exp.sensitivity_control) if exp.sensitivity_control else None
    if aa is not None:
        quiet = aa.lo <= 0 <= aa.hi
        verdict.append(f"1. A/A: {'passes' if quiet else 'FAILS'} (CI {'includes' if quiet else 'excludes'} 0).")
    if sens is not None:
        seen = sens.hi < 0
        verdict.append(f"2. Sensitivity: {'passes' if seen else 'FAILS'} (no-research CI {'wholly below 0' if seen else 'not wholly below 0'}).")
    for a in exp.candidates:
        c = res[a]
        sign = "+" if c.mean > 0 else "−" if c.mean < 0 else "0"
        verdict.append(f"3. {a}: FAST sign {sign} (CI [{c.lo:+.2f}, {c.hi:+.2f}]). Agreement is read against that arm's own "
                       "registered tournament look, by hand (card step 3).")
    return "\n".join(out + ["## Card steps", "", *verdict]), True
