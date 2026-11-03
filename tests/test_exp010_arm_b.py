"""EXP-010 arm B ("sharpen + normal-shaped P1/P99 tails") and the changed kill rule (Christian's decisions 1 and 2, 2026-11-02).

Synthetic data only: made-up questions, numbers and bounds. Offline (conftest blocks the network).
"""
import importlib.util
import json
import statistics
from collections import Counter
from pathlib import Path

import numpy as np
import pytest

from evals import exp010 as e
from evals import report, scoring
from forecast_engine import parsing

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("exp010_tool_b", ROOT / "tools" / "exp010.py")
T = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(T)
B = T.LIVE  # the live CDF builder (main.to_prediction + forecasting-tools)

LIN = {"question_id": 1, "post_id": 11, "question_type": "numeric", "lower_bound": 0.0, "upper_bound": 200.0,
       "open_lower_bound": False, "open_upper_bound": False}
LIN_SC = {"zero_point": None, "inbound_outcome_count": 200}
LOG = {"question_id": 2, "post_id": 12, "question_type": "numeric", "lower_bound": 1.0, "upper_bound": 10_000.0,
       "open_lower_bound": False, "open_upper_bound": True}
LOG_SC = {"zero_point": 0.0, "inbound_outcome_count": 200}
DISC = {"question_id": 3, "post_id": 13, "question_type": "discrete", "lower_bound": -0.5, "upper_bound": 10.5,
        "open_lower_bound": False, "open_upper_bound": False, "nominal_lower_bound": 0.0, "nominal_upper_bound": 10.0}
DISC_SC = {"zero_point": None, "inbound_outcome_count": 11}
PTS = [(0.1, 40.0), (0.2, 55.0), (0.4, 70.0), (0.6, 80.0), (0.8, 100.0), (0.9, 120.0)]
LOG_PTS = [(0.1, 20.0), (0.2, 50.0), (0.4, 90.0), (0.6, 110.0), (0.8, 300.0), (0.9, 900.0)]
DISC_PTS = [(0.1, 2.0), (0.2, 3.0), (0.4, 4.0), (0.6, 4.0), (0.8, 6.0), (0.9, 7.0)]
K = statistics.NormalDist().inv_cdf(0.01) / statistics.NormalDist().inv_cdf(0.1)  # ≈ 1.815


def q_of(snap, sc):
    return e.axis_from(snap, sc)


def live_cdf(q, pts):
    import main

    return [p.percentile for p in main.to_prediction(B.question(q), pts).get_cdf()]


def valid(c, q):
    return e.cdf_problems(c, q, B.max_step(q.cdf_size)) == []


# ---------------------------------------------------------------- decision 1: the kill rule
def _cmp(point, lo, hi, n=50, level=0.975):
    return report.Comparison(arm="x", baseline="s = 1.0", metric=e.PRIMARY.key, higher_is_better=True, n=n, n_clusters=25,
                             mean_arm=0, mean_base=0, mean_diff=point, ci_low=lo, ci_high=hi, se_boot=1.0, median_diff=point,
                             wins=1, ties=0, losses=1, sd_diff=5.0, mde=1.0, level=level)


def _folds(*s):
    return [e.Fold(i + 1, 40, 10, 5, x) for i, x in enumerate(s)]


def _era(name, point):
    return e.Group(name, 20, 20, _cmp(point, point - 5, point + 5, 20), 0.8, 0.1, 0.2)


def test_rule_has_no_kill_on_a_nonpositive_mean_but_keeps_the_ci_kill_and_conflict_kill():
    assert e.RULE.kill_if_point_at_most is None
    assert e.RULE.kill_if_ci_upper_below == 0.75 == e.KILL_THRESHOLD_POINTS
    assert e.RULE.on_conflict == "kill"


@pytest.mark.parametrize("arm", e.ARMS)
def test_negative_mean_with_ci_upper_at_least_075_is_inconclusive_not_kill(arm):
    eras = [_era("frontier", -1.0), _era("cheap", -1.0)]
    for hi in (0.75, 1.0, 6.0):
        d = e.decide(_cmp(-2.0, -9.0, hi), _folds(0.7, 0.8, 0.7, 0.7, 0.8), eras, "screen", arm)
        assert d.verdict == "inconclusive", (arm, hi)
        assert not any("mean improvement ≤" in c.condition for c in d.checks)
        assert "rerun" in d.next_step
    killed = e.decide(_cmp(-2.0, -9.0, 0.74), _folds(0.7, 0.8, 0.7, 0.7, 0.8), eras, "screen", arm)
    assert killed.verdict == "kill" and "not rerun" in killed.next_step  # only through the CI rule
    small = e.decide(_cmp(0.4, 0.1, 0.7), _folds(0.7, 0.8, 0.7, 0.7, 0.8), [_era("frontier", 1.0)], "screen", arm)
    assert small.verdict == "kill" and "on_conflict" in small.note  # adopt and kill both hold -> kill


def test_arm_b_stability_allows_s_1_and_arm_a_does_not():
    eras = [_era("frontier", 3.0)]
    ones = _folds(1.0, 1.0, 1.0, 1.0, 1.0)
    assert e.decide(_cmp(3.0, 1.0, 5.0), ones, eras, "screen", "B").verdict == "adopt"
    assert e.decide(_cmp(3.0, 1.0, 5.0), ones, eras, "screen", "A").verdict == "inconclusive"
    no_train = [e.Fold(1, 0, 10, 5, 1.0)]
    assert e.decide(_cmp(3.0, 1.0, 5.0), no_train, eras, "screen", "B").verdict == "inconclusive"
    assert e.decide(_cmp(3.0, 1.0, 5.0), _folds(0.7, 0.8, 0.7, 0.7, 0.8), [_era("frontier", -1.0)], "screen",
                    "B").verdict == "inconclusive"  # the frontier era must gain for arm B too


# ---------------------------------------------------------------- the arm B transform
def test_arm_b_tails_on_a_linear_question():
    q = q_of(LIN, LIN_SC)
    for s in (1.0, 0.7):
        sp = e.sharpen(PTS, s, q)
        out = e.arm_points(PTS, s, q, "B")
        assert out[1:-1] == sp and [p for p, _ in out] == [0.01, 0.1, 0.2, 0.4, 0.6, 0.8, 0.9, 0.99]
        m = 75.0
        assert out[0][1] == pytest.approx(m + (sp[0][1] - m) * K)
        assert out[-1][1] == pytest.approx(m + (sp[-1][1] - m) * K)
    assert e.arm_points(PTS, 1.0, q, "B")[0][1] == pytest.approx(75 - 35 * K)  # s = 1.0 still adds the tails
    assert e.arm_points(PTS, 1.0, q, "B", tails=False) == [tuple(p) for p in PTS]
    assert e.arm_points(PTS, 0.7, q, "A") == e.sharpen(PTS, 0.7, q)
    with pytest.raises(ValueError):
        e.arm_points(PTS, 1.0, q, "C")


def test_symmetric_forecast_gets_the_one_normal_through_p10_and_p90():
    q = q_of(LIN, LIN_SC)
    sym = [(0.1, 40.0), (0.2, 50.0), (0.4, 60.0), (0.6, 80.0), (0.8, 90.0), (0.9, 100.0)]
    nd = statistics.NormalDist(70.0, 30.0 / statistics.NormalDist().inv_cdf(0.9))
    out = e.arm_points(sym, 1.0, q, "B")
    assert out[0][1] == pytest.approx(nd.inv_cdf(0.01)) and out[-1][1] == pytest.approx(nd.inv_cdf(0.99))


def test_arm_b_tails_on_a_log_question_are_normal_on_the_log_axis():
    q = q_of(LOG, LOG_SC)
    for s in (1.0, 0.6):
        out = e.arm_points(LOG_PTS, s, q, "B")
        loc = [scoring.numeric_unscaled_location(v, 1.0, 10_000.0, 0.0) for _, v in out]
        m = (loc[3] + loc[4]) / 2  # P40, P60 after the inserted P1
        assert loc[0] == pytest.approx(m + (loc[1] - m) * K) and loc[-1] == pytest.approx(m + (loc[-2] - m) * K)
        assert all(v > 0 for _, v in out)  # never at or below the zero point
        assert out[-1][1] > out[-2][1] * 2  # ratio spacing: a long right tail in plain units
        c = e.submitted_cdf(B, q, LOG_PTS, s, "B")
        assert len(c) == 201 and valid(c, q)


def test_arm_b_tails_on_a_discrete_question():
    q = q_of(DISC, DISC_SC)
    for s in e.GRID:
        out = e.arm_points(DISC_PTS, s, q, "B")
        assert out[0][0] == 0.01 and out[-1][0] == 0.99 and out[0][1] < out[1][1] and out[-1][1] > out[-2][1]
        c = e.submitted_cdf(B, q, DISC_PTS, s, "B")
        assert len(c) == 12 and valid(c, q) and c[0] == 0.0 and c[-1] == 1.0
    assert e.submitted_cdf(B, q, DISC_PTS, 1.0, "B") != e.submitted_cdf(B, q, DISC_PTS, 1.0, "A")


def test_tail_beyond_a_closed_bound_is_handled_exactly_as_live():
    """P1 below a closed lower bound: our transform does not clamp it; the live builder clamps it (fit_to_bounds), as it would
    any percentile beyond a closed bound. No mass beyond the bound."""
    q = q_of(LIN, LIN_SC)
    pts = [(0.1, 6.0), (0.2, 12.0), (0.4, 20.0), (0.6, 28.0), (0.8, 40.0), (0.9, 52.0)]
    out = e.arm_points(pts, 1.0, q, "B")
    assert out[0][1] < 0.0  # beyond the bound, handed over as is
    c = e.submitted_cdf(B, q, pts, 1.0, "B")
    assert c == live_cdf(q, out)
    clamped, clipped = parsing.fit_to_bounds(out, 0.0, 200.0, False, False)
    assert clipped and clamped[0] == (0.01, 0.0)
    assert c == live_cdf(q, clamped)  # the clamp is the live one, not ours
    assert c[0] == 0.0 and c[-1] == 1.0 and valid(c, q)


def test_tails_beyond_open_bounds_keep_the_open_bound_mass_as_live():
    q = q_of(LIN | {"open_lower_bound": True, "open_upper_bound": True}, LIN_SC)
    wide = [(0.1, -200.0), (0.2, 0.0), (0.4, 80.0), (0.6, 120.0), (0.8, 200.0), (0.9, 400.0)]
    out = e.arm_points(wide, 1.0, q, "B")
    assert out[0][1] < -1.9 * 200 and out[-1][1] > 200 + 1.9 * 200  # past the 1.9x clamp on both sides
    c = e.submitted_cdf(B, q, wide, 1.0, "B")
    clamped, clipped = parsing.fit_to_bounds(out, 0.0, 200.0, True, True)
    assert clipped and c == live_cdf(q, clamped) == live_cdf(q, out)
    assert c[0] >= 0.001 and c[-1] <= 0.999 and valid(c, q)


def test_tails_inside_an_open_bound_are_kept_as_declared():
    q = q_of(LIN | {"open_upper_bound": True}, LIN_SC)
    out = e.arm_points(PTS, 1.0, q, "B")
    assert 0 < out[-1][1] < 200
    assert e.submitted_cdf(B, q, PTS, 1.0, "B") == live_cdf(q, out)


@pytest.mark.parametrize("s", e.GRID)
def test_arm_b_cdfs_stay_valid_on_every_question_shape(s):
    shapes = [(q_of(LIN, LIN_SC), PTS), (q_of(LOG, LOG_SC), LOG_PTS), (q_of(DISC, DISC_SC), DISC_PTS)]
    for lo, hi in ((True, True), (True, False), (False, True)):
        shapes.append((q_of(LIN | {"open_lower_bound": lo, "open_upper_bound": hi}, LIN_SC), PTS))
    shapes += [(q, pts) for _, q, pts in T.tail_edge_questions()]
    for q, pts in shapes:
        assert valid(e.submitted_cdf(B, q, pts, s, "B"), q), (q.question_type, q.open_lower_bound, q.open_upper_bound, s)


def test_arm_b_without_tails_at_s1_is_todays_forecast():
    for q, pts in [(q_of(LIN, LIN_SC), PTS), (q_of(LOG, LOG_SC), LOG_PTS), (q_of(DISC, DISC_SC), DISC_PTS)]:
        stored = json.loads(json.dumps(pts))
        assert e.submitted_cdf(B, q, stored, 1.0, "B", tails=False) == live_cdf(q, stored) == e.submitted_cdf(B, q, stored)


# ---------------------------------------------------------------- building and dropping: both arms on the same questions
class _FailsWithTails:
    """A builder that fails whenever the points carry arm B's P1 (stands in for a live conversion failure)."""

    def build(self, axis, points):
        if points[0][0] == 0.01 and axis.question_id % 2 == 0:
            raise ValueError("made-up conversion failure")
        return B.build(axis, points)

    def max_step(self, n):
        return B.max_step(n)


def test_a_case_arm_b_cannot_build_is_dropped_from_both_arms(tmp_path):
    paths = e.write_synthetic(tmp_path, e.synthetic_cases(B, 12, seed=3, shadow_share=0.0))
    loaded = e.load_cases(paths["runs"], paths["outcomes"], paths["scaling"])
    dropped = Counter()
    kept, cdfs = e.build_cdfs(loaded.cases, _FailsWithTails(), e.GRID, dropped)
    n_even = sum(c.question_id % 2 == 0 for c in loaded.cases)
    assert dropped == Counter({e.DROP_CONVERSION_B: n_even})
    assert {c.key for c in kept} == set(cdfs) and all(c.question_id % 2 for c in kept)
    assert all(set(cdfs[k]) == {"A", "B"} and set(cdfs[k]["B"]) == set(e.GRID) for k in cdfs)
    only_a, cdfs_a = e.build_cdfs(loaded.cases, _FailsWithTails(), e.GRID, Counter(), arms=("A",))
    assert len(only_a) == len(loaded.cases) and all(set(v) == {"A"} for v in cdfs_a.values())


def test_count_mode_with_the_builder_reports_build_drops_without_scores(tmp_path, capsys):
    paths = e.write_synthetic(tmp_path / "d", e.synthetic_cases(B, 6, seed=6, shadow_share=0.0))
    args = ["--runs-dir", str(paths["runs"]), "--outcomes", str(paths["outcomes"]), "--scaling", str(paths["scaling"]), "--count"]
    assert T.main(args) == 0
    out = capsys.readouterr().out
    assert "live CDF build check (no scores): 6 of 6 ready forecasts" in out
    assert "baseline" not in out.lower() and "Δ" not in out


# ---------------------------------------------------------------- analysis: baseline, folds, level
@pytest.fixture(scope="module")
def two_arm(tmp_path_factory):
    an, _ = e.synthetic_analysis(B, tmp_path_factory.mktemp("two"), 60, true_s=0.6, seed=12, reps=1000, truth="normal",
                                 shadow_share=0.3)
    return an


def test_both_arms_share_the_baseline_and_the_folds(two_arm):
    a, b = two_arm.arms["A"], two_arm.arms["B"]
    assert a.comparison.mean_base == pytest.approx(b.comparison.mean_base)  # both against arm A at s = 1.0
    assert a.comparison.n == b.comparison.n == two_arm.n_cases
    assert [(f.fold, f.n_train, f.n_test, f.clusters_test) for f in a.folds] == [
        (f.fold, f.n_train, f.n_test, f.clusters_test) for f in b.folds]
    assert len(b.fold_beats_today) == len(b.folds)


def test_level_is_97_5_percent_whenever_two_arms_compete(two_arm):
    assert e.ARMS == ("A", "B") and report.family_level(len(e.ARMS)) == pytest.approx(0.975)
    assert two_arm.level == pytest.approx(0.975)
    for r in two_arm.arms.values():
        assert r.comparison.level == pytest.approx(0.975) and r.decision.level == pytest.approx(0.975)
        assert all(g.comparison.level == pytest.approx(0.975) for gs in r.breakdowns.values() for g in gs)
        assert any("97.5% CI excludes 0" in c.condition for c in r.decision.checks)


def test_one_arm_runs_at_95_percent_and_confirm_at_90(tmp_path):
    an, _ = e.synthetic_analysis(B, tmp_path / "one", 40, true_s=1.0, seed=13, reps=500, arms=("B",))
    assert list(an.arms) == ["B"] and an.level == pytest.approx(0.95) and an.comparison.level == pytest.approx(0.95)
    with pytest.raises(ValueError):
        e.synthetic_analysis(B, tmp_path / "bad", 40, true_s=1.0, seed=13, reps=200, arms=("A", "A"))


def test_too_fat_tails_world_b_is_detected_and_a_is_not(tmp_path):
    """Outcomes from the normal the percentiles came from, at the forecast's own spread: the middle is right, but the live CDF's
    linear outer 10% are too fat. Arm B's normal-shaped tails fix exactly that; sharpening the middle (arm A) doesn't help.
    (Synthetic power check: arm B is found in ≈48% of trials at N = 80; N = 200 here keeps the seeded test far from the edge.)"""
    an, _ = e.synthetic_analysis(B, tmp_path, 200, true_s=1.0, seed=31, reps=2000, truth="normal")
    a, b = an.arms["A"], an.arms["B"]
    assert b.comparison.level == pytest.approx(0.975) and b.comparison.ci_low > 0
    assert not a.comparison.ci_low > 0
    assert a.decision.verdict != "adopt"


def _pool(n=80, seed=4):
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        snap, sc = e.synthetic_question(("numeric", "log", "discrete", "numeric")[i % 4], i, i, rng)
        q = e.axis_from(snap, sc)
        pts, _, _ = e.synthetic_points(snap, sc, rng)
        out.append((q, {arm: {s: e.submitted_cdf(B, q, pts, s, arm) for s in e.GRID} for arm in e.ARMS}))
    return out


def test_calibrated_world_neither_arm_wins_in_about_97_5_percent_of_trials():
    """Outcomes drawn from today's submitted CDF (calibrated by construction), fresh draws per trial; each arm at its 97.5% CI.
    A CI lower bound > 0 is necessary for adopt, so counting it is the stricter test."""
    pool, trials, neither = _pool(), 40, 0
    level = report.family_level(2)
    for t in range(trials):
        r = np.random.default_rng(300 + t)
        primary, clusters = {arm: {} for arm in e.ARMS}, {}
        for j, (q, cdfs) in enumerate(pool):
            loc = e.outcome_location(e.draw_resolution(cdfs["A"][1.0], q, r), q)
            for arm in e.ARMS:
                primary[arm][str(j)] = {s: scoring.numeric_baseline_score(c, loc, q.open_lower_bound, q.open_upper_bound,
                                                                          cdf_size=len(c)) for s, c in cdfs[arm].items()}
            clusters[str(j)] = str(j // 2)
        base = {k: primary["A"][k][1.0] for k in clusters}
        wins = []
        for arm in e.ARMS:
            cv = e.nested_cv(primary[arm], clusters, seed=t)
            c = e._comparison(primary[arm], cv.oof_s, clusters, arm=arm, reps=500, seed=t, level=level, base=base)
            assert c.level == pytest.approx(0.975)
            wins.append(c.ci_low > 0)
        neither += not any(wins)
    # ≥ 97.5% expected per arm (and ≥ 95% for the pair by Bonferroni); 37 of 40 leaves ~2% binomial room at 97.5%
    assert neither >= 37, neither


# ---------------------------------------------------------------- which arm goes live
def _result(arm, point, lo, hi, verdict="adopt", s_all=0.8):
    c = _cmp(point, lo, hi)
    d = report.Decision(arm, e.RULE, verdict, [], float("nan"), "")
    return e.ArmResult(arm, e.ARM_LABELS[arm], [], s_all, {}, c, {}, d, {})


def test_which_goes_live():
    both = {"A": _result("A", 3.0, 1.0, 5.0), "B": _result("B", 6.0, 2.0, 10.0, s_all=1.0)}
    arm, words = e.which_goes_live(both, "screen")
    assert arm == "B" and "[Christian]" in words and "larger out-of-fold mean Δ" in words and "s = 1" in words
    assert e.which_goes_live({"A": _result("A", 6.0, 2.0, 10.0), "B": _result("B", 6.0, 2.0, 10.0)}, "screen")[0] == "A"  # tie
    narrow_b = {"A": _result("A", 3.0, 1.0, 5.0), "B": _result("B", 6.0, 0.3, 10.0)}
    assert e.which_goes_live(narrow_b, "screen")[0] == "A"  # a narrow win is a screen, it doesn't go live
    none = {"A": _result("A", 3.0, -1.0, 5.0, "inconclusive"), "B": _result("B", 2.0, 0.3, 4.0)}
    arm, words = e.which_goes_live(none, "screen")
    assert arm is None and "No arm goes live" in words
    assert e.which_goes_live(both, "confirm") == (None, "")


# ---------------------------------------------------------------- report, card and CLI
def _args(paths, *extra):
    return ["--runs-dir", str(paths["runs"]), "--outcomes", str(paths["outcomes"]), "--scaling", str(paths["scaling"]), *extra]


def test_report_has_one_block_per_arm_and_fills_the_card(tmp_path, capsys):
    paths = e.write_synthetic(tmp_path / "d", e.synthetic_cases(B, 44, seed=5, true_s=0.6, shadow_share=0.5))
    card = tmp_path / "card.md"
    card.write_text("# EXP-010\n\n## Results (generated)\n## Decision\n")
    out = tmp_path / "r.md"
    assert T.main(_args(paths, "--out", str(out), "--reps", "500", "--card", str(card))) == 0
    text = out.read_text()
    for needle in ("### Arm A: sharpen", "### Arm B: sharpen + normal-shaped P1/P99 tails", "m = 2, so each arm's CI is at 97.5%",
                   "arm A at s = 1.0 (today's forecast, unchanged)", "Which arm goes live", "By era (arm A)", "By era (arm B)",
                   "By type (arm B)", "By tournament (arm B)", "Breakdowns (descriptive, not decisions)", "(97.5%)",
                   "beats today on its training part", "arm B, out-of-fold s"):
        assert needle in text, needle
    assert text.count("Decision for arm") == 2
    assert "Synthetic question" not in text
    assert "### Arm B" in card.read_text()
    printed = capsys.readouterr().out
    assert printed.count("Decision for arm") == 2 and "which arm goes live" in printed


def test_cli_arms_and_confirm_arm(tmp_path):
    paths = e.write_synthetic(tmp_path / "d", e.synthetic_cases(B, 42, seed=8, true_s=0.6))
    one = tmp_path / "one.md"
    assert T.main(_args(paths, "--arms", "A", "--out", str(one), "--reps", "300")) == 0
    text = one.read_text()
    assert "### Arm B" not in text and "m = 1, so each arm's CI is at 95%" in text
    conf = tmp_path / "conf.md"
    assert T.main(_args(paths, "--confirm-s", "1.0", "--confirm-arm", "B", "--resolved-after", "2024-01-01", "--out", str(conf),
                        "--reps", "300")) == 0
    text = conf.read_text()
    assert "Decision for arm B (sharpen + normal-shaped P1/P99 tails) at s = 1 (fixed, adopted)" in text and "(90%)" in text
    assert "### Arm A" not in text


def test_power_simulation_has_real_arm_b_rows_at_97_5_percent():
    lines = e.power_simulation(B, n_values=(12,), trials=2, pool_size=24, reps=200, seed=1)
    text = "\n".join(lines)
    assert "97.5% CI (m = 2)" in text
    rows = [ln for ln in lines if ln.startswith(("cdf ", "normal "))]
    assert len(rows) == len(e.POWER_TRUTHS) * 3
    arms = Counter(r[25:32].strip() for r in rows)  # the arm column
    assert arms == Counter({"A": len(e.POWER_TRUTHS), "B": len(e.POWER_TRUTHS), "A or B": len(e.POWER_TRUTHS)})
