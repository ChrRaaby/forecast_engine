"""The look of a FAST lane experiment: paired Metaculus baseline scores, cluster bootstrap. EXP-017: each arm against A. EXP-018 (a
2x2, `Experiment.contrast`): the mean of its two (treatment − control) differences, the interaction, and per-model and per-arm tables.

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
    return compare_values(arm, {q: scores[arm][q] - scores[base][q] for q in qids}, qids, cl, level, reps, seed)


def compare_values(label: str, values: dict[int, float], qids: list[int], cl: dict[int, str], level: float, reps: int, seed: int
                   ) -> Compared:
    d = [values[q] for q in qids]
    c = [cl[q] for q in qids]
    if not d:
        return Compared(label, level, 0, 0, float("nan"), float("nan"), float("nan"), float("nan"))
    lo, hi, se = cluster_bootstrap_ci(d, c, reps=reps, seed=seed, level=level)
    return Compared(label, level, len(d), len(set(c)), float(np.mean(d)), lo, hi, se)


def member_scores(rec: dict[str, Any], outcome: float, builder: Callable[[], Any]) -> dict[str, float]:
    """Each forecaster's own answer scored as if it were the only one (EXP-018's per-model table)."""
    out = {}
    for f in rec.get("forecasters") or []:
        if f.get("prediction") is None or f.get("excluded"):
            continue
        s = score({"status": "fastlane", "aggregate": f["prediction"], "question": rec["question"]}, outcome, builder)
        if s is not None:
            out[f["model"]] = s
    return out


def arm_stats(rec: dict[str, Any]) -> dict[str, float]:
    calls = [f.get("call") or {} for f in rec.get("forecasters") or []]
    n = max(1, len(calls))
    return {"cut_off": sum(1 for c in calls if (c.get("extra") or {}).get("finish_reason") == "length") / n,
            "empty_or_failed": sum(1 for c in calls if c.get("error")) / n,
            "unparsed": sum(1 for f in rec.get("forecasters") or [] if not (f.get("call") or {}).get("error") and f.get("parse_error")) / n,
            "tokens_out": float(sum(c.get("tokens_out") or 0 for c in calls)),
            "seconds": float(max([c.get("latency_s") or 0.0 for c in calls], default=0.0)),
            "usd": float(sum(c.get("cost_usd") or 0.0 for c in calls))}


def contrast_section(exp: Experiment, scores: dict[str, dict[int, float]], complete: list[int], cl: dict[int, str],
                     src: dict[int, str], flagged: set[int], members: dict[str, dict[int, dict[str, float]]],
                     stats: dict[str, dict[int, dict[str, float]]], reps: int) -> list[str]:
    (t1, c1), (t2, c2) = exp.contrast
    d1 = {q: scores[t1][q] - scores[c1][q] for q in complete}
    d2 = {q: scores[t2][q] - scores[c2][q] for q in complete}
    primary = {q: (d1[q] + d2[q]) / 2 for q in complete}
    inter = {q: d2[q] - d1[q] for q in complete}
    sel = {"all": complete, "polymarket": [q for q in complete if src[q] == "polymarket"],
           "series": [q for q in complete if src[q] == "series"], "without flagged": [q for q in complete if q not in flagged]}
    rows = []
    for label, vals in ((f"primary ½·[({t1} − {c1}) + ({t2} − {c2})]", primary), (f"interaction ({t2} − {c2}) − ({t1} − {c1})", inter),
                        (f"{t1} − {c1}", d1), (f"{t2} − {c2}", d2)):
        for part, qids in sel.items():
            rows.append(compare_values(label, vals, qids, cl, 0.95, reps, exp.seed).line(part).replace(" − A |", " |", 1))
    out = ["| Questions | Difference | N | Clusters | Mean | CI | MDE |", "|---|---|---|---|---|---|---|", *rows, ""]
    p = compare_values("primary", primary, complete, cl, 0.95, reps, exp.seed)
    verdict = ("CI above 0: write the frontier-shadow card (Christian decides the spend)" if p.lo > 0 else
               "CI wholly below 0: killed (note it in the choices ledger, B-52)" if p.hi < 0 else
               "inconclusive" + (f"; mean > 0, so one more look at {exp.second_look_n}" if p.mean > 0 and exp.second_look_n else ""))
    out += [f"**Primary: mean {p.mean:+.2f}, 95% CI [{p.lo:+.2f}, {p.hi:+.2f}], N {p.n}: {verdict}.** Read only if EXP-017's A/A and "
            "sensitivity checks passed.", "", "Per model (each model's own answers, high − medium, both research levels; descriptive):", "",
            "| Model | N | Mean | 95% CI |", "|---|---|---|---|"]
    models = sorted({m for a in members for q in members[a] for m in members[a][q]})
    for m in models:
        vals = {}
        for q in complete:
            pairs = [(members[t][q].get(m), members[c][q].get(m)) for t, c in exp.contrast]
            if all(x is not None and y is not None for x, y in pairs):
                vals[q] = sum(x - y for x, y in pairs) / len(pairs)
        qs = sorted(vals)
        c = compare_values(m, vals, qs, cl, 0.95, reps, exp.seed)
        out.append(f"| {m} | {c.n} | {c.mean:+.2f} | [{c.lo:+.2f}, {c.hi:+.2f}] |")
    out += ["", "Per arm (means per question):", "", "| Arm | Cut off | Failed | Unparsed | Output tokens | Max seconds | $ |",
            "|---|---|---|---|---|---|---|"]
    for a in exp.arms:
        st = [stats[a][q] for q in complete if q in stats[a]]
        if st:
            mean = {k: float(np.mean([s[k] for s in st])) for k in st[0]}
            out.append(f"| {a} | {mean['cut_off']:.1%} | {mean['empty_or_failed']:.1%} | {mean['unparsed']:.1%} | "
                       f"{mean['tokens_out']:.0f} | {mean['seconds']:.0f} | {mean['usd']:.4f} |")
    return out


def look(exp: Experiment, root: Path, now: datetime, builder: Callable[[], Any] = live_builder, reps: int = 10_000
         ) -> tuple[str, bool]:
    """(markdown, ok). ok = False when the look is refused (too early), so a script can't mistake counts for a result."""
    picks = [p for p in _rows(root / "picks.jsonl") if exp.name in (p.get("experiments") or [p.get("experiment")])]
    outs = {int(r["question_id"]): r for r in _rows(root / "outcomes.jsonl")}
    recs = load_records(root)
    cache: dict[str, Any] = {}

    def get_builder():
        if "b" not in cache:
            cache["b"] = builder()
        return cache["b"]

    why_out: Counter = Counter()
    scores: dict[str, dict[int, float]] = {a: {} for a in exp.arms}
    members: dict[str, dict[int, dict[str, float]]] = {a: {} for a in exp.arms}
    stats: dict[str, dict[int, dict[str, float]]] = {a: {} for a in exp.arms}
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
            if exp.contrast:
                members[a][q] = member_scores(rec, o["outcome"], get_builder)
                stats[a][q] = arm_stats(rec)
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
    if exp.contrast:
        return "\n".join(head + [""] + contrast_section(exp, scores, complete, cl, src, flagged, members, stats, reps)), True
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
