from datetime import datetime, timezone

import pytest

from forecast_engine import guards

NOW = datetime(2026, 10, 3, 20, 0, tzinfo=timezone.utc)


def test_forecastable_only_open_unresolved_unclosed():
    assert guards.forecastable("open", datetime(2026, 10, 4, tzinfo=timezone.utc), None, NOW) == (True, "")
    assert not guards.forecastable("closed", None, None, NOW)[0]
    assert not guards.forecastable("resolved", None, "yes", NOW)[0]
    assert not guards.forecastable("open", None, "no", NOW)[0]
    ok, why = guards.forecastable("open", datetime(2026, 10, 3, 19, 59, tzinfo=timezone.utc), None, NOW)
    assert not ok and "closed at" in why


def test_clock_drift():
    assert guards.clock_drift_days(NOW, "Sat, 03 Oct 2026 20:05:00 GMT") < 0.01
    assert guards.clock_drift_days(NOW, "Mon, 05 Oct 2026 20:00:00 GMT") == pytest.approx(2.0)


P = [0.1, 0.2, 0.4, 0.6, 0.8, 0.9]


def _pts(vals):
    return list(zip(P, vals))


def test_numeric_flags_median_far_outside_range():
    agg = _pts([900, 950, 1000, 1100, 1200, 1300])
    assert any("far outside" in f for f in guards.numeric_flags(agg, [agg], 0, 100))
    assert guards.numeric_flags(_pts([10, 20, 40, 60, 80, 90]), [_pts([10, 20, 40, 60, 80, 90])], 0, 100) == []


def test_numeric_flags_units_disagreement():
    a = _pts([1, 2, 3, 4, 5, 6])
    b = _pts([1000, 2000, 3000, 4000, 5000, 6000])
    flags = guards.numeric_flags(a, [a, a, b], None, None)
    assert any("differ by more than" in f for f in flags)


def test_mc_options_must_match_exactly():
    guards.check_mc_options({"A": 0.5, "B": 0.5}, ["A", "B"])
    with pytest.raises(guards.GuardError):
        guards.check_mc_options({"A": 0.5, "C": 0.5}, ["A", "B"])
    with pytest.raises(guards.GuardError):
        guards.check_mc_options({"A": 1.0}, ["A", "B"])
