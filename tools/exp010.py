"""EXP-010 entry point (B-68): `evals/exp010.py` with the live CDF builder plugged in.

The CDF the bot submits for a numeric/discrete question is built in the live adapter: `main.to_prediction` (fit the percentiles to
the range Metaculus accepts, break ties, forecasting-tools' NumericDistribution.from_question) and `get_cdf()`. ADR-0005 keeps the
adapter out of evals/ (tests/test_hygiene.py), so this file is the one place EXP-010 imports it. It is imported, not copied, and
nothing in main.py or forecast_engine/ is changed: what is scored is exactly what live would submit.

    poetry run python tools/exp010.py --count                     # resolved numeric/discrete forecasts so far (gate: 40 questions)
    poetry run python tools/exp010.py --fetch-scaling             # store each new question's Metaculus axis (needs METACULUS_TOKEN)
    poetry run python tools/exp010.py --card docs/experiments/EXP-010-numeric-range-sharpening.md     # the scored run
    poetry run python tools/exp010.py --validate                  # offline self-check on synthetic data (~10 s)
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


def check_offline(tmp: Path, n_questions: int = 80, reps: int = 2000, builder: exp010.CdfBuilder = LIVE) -> list[vh.Result]:
    """Four checks, in the harness's format: s = 1.0 reproduces live exactly; sharpened CDFs are valid; a too-wide forecaster is
    detected; a calibrated one is not sharpened."""
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

    def too_wide():
        an, _ = exp010.synthetic_analysis(builder, tmp / "wide", n_questions, true_s=0.6, seed=21, reps=reps)
        c, chosen = an.comparison, [f.chosen_s for f in an.folds]
        ok = all(s < 1.0 for s in chosen) and c.ci_low > 0
        return ok, (f"too-wide forecaster (truth 0.6x as wide) detected: chosen s per fold {', '.join(f'{s:g}' for s in chosen)}, "
                    f"Δ {c.mean_diff:.2f} points, CI [{c.ci_low:.2f}, {c.ci_high:.2f}]")

    out.append(vh._sub(C5, "too wide detected", too_wide))

    def calibrated():
        an, _ = exp010.synthetic_analysis(builder, tmp / "cal", n_questions, true_s=1.0, seed=22, reps=reps)
        c = an.comparison
        ok = an.decision.verdict != "adopt" and not c.ci_low > 0
        return ok, (f"calibrated forecaster not sharpened: verdict {an.decision.verdict}, chosen s per fold "
                    f"{', '.join(f'{f.chosen_s:g}' for f in an.folds)}, CI [{c.ci_low:.2f}, {c.ci_high:.2f}]")

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
