"""Tests for evals/scoring.py (B-29, protocol T10).

Golden values are derived by hand from the formulas in Metaculus's public scoring code
(scoring/score_math.py, github.com/Metaculus/metaculus) and https://www.metaculus.com/help/scores-faq/.
The FAQ's own worked examples were not reachable when this was written (HTTP 403), so these are NOT
copies of published example numbers: replace/add them if the FAQ examples are obtained.
"""
import math

import numpy as np
import pytest

from evals import scoring as s


# ---- golden: binary
def test_binary_baseline_golden():
    assert s.binary_baseline_score(0.5, 1) == pytest.approx(0.0)
    assert s.binary_baseline_score(1.0, 1, eps=1e-12) == pytest.approx(100.0, abs=1e-4)
    # 100 * (log2(0.8) + 1) = 67.8072
    assert s.binary_baseline_score(0.8, 1) == pytest.approx(67.8072, abs=1e-3)
    # NO outcome uses 1 - p = 0.2: 100 * (log2(0.2) + 1) = -132.1928
    assert s.binary_baseline_score(0.8, 0) == pytest.approx(-132.1928, abs=1e-3)


def test_binary_log_and_brier_golden():
    assert s.binary_log_score(0.8, 1) == pytest.approx(math.log(0.8))
    assert s.binary_log_score(0.8, 0) == pytest.approx(math.log(0.2))
    assert s.binary_brier(0.8, 1) == pytest.approx(0.04)
    assert s.binary_brier(0.8, 0) == pytest.approx(0.64)
    assert s.binary_brier(0.5, 1) == pytest.approx(0.25)


# ---- golden: multiple choice
def test_mc_golden():
    assert s.mc_baseline_score([0.25] * 4, 2) == pytest.approx(0.0)
    # 100 * ln(4 * 0.7) / ln(4) = 74.2713
    assert s.mc_baseline_score([0.7, 0.1, 0.1, 0.1], 0) == pytest.approx(74.2713, abs=1e-3)
    assert s.mc_log_score([0.7, 0.1, 0.1, 0.1], 1) == pytest.approx(math.log(0.1))
    assert s.mc_brier([0.7, 0.1, 0.1, 0.1], 0) == pytest.approx(0.09 + 3 * 0.01)
    # binary is the N = 2 case of the multiple-choice formula
    assert s.binary_baseline_score(0.3, 1) == pytest.approx(s.mc_baseline_score([0.7, 0.3], 1))


# ---- golden: numeric
def uniform_cdf():
    return np.linspace(0, 1, s.NUM_CDF_POINTS)


def test_numeric_uniform_forecast_scores_zero_baseline():
    cdf = uniform_cdf()
    for y in (0.0, 0.003, 0.5, 0.999, 1.0):
        assert s.numeric_baseline_score(cdf, y) == pytest.approx(0.0, abs=1e-9)


def test_numeric_baseline_open_bounds_golden():
    # Open bounds, 0.05 mass each side, uniform 0.9 inside: every bucket equals its baseline -> 0.
    cdf = 0.05 + 0.9 * np.linspace(0, 1, s.NUM_CDF_POINTS)
    for y in (-1.0, 0.4, 2.0):
        assert s.numeric_baseline_score(cdf, y, open_lower=True, open_upper=True) == pytest.approx(0.0, abs=1e-9)
    # Concentrated: 0.5 mass in one inner bin -> 100 * ln(0.5 / (0.9/200)) / 2
    pmf_inner = np.full(200, 0.4 / 199)
    pmf_inner[99] = 0.5
    cdf2 = 0.05 + np.concatenate([[0], np.cumsum(pmf_inner)])
    got = s.numeric_baseline_score(cdf2, 99.5 / 200 + 1e-9, open_lower=True, open_upper=True)
    assert got == pytest.approx(100 * math.log(0.5 / (0.9 / 200)) / 2, abs=1e-6)


def test_resolution_bucket_edges():
    assert s.resolution_bucket(-0.01) == 0
    assert s.resolution_bucket(0.0) == 1
    assert s.resolution_bucket(1.0) == 200  # on the upper bound is still an inner bin
    assert s.resolution_bucket(1.01) == 201


def test_numeric_out_of_bounds_requires_open_bound():
    with pytest.raises(ValueError):
        s.numeric_baseline_score(uniform_cdf(), -0.1)
    with pytest.raises(ValueError):
        s.numeric_baseline_score(uniform_cdf(), 1.1, open_lower=True)


def test_cdf_validation():
    with pytest.raises(ValueError):
        s.cdf_to_pmf(np.linspace(0, 1, 200))
    bad = uniform_cdf()
    bad[100] = 0.2
    with pytest.raises(ValueError):
        s.cdf_to_pmf(bad)


def test_crps_golden():
    # Uniform on [0,1], outcome y: integral = (y^3 + (1-y)^3) / 3
    for y in (0.0, 0.25, 0.5, 1.0):
        assert s.numeric_crps(uniform_cdf(), y) == pytest.approx((y**3 + (1 - y) ** 3) / 3, abs=1e-9)
    # A step CDF on the 201-point grid ramps linearly over one 0.005-wide bin: 0.005 / 3 even when y is at the step
    step = (np.linspace(0, 1, s.NUM_CDF_POINTS) >= 0.5).astype(float)
    assert s.numeric_crps(step, 0.5) == pytest.approx(0.005 / 3, abs=1e-9)
    # Scaling the axis scales the score
    xs = np.linspace(0, 100, s.NUM_CDF_POINTS)
    assert s.numeric_crps(uniform_cdf(), 50, x_grid=xs) == pytest.approx(100 * 0.25 / 3, abs=1e-6)


def test_crps_clips_outcome_to_range():
    assert s.numeric_crps(uniform_cdf(), 5.0) == s.numeric_crps(uniform_cdf(), 1.0)


# ---- properties
@pytest.mark.parametrize("q", [0.05, 0.3, 0.5, 0.77, 0.95])
def test_binary_scores_are_proper(q):
    """If the event happens with probability q, the expected score is best at reporting p = q."""
    grid = np.linspace(0.01, 0.99, 99)

    def expected(fn, p):
        return q * fn(p, 1) + (1 - q) * fn(p, 0)

    log_best = grid[np.argmax([expected(s.binary_log_score, p) for p in grid])]
    base_best = grid[np.argmax([expected(s.binary_baseline_score, p) for p in grid])]
    brier_best = grid[np.argmin([expected(s.binary_brier, p) for p in grid])]
    for best in (log_best, base_best, brier_best):
        assert best == pytest.approx(q, abs=0.0101)


def test_mc_log_score_is_proper():
    q = np.array([0.6, 0.3, 0.1])
    honest = sum(q[i] * s.mc_baseline_score(q, i) for i in range(3))
    rng = np.random.default_rng(0)
    for _ in range(200):
        other = rng.dirichlet(np.ones(3))
        assert sum(q[i] * s.mc_baseline_score(other, i) for i in range(3)) <= honest + 1e-9


def test_numeric_score_is_proper_on_pmf():
    rng = np.random.default_rng(1)
    q = rng.dirichlet(np.ones(200))
    cdf_q = np.concatenate([[0], np.cumsum(q)])
    honest = sum(q[i] * s.numeric_log_score(cdf_q, (i + 0.5) / 200) for i in range(200))
    for _ in range(20):
        r = rng.dirichlet(np.ones(200))
        cdf_r = np.concatenate([[0], np.cumsum(r)])
        assert sum(q[i] * s.numeric_log_score(cdf_r, (i + 0.5) / 200) for i in range(200)) <= honest + 1e-9


def test_crps_prefers_the_sharper_correct_forecast():
    y = 0.6
    wide = uniform_cdf()
    narrow = np.clip((np.linspace(0, 1, s.NUM_CDF_POINTS) - 0.5) / 0.2, 0, 1)  # uniform on [0.5, 0.7]
    assert s.numeric_crps(narrow, y) < s.numeric_crps(wide, y)
    wrong = np.clip((np.linspace(0, 1, s.NUM_CDF_POINTS) - 0.1) / 0.2, 0, 1)  # uniform on [0.1, 0.3]
    assert s.numeric_crps(wrong, y) > s.numeric_crps(wide, y)


# ---- edge cases
def test_extreme_probabilities_are_finite_and_signed():
    assert s.binary_log_score(0.0, 1) == pytest.approx(math.log(s.EPS))
    assert s.binary_log_score(1.0, 0) == pytest.approx(math.log(s.EPS))
    assert s.binary_log_score(0.0, 0) == pytest.approx(math.log(1 - s.EPS))
    assert math.isfinite(s.binary_baseline_score(0.0, 1))
    assert s.binary_baseline_score(1.0, 1) <= 100.0
    assert s.binary_log_score(0.001, 1, eps=0.001) == pytest.approx(math.log(0.001))  # Metaculus-style clamp
    assert s.binary_brier(0.0, 1) == 1.0 and s.binary_brier(1.0, 1) == 0.0


def test_bad_inputs_raise():
    with pytest.raises(ValueError):
        s.binary_log_score(0.5, 2)
    with pytest.raises(ValueError):
        s.binary_brier(1.2, 1)
    with pytest.raises(ValueError):
        s.mc_baseline_score([0.5, 0.4], 0)  # does not sum to 1
    with pytest.raises(ValueError):
        s.mc_baseline_score([0.5, 0.5], 2)


# ---- calibration
def test_calibration_table_and_ece():
    p = [0.05, 0.05, 0.95, 0.95, 1.0]
    o = [0, 0, 1, 1, 1]
    rows = s.calibration_table(p, o)
    assert [r["n"] for r in rows] == [2, 3]  # p = 1.0 lands in the top bin
    assert s.expected_calibration_error(p, o) == pytest.approx((2 * 0.05 + 3 * abs(np.mean([0.95, 0.95, 1.0]) - 1)) / 5)


def test_ece_zero_when_perfectly_calibrated_and_large_when_overconfident():
    p = [0.7] * 10
    assert s.expected_calibration_error(p, [1] * 7 + [0] * 3) == pytest.approx(0.0)
    assert s.expected_calibration_error([0.9] * 10, [1] * 5 + [0] * 5) == pytest.approx(0.4)


def test_sharpness():
    assert s.sharpness([0.5, 0.5, 0.5]) == 0.0
    assert s.sharpness([0.1, 0.9]) == pytest.approx(0.16)
