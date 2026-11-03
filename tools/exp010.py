"""EXP-010 entry point (B-68): `evals/exp010.py` with the live CDF builder plugged in.

The CDF the bot submits for a numeric/discrete question is built in the live adapter: `main.to_prediction` (fit the percentiles to
the range Metaculus accepts, break ties, forecasting-tools' NumericDistribution.from_question) and `get_cdf()`. ADR-0005 keeps the
adapter out of evals/ (tests/test_hygiene.py), so this file is the one place EXP-010 imports it. It is imported, not copied, and
nothing in main.py or forecast_engine/ is changed: what is scored is exactly what live would submit.

    poetry run python tools/exp010.py --count                     # resolved numeric/discrete forecasts so far (gate: 40 questions)
    poetry run python tools/exp010.py --fetch-scaling             # store each new question's Metaculus axis (needs METACULUS_TOKEN)
    poetry run python tools/exp010.py --card docs/experiments/EXP-010-numeric-range-sharpening.md     # the scored run
    poetry run python tools/exp010.py --validate                  # offline self-check on synthetic data (~15 s)
    poetry run python tools/exp010.py --power                     # synthetic power check (~15 s)

Every other flag is evals/exp010.py's (`--help`).
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import main as live  # noqa: E402  (the live adapter: to_prediction is the submission step)
from forecasting_tools import DiscreteQuestion, NumericQuestion  # noqa: E402
from forecasting_tools.data_models.numeric_report import NumericDefaults  # noqa: E402

from evals import exp010  # noqa: E402
from evals import validate_harness as vh  # noqa: E402

C5 = "EXP-010 tool"


class LiveCdf:
    """`exp010.CdfBuilder` backed by the live adapter."""

    def question(self, axis: exp010.Axis) -> NumericQuestion:
        """The forecasting-tools question object the adapter had when it built the CDF (no text carried over)."""
        cls = DiscreteQuestion if axis.question_type == "discrete" else NumericQuestion
        return cls(
            question_text="",
            id_of_question=axis.question_id,
            id_of_post=axis.post_id,
            lower_bound=axis.lower_bound,
            upper_bound=axis.upper_bound,
            open_lower_bound=axis.open_lower_bound,
            open_upper_bound=axis.open_upper_bound,
            zero_point=axis.zero_point,
            cdf_size=axis.cdf_size,
            nominal_lower_bound=axis.nominal_lower_bound,
            nominal_upper_bound=axis.nominal_upper_bound,
        )

    def build(self, axis: exp010.Axis, points: list[tuple[float, float]]) -> list[float]:
        return [p.percentile for p in live.to_prediction(self.question(axis), points).get_cdf()]

    def max_step(self, cdf_size: int) -> float:
        return NumericDefaults.get_max_pmf_value(cdf_size)


LIVE = LiveCdf()


# --------------------------------------------------------------------------- offline self-check (protocol §7, T13)
def validation_questions(seed: int = 11) -> list[tuple[str, exp010.Axis, list[tuple[float, float]]]]:
    """Made-up numeric (linear, open and closed bounds), log-scaled and discrete questions with an aggregate each."""
    rng = np.random.default_rng(seed)
    out = []
    for i, (kind, lo_open, hi_open) in enumerate([("numeric", False, False), ("numeric", True, True), ("numeric", True, False),
                                                  ("log", False, True), ("discrete", False, False), ("discrete", False, False)]):
        snap, sc = exp010.synthetic_question(kind, 70_000 + i, 70_000 + i, rng)
        if kind == "numeric":
            snap.update(open_lower_bound=lo_open, open_upper_bound=hi_open)
        pts, _, _ = exp010.synthetic_points(snap, sc, rng)
        out.append((kind, exp010.axis_from(snap, sc), json.loads(json.dumps(pts))))  # JSON round trip, as in a record
    return out


def tail_edge_questions() -> list[tuple[str, exp010.Axis, list[tuple[float, float]]]]:
    """Made-up questions whose arm B tails land beyond a bound: a closed lower bound (P1 below it, clamped by the live code), open
    bounds with a wide forecast (P1/P99 past the 1.9x clamp), a log-scaled open upper bound and a discrete count near 0."""
    lin = {"question_id": 71_000, "post_id": 71_000, "question_type": "numeric", "lower_bound": 0.0, "upper_bound": 100.0,
           "open_lower_bound": False, "open_upper_bound": False}
    sc = {"zero_point": None, "inbound_outcome_count": 200}
    near_lo = [(0.1, 3.0), (0.2, 6.0), (0.4, 10.0), (0.6, 14.0), (0.8, 20.0), (0.9, 26.0)]
    wide = [(0.1, -100.0), (0.2, 0.0), (0.4, 40.0), (0.6, 60.0), (0.8, 100.0), (0.9, 200.0)]
    log = {"question_id": 71_002, "post_id": 71_002, "question_type": "numeric", "lower_bound": 1.0, "upper_bound": 10_000.0,
           "open_lower_bound": False, "open_upper_bound": True}
    log_pts = [(0.1, 30.0), (0.2, 100.0), (0.4, 400.0), (0.6, 900.0), (0.8, 3000.0), (0.9, 8000.0)]
    disc = {"question_id": 71_003, "post_id": 71_003, "question_type": "discrete", "lower_bound": -0.5, "upper_bound": 10.5,
            "open_lower_bound": False, "open_upper_bound": False, "nominal_lower_bound": 0.0, "nominal_upper_bound": 10.0}
    disc_pts = [(0.1, 0.0), (0.2, 1.0), (0.4, 2.0), (0.6, 2.0), (0.8, 4.0), (0.9, 6.0)]
    return [
        ("numeric, closed bounds, P1 below the lower bound", exp010.axis_from(lin, sc), near_lo),
        ("numeric, open bounds, P1/P99 past the 1.9x clamp",
         exp010.axis_from(lin | {"question_id": 71_001, "open_lower_bound": True, "open_upper_bound": True}, sc), wide),
        ("log-scaled, open upper bound", exp010.axis_from(log, {"zero_point": 0.0, "inbound_outcome_count": 200}), log_pts),
        ("discrete count near 0", exp010.axis_from(disc, {"zero_point": None, "inbound_outcome_count": 11}), disc_pts),
    ]


def check_offline(tmp: Path, n_questions: int = 80, reps: int = 2000, builder: exp010.CdfBuilder = LIVE) -> list[vh.Result]:
    """Five checks, in the harness's format: s = 1.0 reproduces live exactly; sharpened CDFs are valid; arm B without its tails at
    s = 1.0 reproduces live and arm B's CDFs are valid; a too-wide forecaster is detected; a calibrated one is not sharpened by
    either arm."""
    out: list[vh.Result] = []
    questions = validation_questions()

    def reproduces():
        bad = []
        for kind, q, pts in questions:
            direct = [p.percentile for p in live.to_prediction(LIVE.question(q), pts).get_cdf()]
            if exp010.sharpen(pts, 1.0, q) != [tuple(x) for x in pts] or exp010.submitted_cdf(builder, q, pts, 1.0) != direct:
                bad.append(f"{kind} (open bounds {q.open_lower_bound}/{q.open_upper_bound}, {q.cdf_size} points)")
        return not bad, ("s = 1.0 gives back exactly the CDF main.to_prediction submits on "
                         f"{len(questions) - len(bad)}/{len(questions)} questions (linear, log-scaled, discrete)"
                         + (f"; DIFFERS: {', '.join(bad)}" if bad else ""))

    out.append(vh._sub(C5, "reproduces live", reproduces))

    def valid():
        bad = [f"{kind} s={s:g}: {'; '.join(pr)}" for kind, q, pts in questions for s in exp010.GRID
               if (pr := exp010.cdf_problems(exp010.submitted_cdf(builder, q, pts, s), q, builder.max_step(q.cdf_size)))]
        return not bad, f"every sharpened CDF within Metaculus's limits ({len(questions) * len(exp010.GRID)} CDFs)" + (
            f"; INVALID: {bad[:3]}" if bad else "")

    out.append(vh._sub(C5, "valid CDFs", valid))

    def arm_b():
        qs = questions + tail_edge_questions()
        same = [kind for kind, q, pts in questions
                if exp010.arm_points(pts, 1.0, q, "B", tails=False) != [tuple(x) for x in pts]
                or exp010.submitted_cdf(builder, q, pts, 1.0, "B", tails=False)
                != [p.percentile for p in live.to_prediction(LIVE.question(q), pts).get_cdf()]]
        bad = []
        for kind, q, pts in qs:
            for s in exp010.GRID:
                try:
                    c = exp010.submitted_cdf(builder, q, pts, s, "B")
                except Exception as ex:  # noqa: BLE001
                    bad.append(f"{kind} s={s:g}: conversion failed ({type(ex).__name__})")
                    continue
                if pr := exp010.cdf_problems(c, q, builder.max_step(q.cdf_size)):
                    bad.append(f"{kind} s={s:g}: {'; '.join(pr)}")
        ok = not same and not bad
        return ok, (f"arm B at s = 1.0 without its tails gives back the live CDF on {len(questions) - len(same)}/{len(questions)} "
                    f"questions; every arm B CDF within Metaculus's limits ({len(qs) * len(exp010.GRID)} CDFs, incl. "
                    f"{len(qs) - len(questions)} with tails beyond a bound)"
                    + (f"; DIFFERS: {', '.join(same)}" if same else "") + (f"; INVALID: {bad[:3]}" if bad else ""))

    out.append(vh._sub(C5, "arm B reproduces live and stays valid", arm_b))

    def too_wide():
        an, _ = exp010.synthetic_analysis(builder, tmp / "wide", n_questions, true_s=0.6, seed=21, reps=reps)
        c, chosen = an.comparison, [f.chosen_s for f in an.folds]
        ok = all(s < 1.0 for s in chosen) and c.ci_low > 0
        return ok, (f"too-wide forecaster (truth 0.6x as wide) detected: chosen s per fold {', '.join(f'{s:g}' for s in chosen)}, "
                    f"Δ {c.mean_diff:.2f} points, CI [{c.ci_low:.2f}, {c.ci_high:.2f}]")

    out.append(vh._sub(C5, "too wide detected", too_wide))

    def calibrated():
        an, _ = exp010.synthetic_analysis(builder, tmp / "cal", n_questions, true_s=1.0, seed=22, reps=reps)
        ok = all(r.decision.verdict != "adopt" and not r.comparison.ci_low > 0 for r in an.arms.values())
        return ok, ("calibrated forecaster kept by both arms: " + "; ".join(
            f"arm {a} verdict {r.decision.verdict}, chosen s per fold {', '.join(f'{f.chosen_s:g}' for f in r.folds)}, "
            f"{r.comparison.level:.1%} CI [{r.comparison.ci_low:.2f}, {r.comparison.ci_high:.2f}]" for a, r in an.arms.items()))

    out.append(vh._sub(C5, "calibrated kept", calibrated))
    return out


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--validate" in args:
        with tempfile.TemporaryDirectory(prefix="exp010-") as d:
            text, code = vh.render(check_offline(Path(d)), "offline")
        sys.stdout.write(text.replace("B-32 harness validation", "EXP-010 tool validation", 1).replace("HARNESS", "TOOL"))
        return code
    return exp010.main(args, builder=LIVE)


if __name__ == "__main__":
    raise SystemExit(main())
