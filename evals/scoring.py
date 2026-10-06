"""Scorer (B-29, evaluation protocol §4, T10): pure functions, no I/O.

Conventions
- Probabilities are of the event as asked (binary: P(YES)). Outcomes are 1/0 for binary, an option index for
  multiple choice, a value for numeric questions.
- Logs are natural unless a function says otherwise. Probabilities are floored at `EPS` before any log so a
  forecast of exactly 0 gives a large finite penalty instead of -inf (T10). Metaculus itself only accepts
  forecasts in [0.001, 0.999]; pass `eps=0.001` to mimic that.
- "Baseline" scores follow Metaculus's public scoring code (scoring/score_math.py in github.com/Metaculus/metaculus,
  and https://www.metaculus.com/help/scores-faq/): 0 = same as a no-information forecast, 100 = perfect.
  Binary and multiple choice: 100 * ln(N * p_outcome) / ln(N) (N = 2 for binary, which equals 100 * (log2 p + 1)).
  Continuous: 100 * ln(pmf[bucket] / baseline) / 2 on the 202-bucket pmf derived from the 201-point CDF, where the
  baseline is 0.05 for the two open-bound buckets and (1 - 0.05 * open_bounds) / 200 for the 200 inner buckets.
- Numeric CDFs are the 201-point CDFs the Metaculus API uses (`NUM_CDF_POINTS`), values in [0, 1], non-decreasing.
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np

EPS = 1e-6
NUM_CDF_POINTS = 201
OPEN_BOUND_MASS = 0.05


def _clip(p: float, eps: float = EPS) -> float:
    return min(max(float(p), eps), 1.0 - eps)


def _check_binary_outcome(outcome: int | bool) -> int:
    if outcome not in (0, 1, True, False):
        raise ValueError(f"binary outcome must be 0 or 1, got {outcome!r}")
    return int(outcome)


# --------------------------------------------------------------------------- binary
def binary_log_score(p: float, outcome: int | bool, eps: float = EPS) -> float:
    """ln of the probability given to what happened. Higher is better; 0 is perfect."""
    o = _check_binary_outcome(outcome)
    p = _clip(p, eps)
    return math.log(p if o == 1 else 1.0 - p)


def binary_brier(p: float, outcome: int | bool) -> float:
    """(p - o)^2. Lower is better."""
    o = _check_binary_outcome(outcome)
    if not 0.0 <= p <= 1.0:
        raise ValueError(f"probability out of range: {p}")
    return (float(p) - o) ** 2


def binary_baseline_score(p: float, outcome: int | bool, eps: float = EPS) -> float:
    """Metaculus baseline score for a binary question: 100 * (log2(p_outcome) + 1)."""
    return mc_baseline_score([1.0 - p, p], _check_binary_outcome(outcome), eps)


# --------------------------------------------------------------------------- multiple choice
def _check_pmf(probs: Sequence[float], outcome_index: int) -> np.ndarray:
    arr = np.asarray(probs, dtype=float)
    if arr.ndim != 1 or arr.size < 2:
        raise ValueError("need a 1-D probability vector with at least 2 options")
    if np.any(arr < 0):
        raise ValueError("negative probability")
    if not math.isclose(float(arr.sum()), 1.0, abs_tol=1e-6):
        raise ValueError(f"probabilities must sum to 1, got {arr.sum()}")
    if not 0 <= outcome_index < arr.size:
        raise ValueError(f"outcome index {outcome_index} outside 0..{arr.size - 1}")
    return arr


def mc_log_score(probs: Sequence[float], outcome_index: int, eps: float = EPS) -> float:
    arr = _check_pmf(probs, outcome_index)
    return math.log(max(float(arr[outcome_index]), eps))


def mc_brier(probs: Sequence[float], outcome_index: int) -> float:
    """Multi-category Brier: sum over options of (p_i - 1[i == outcome])^2 (range 0..2)."""
    arr = _check_pmf(probs, outcome_index)
    target = np.zeros_like(arr)
    target[outcome_index] = 1.0
    return float(np.sum((arr - target) ** 2))


def mc_baseline_score(probs: Sequence[float], outcome_index: int, eps: float = EPS) -> float:
    """100 * ln(N * p_outcome) / ln(N); 0 for the uniform forecast, 100 for a perfect one."""
    arr = _check_pmf(probs, outcome_index)
    n = arr.size
    return 100.0 * math.log(max(float(arr[outcome_index]), eps) * n) / math.log(n)


# --------------------------------------------------------------------------- numeric
def _check_cdf(cdf: Sequence[float]) -> np.ndarray:
    arr = np.asarray(cdf, dtype=float)
    if arr.shape != (NUM_CDF_POINTS,):
        raise ValueError(f"CDF must have {NUM_CDF_POINTS} points, got shape {arr.shape}")
    if np.any(np.isnan(arr)) or arr.min() < -1e-9 or arr.max() > 1 + 1e-9:
        raise ValueError("CDF values must lie in [0, 1]")
    if np.any(np.diff(arr) < -1e-9):
        raise ValueError("CDF must be non-decreasing")
    return np.clip(arr, 0.0, 1.0)


def cdf_to_pmf(cdf: Sequence[float]) -> np.ndarray:
    """201-point CDF -> 202 buckets: [mass below range, 200 inner bins, mass above range]."""
    c = _check_cdf(cdf)
    return np.concatenate([[c[0]], np.diff(c), [1.0 - c[-1]]])


def resolution_bucket(outcome_scaled: float) -> int:
    """Bucket of the 202-bucket pmf for an outcome expressed as a fraction of the range (0 = lower bound, 1 = upper).

    < 0 -> bucket 0 (below range), > 1 -> bucket 201 (above range), otherwise inner bin 1..200.
    """
    if outcome_scaled < 0:
        return 0
    if outcome_scaled > 1:
        return NUM_CDF_POINTS  # 201
    return min(int(outcome_scaled * (NUM_CDF_POINTS - 1)) + 1, NUM_CDF_POINTS - 1)


def numeric_baseline_score(
    cdf: Sequence[float],
    outcome_scaled: float,
    open_lower: bool = False,
    open_upper: bool = False,
    eps: float = EPS,
) -> float:
    """Metaculus continuous baseline score: 100 * ln(pmf[bucket] / baseline) / 2.

    `outcome_scaled` is the outcome mapped to [0, 1] over the question range (log-scale questions: map with the
    question's own log transform first). On a question with a closed bound the outcome cannot fall outside it.
    """
    pmf = cdf_to_pmf(cdf)
    bucket = resolution_bucket(outcome_scaled)
    if bucket == 0 and not open_lower or bucket == NUM_CDF_POINTS and not open_upper:
        raise ValueError("outcome is outside a closed bound")
    if bucket in (0, NUM_CDF_POINTS):
        baseline = OPEN_BOUND_MASS
    else:
        baseline = (1.0 - OPEN_BOUND_MASS * (int(open_lower) + int(open_upper))) / (NUM_CDF_POINTS - 1)
    return 100.0 * math.log(max(float(pmf[bucket]), eps) / baseline) / 2.0


def numeric_log_score(cdf: Sequence[float], outcome_scaled: float, eps: float = EPS) -> float:
    """ln of the pmf mass in the bucket the outcome fell into (the discretised log score on the CDF)."""
    pmf = cdf_to_pmf(cdf)
    return math.log(max(float(pmf[resolution_bucket(outcome_scaled)]), eps))


def numeric_crps(cdf: Sequence[float], outcome_scaled: float, x_grid: Sequence[float] | None = None) -> float:
    """Continuous ranked probability score, exact for a piecewise-linear CDF. Lower is better.

    Integral of (F(x) - 1[x >= y])^2 dx over the question range. `x_grid` are the 201 locations of the CDF points in
    the units the caller wants the score in (default: the scaled range [0, 1], so the CRPS is a fraction of the range).
    `outcome_scaled` is on the same axis as `x_grid` (default [0, 1]). Outcomes beyond the range are clipped to it and
    mass in open-bound buckets is not counted, so the score is bounded on the range.
    """
    f = _check_cdf(cdf)
    x = np.linspace(0.0, 1.0, NUM_CDF_POINTS) if x_grid is None else np.asarray(x_grid, dtype=float)
    if x.shape != f.shape or np.any(np.diff(x) <= 0):
        raise ValueError("x_grid must be strictly increasing with one value per CDF point")
    y = float(min(max(outcome_scaled, x[0]), x[-1]))
    total = 0.0
    for i in range(len(x) - 1):
        a, b, fa, fb = x[i], x[i + 1], f[i], f[i + 1]
        if y <= a:
            pieces = [(a, b, fa, fb, 1.0)]
        elif y >= b:
            pieces = [(a, b, fa, fb, 0.0)]
        else:
            fy = fa + (fb - fa) * (y - a) / (b - a)
            pieces = [(a, y, fa, fy, 0.0), (y, b, fy, fb, 1.0)]
        for lo, hi, flo, fhi, target in pieces:
            u, v = flo - target, fhi - target
            total += (hi - lo) * (u * u + u * v + v * v) / 3.0
    return float(total)


# --------------------------------------------------------------------------- calibration
def calibration_table(probs: Sequence[float], outcomes: Sequence[int], n_bins: int = 10) -> list[dict]:
    """Reliability table over equal-width bins of P(YES). Empty bins are omitted."""
    p = np.asarray(probs, dtype=float)
    o = np.asarray(outcomes, dtype=float)
    if p.shape != o.shape or p.ndim != 1:
        raise ValueError("probs and outcomes must be 1-D and the same length")
    if np.any((p < 0) | (p > 1)) or not np.all(np.isin(o, (0, 1))):
        raise ValueError("probs must be in [0,1] and outcomes 0/1")
    idx = np.minimum((p * n_bins).astype(int), n_bins - 1)  # p == 1 goes in the top bin
    rows = []
    for b in range(n_bins):
        mask = idx == b
        if mask.any():
            rows.append(
                {
                    "bin": b,
                    "lo": b / n_bins,
                    "hi": (b + 1) / n_bins,
                    "n": int(mask.sum()),
                    "mean_forecast": float(p[mask].mean()),
                    "observed_rate": float(o[mask].mean()),
                }
            )
    return rows


def expected_calibration_error(probs: Sequence[float], outcomes: Sequence[int], n_bins: int = 10) -> float:
    """Sample-weighted mean |mean forecast - observed rate| across bins."""
    rows = calibration_table(probs, outcomes, n_bins)
    n = sum(r["n"] for r in rows)
    if n == 0:
        raise ValueError("no forecasts")
    return sum(r["n"] * abs(r["mean_forecast"] - r["observed_rate"]) for r in rows) / n


def sharpness(probs: Sequence[float]) -> float:
    """Variance of the forecasts around their own mean: how far from a constant forecast they range."""
    return float(np.var(np.asarray(probs, dtype=float)))
