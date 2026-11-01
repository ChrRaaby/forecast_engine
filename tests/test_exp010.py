"""EXP-010 numeric range sharpening tool (evals/exp010.py, B-68).

Synthetic data only: made-up questions, numbers and bounds, no research text. Offline (conftest blocks the network).
"""
import importlib.util
import json
import math
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from evals import exp010 as e
from evals import report, scoring
from evals import validate_harness as vh
from forecast_engine.clock import FixedClock

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("exp010_tool", ROOT / "tools" / "exp010.py")
T = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(T)
B = T.LIVE  # the live CDF builder (main.to_prediction + forecasting-tools)

LIN = {"question_id": 1, "post_id": 11, "question_type": "numeric", "lower_bound": 0.0, "upper_bound": 200.0,
       "open_lower_bound": False, "open_upper_bound": True}
LIN_SC = {"zero_point": None, "inbound_outcome_count": 200}
LOG = {"question_id": 2, "post_id": 12, "question_type": "numeric", "lower_bound": 1.0, "upper_bound": 10_000.0,
       "open_lower_bound": False, "open_upper_bound": True}
LOG_SC = {"zero_point": 0.0, "inbound_outcome_count": 200}
DISC = {"question_id": 3, "post_id": 13, "question_type": "discrete", "lower_bound": -0.5, "upper_bound": 10.5,
        "open_lower_bound": False, "open_upper_bound": False, "nominal_lower_bound": 0.0, "nominal_upper_bound": 10.0}
DISC_SC = {"zero_point": None, "inbound_outcome_count": 11}
PTS = [(0.1, 40.0), (0.2, 55.0), (0.4, 70.0), (0.6, 80.0), (0.8, 100.0), (0.9, 120.0)]
LOG_PTS = [(0.1, 20.0), (0.2, 50.0), (0.4, 90.0), (0.6, 110.0), (0.8, 300.0), (0.9, 900.0)]
DISC_PTS = [(0.1, 2.0), (0.2, 3.0), (0.4, 4.0), (0.6, 4.0), (0.8, 6.0), (0.9, 7.0)]  # ties, as models answer counts


def q_of(snap, sc):
    return e.axis_from(snap, sc)


def live_cdf(q, pts):
    import main

    return [p.percentile for p in main.to_prediction(B.question(q), pts).get_cdf()]


def all_questions():
    out = [(q_of(LIN, LIN_SC), PTS), (q_of(LOG, LOG_SC), LOG_PTS), (q_of(DISC, DISC_SC), DISC_PTS)]
    for lo, hi in ((False, False), (True, True), (True, False)):
        out.append((q_of(LIN | {"open_lower_bound": lo, "open_upper_bound": hi}, LIN_SC), PTS))
    return out


# ---------------------------------------------------------------- s = 1.0 is exactly what live submits
@pytest.mark.parametrize("i", range(6))
def test_s1_gives_back_exactly_the_cdf_live_submits(i):
    q, pts = all_questions()[i]
    stored = json.loads(json.dumps(pts))  # as the aggregate comes out of a record
    assert e.sharpen(stored, 1.0, q) == [tuple(p) for p in pts]
    assert e.submitted_cdf(B, q, stored, 1.0) == live_cdf(q, stored)


def test_live_cdf_builder_unchanged_golden():
    """If forecasting-tools or main.to_prediction changes how the CDF is built, stored forecasts no longer rebuild to what was
    submitted (the records keep the percentiles, not the CDF). Values computed with forecasting-tools 0.3.1 on 2026-11-01."""
    c = e.submitted_cdf(B, q_of(LIN, LIN_SC), PTS)
    assert [c[i] for i in (0, 50, 100, 150, 200)] == pytest.approx([0.0, 0.1673333333, 0.7962, 0.91614375, 0.94955], abs=1e-9)
    d = e.submitted_cdf(B, q_of(DISC, DISC_SC), DISC_PTS)
    assert d == pytest.approx([0.0, 0.0405090909, 0.0810181818, 0.1512272727, 0.3006363636, 0.6480446378, 0.7479542732,
                               0.8478636364, 0.9124155844, 0.9416103896, 0.9708051948, 1.0], abs=1e-9)


# ---------------------------------------------------------------- the transform
def test_sharpen_scales_distances_from_the_median():
    out = e.sharpen(PTS, 0.7, q_of(LIN, LIN_SC))
    m = 75.0  # midpoint of P40 = 70 and P60 = 80
    assert [p for p, _ in out] == [p for p, _ in PTS]
    assert [v for _, v in out] == pytest.approx([m + 0.7 * (v - m) for _, v in PTS])
    assert [v for _, v in out][0] == pytest.approx(50.5) and [v for _, v in out][-1] == pytest.approx(106.5)


def test_sharpen_keeps_order_and_ties():
    out = e.sharpen(DISC_PTS, 0.5, q_of(DISC, DISC_SC))
    vals = [v for _, v in out]
    assert vals == sorted(vals) and vals[2] == vals[3] == 4.0


def test_sharpen_on_a_log_question_works_in_ratios():
    q = q_of(LOG, LOG_SC)
    out = e.sharpen(LOG_PTS, 0.5, q)
    loc = [scoring.numeric_unscaled_location(v, 1.0, 10_000.0, 0.0) for _, v in LOG_PTS]
    new = [scoring.numeric_unscaled_location(v, 1.0, 10_000.0, 0.0) for _, v in out]
    m = (loc[2] + loc[3]) / 2
    assert new == pytest.approx([m + 0.5 * (x - m) for x in loc])
    assert all(v > 0 for _, v in out)  # never below the zero point


def test_sharpen_rejects_nonpositive_s():
    with pytest.raises(ValueError):
        e.sharpen(PTS, 0.0)


@pytest.mark.parametrize("s", e.GRID)
def test_sharpened_cdfs_stay_valid(s):
    for q, pts in all_questions():
        c = e.submitted_cdf(B, q, pts, s)
        assert e.cdf_problems(c, q, B.max_step(q.cdf_size)) == [], (q.question_type, q.open_lower_bound, q.open_upper_bound, s)
        assert len(c) == q.cdf_size
        assert all(b >= a for a, b in zip(c, c[1:]))
        base = e.submitted_cdf(B, q, pts, 1.0)
        if not q.open_lower_bound:
            assert c[0] == base[0] == 0.0
        if not q.open_upper_bound:
            assert c[-1] == base[-1] == 1.0


def test_cdf_problems_catches_bad_cdfs():
    q = q_of(LIN | {"open_lower_bound": True, "open_upper_bound": True}, LIN_SC)
    good = e.submitted_cdf(B, q, PTS)
    assert e.cdf_problems(good, q, B.max_step(q.cdf_size)) == []
    assert e.cdf_problems(good[:-1], q, B.max_step(q.cdf_size))
    flat = list(good)
    flat[100] = flat[99]
    assert e.cdf_problems(flat, q, B.max_step(q.cdf_size))
    no_tail = [0.0] + list(good[1:])
    assert e.cdf_problems(no_tail, q, B.max_step(q.cdf_size))


# ---------------------------------------------------------------- outcomes and scoring (incl. discrete)
def test_outcome_location():
    q, ql = q_of(LIN, LIN_SC), q_of(LOG, LOG_SC)
    assert e.outcome_location("50", q) == 0.25
    assert e.outcome_location("above_upper_bound", q) == 2.0 and e.outcome_location("below_lower_bound", q) == -1.0
    assert e.outcome_location("annulled", q) is None and e.outcome_location("ambiguous", q) is None
    assert e.outcome_location("n/a", q) is None
    from forecasting_tools import NumericDistribution, Percentile

    nd = NumericDistribution(declared_percentiles=[Percentile(percentile=0.1, value=10), Percentile(percentile=0.9, value=900)],
                             open_upper_bound=True, open_lower_bound=False, upper_bound=10_000.0, lower_bound=1.0, zero_point=0.0)
    for v in (1.0, 30.0, 777.0, 10_000.0):
        assert e.outcome_location(str(v), ql) == pytest.approx(nd._nominal_location_to_cdf_location(v), abs=1e-12)
        assert scoring.numeric_scaled_location(e.outcome_location(str(v), ql), 1.0, 10_000.0, 0.0) == pytest.approx(v)


def test_resolution_bucket_follows_metaculus_at_edges():
    # formulas.py: an outcome exactly on an inner edge k/N belongs to bin k (the lower one)
    assert scoring.resolution_bucket(0.25) == 50
    assert scoring.resolution_bucket(0.25 + 1e-6) == 51
    assert scoring.resolution_bucket(0.0) == 1 and scoring.resolution_bucket(1.0) == 200
    assert scoring.resolution_bucket(1.01, cdf_size=12) == 12 and scoring.resolution_bucket(-0.1, cdf_size=12) == 0


def test_discrete_questions_score_on_their_own_buckets():
    q = q_of(DISC, DISC_SC)
    assert q.cdf_size == 12
    loc = e.outcome_location("3", q)  # value 3 of 0..10 is inner bucket 4
    assert scoring.resolution_bucket(loc, 12) == 4
    uniform = list(np.linspace(0, 1, 12))
    assert scoring.numeric_baseline_score(uniform, loc, cdf_size=12) == pytest.approx(0.0, abs=1e-9)
    with pytest.raises(ValueError):
        scoring.numeric_baseline_score(uniform, loc)  # the 201-point default refuses a discrete CDF
    c = e.submitted_cdf(B, q, DISC_PTS)
    got = e.score_cdf(c, loc, q)
    pmf = scoring.cdf_to_pmf(c, 12)
    assert got["baseline"] == pytest.approx(100 * math.log(pmf[4] / (1 / 11)) / 2)
    assert got["log"] == pytest.approx(math.log(pmf[4]))
    assert got["baseline"] - e.score_cdf(e.submitted_cdf(B, q, DISC_PTS, 0.7), loc, q)["baseline"] == pytest.approx(
        50 * (got["log"] - e.score_cdf(e.submitted_cdf(B, q, DISC_PTS, 0.7), loc, q)["log"]))
    with pytest.raises(ValueError):
        e.score_cdf(e.submitted_cdf(B, q_of(LIN, LIN_SC), PTS), loc, q)


def test_pit_and_outside_share():
    c = list(np.linspace(0, 1, 201))
    assert scoring.numeric_pit(c, 0.5 - 1e-6) == pytest.approx(0.4975, abs=1e-9)
    assert e.outside(0.05) and e.outside(0.95) and not e.outside(0.5)
    q = q_of(LIN, LIN_SC)
    cdf = e.submitted_cdf(B, q, PTS)
    assert scoring.numeric_pit(cdf, 2.0) == pytest.approx((cdf[-1] + 1) / 2)


# ---------------------------------------------------------------- folds and nested CV
def test_folds_never_split_a_cluster():
    clusters = {f"k{i}": f"c{i // 3}" for i in range(60)}
    primary = {k: {s: float(i % 5) for s in e.GRID} for i, k in enumerate(clusters)}
    cv = e.nested_cv(primary, clusters, k=5, seed=1)
    for f in range(5):
        test = {clusters[k] for k in primary if cv.fold_of_cluster[clusters[k]] == f}
        train = {clusters[k] for k in primary if cv.fold_of_cluster[clusters[k]] != f}
        assert test and not test & train
    assert sum(f.n_test for f in cv.folds) == 60
    assert e.outer_folds(clusters.values(), 5, 1) == e.outer_folds(clusters.values(), 5, 1)
    assert e.outer_folds(clusters.values(), 5, 1) != e.outer_folds(clusters.values(), 5, 2)


def test_choose_s_prefers_the_baseline_on_ties():
    primary = {"a": {s: 1.0 for s in e.GRID}}
    assert e.choose_s(["a"], primary)[0] == 1.0
    primary = {"a": {s: (1.0 if s in (0.7, 0.8) else 0.0) for s in e.GRID}}
    assert e.choose_s(["a"], primary)[0] == 0.8


def test_too_wide_forecasts_are_sharpened_with_a_ci_excluding_0(tmp_path):
    an, _ = e.synthetic_analysis(B, tmp_path, 80, true_s=0.6, seed=3, reps=2000)
    assert all(f.chosen_s < 1.0 for f in an.folds)
    assert an.s_all < 1.0
    assert an.comparison.ci_low > 0
    assert an.decision.verdict == "adopt"


def _pool(n=80, seed=4):
    """Made-up calibrated questions with their live CDFs at every s, built once (the slow part)."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        snap, sc = e.synthetic_question(("numeric", "log", "discrete", "numeric")[i % 4], i, i, rng)
        q = e.axis_from(snap, sc)
        pts, _, _ = e.synthetic_points(snap, sc, rng)
        out.append((q, {s: e.submitted_cdf(B, q, pts, s) for s in e.GRID}))
    return out


def test_calibrated_forecasts_ci_includes_0_in_about_95_percent_of_trials():
    """Outcomes drawn from each forecast's own submitted distribution (calibrated by construction), fresh draws per trial."""
    pool, trials, covered = _pool(), 40, 0
    for t in range(trials):
        r = np.random.default_rng(100 + t)
        primary, clusters = {}, {}
        for j, (q, cdfs) in enumerate(pool):
            loc = e.outcome_location(e.draw_resolution(cdfs[1.0], q, r), q)
            primary[str(j)] = {s: scoring.numeric_baseline_score(c, loc, q.open_lower_bound, q.open_upper_bound,
                                                                 cdf_size=len(c)) for s, c in cdfs.items()}
            clusters[str(j)] = str(j // 2)
        cv = e.nested_cv(primary, clusters, seed=t)
        c = e._comparison(primary, cv.oof_s, clusters, arm="x", reps=500, seed=t)
        covered += not c.ci_excludes_zero
    # 95% expected; 34 of 40 is the lower end of an exact binomial band with ~1% in its tail
    assert covered >= 34, covered


# ---------------------------------------------------------------- loading: drops, gate, cut-off, count
def _write(tmp_path, n, **kw):
    return e.write_synthetic(tmp_path, e.synthetic_cases(B, n, **kw))


def _rec(qid, post, status="ok", agg=PTS, qtype="numeric", models=None):
    snap = (LIN if qtype == "numeric" else DISC) | {"question_id": qid, "post_id": post, "question_type": qtype}
    return {"question": snap, "as_of": "2025-03-14T12:00:00+00:00", "status": status, "aggregate": agg, "config_version": "x",
            "forecasters": [{"model": m} for m in (models or [f.model for f in e.TOURNAMENT_CONFIG.forecasters])],
            "research": {"items": [{"text": "SECRET RESEARCH TEXT"}]}}


def test_drops_are_counted_by_reason(tmp_path):
    runs = tmp_path / "runs"
    live, test, shadow = runs / "r1-tournament", runs / "r0-test_questions", runs / "r1-tournament-shadow-cheap"
    for d in (live, test, shadow):
        d.mkdir(parents=True)
    later = runs / "r2-tournament"
    later.mkdir()
    files = {
        live / "q1.json": _rec(1, 11), later / "q1.json": _rec(1, 11) | {"as_of": "2025-03-20T00:00:00+00:00"},
        shadow / "q1.json": _rec(1, 11, status="shadow", models=[f.model for f in e.DEFAULT_CONFIG.forecasters]),
        test / "q2.json": _rec(2, 12), live / "q3.json": _rec(3, 13, status="failed"),
        live / "q4.json": _rec(4, 14, agg=None), live / "q5.json": _rec(5, 15), live / "q6.json": _rec(6, 16),
        live / "q7.json": _rec(7, 17), live / "q8.json": _rec(8, 18), live / "q9.json": _rec(9, 19),
        live / "q10.json": _rec(10, 20), live / "q11.json": _rec(11, 21, qtype="discrete", agg=DISC_PTS),
        live / "q12.json": {"question": {"question_id": 12, "question_type": "binary"}, "status": "ok", "aggregate": 0.3},
    }
    for path, rec in files.items():
        path.write_text(json.dumps(rec))
    (live / "q13.json").write_text("{not json")
    res = {1: "120", 5: None, 6: "annulled", 7: "50", 8: "60", 9: "nonsense", 10: "below_lower_bound", 11: "4"}
    outcomes = tmp_path / "outcomes.jsonl"
    outcomes.write_text("".join(json.dumps({"question_id": q, "status": "resolved" if r else "open", "resolution": r,
                                            "actual_resolve_time": "2025-04-01T00:00:00Z"}) + "\n" for q, r in res.items()))
    sc = {q: {"question_id": q, "range_min": 0.0, "range_max": 200.0, "zero_point": None, "inbound_outcome_count": 200}
          for q in (1, 5, 6, 8, 9, 10)}
    sc[8]["range_max"] = 300.0  # disagrees with the record
    sc[11] = {"question_id": 11, "range_min": -0.5, "range_max": 10.5, "zero_point": None, "inbound_outcome_count": 11}
    scaling = tmp_path / "scaling.jsonl"
    scaling.write_text("".join(json.dumps(r) + "\n" for r in sc.values()))
    loaded = e.load_cases(runs, outcomes, scaling)
    assert sorted(c.key for c in loaded.cases) == ["11:live", "1:live", "1:shadow-cheap"]
    assert {c.key: c.era for c in loaded.cases} == {"1:live": "frontier", "1:shadow-cheap": "cheap", "11:live": "frontier"}
    assert loaded.dropped == Counter({
        e.DROP_UNREADABLE: 1, e.DROP_TEST: 1, e.DROP_STATUS: 1, e.DROP_NO_AGGREGATE: 1, e.DROP_DUPLICATE: 1,
        e.DROP_UNRESOLVED: 1, e.DROP_ANNULLED: 1, e.DROP_NO_SCALING: 1, e.DROP_SCALING_MISMATCH: 1, e.DROP_BAD_RESOLUTION: 1,
        e.DROP_OUTSIDE_CLOSED: 1,
    })
    assert loaded.n_questions == 2
    pending = e.load_cases(runs, outcomes, scaling, need_scaling=False)
    assert [c.question_id for c in pending.pending] == [7]


def test_resolved_after_keeps_only_questions_resolved_strictly_later(tmp_path):
    paths = _write(tmp_path, 10, seed=1, shadow_share=0.0)
    rows = [json.loads(x) for x in paths["outcomes"].read_text().splitlines()]
    rows[0]["actual_resolve_time"] = None  # undated: can't show it is fresh
    rows[1]["scheduled_resolve_time"] = "2030-01-01T00:00:00Z"  # the scheduled time is never used
    paths["outcomes"].write_text("".join(json.dumps(r) + "\n" for r in rows))
    cut = date.fromisoformat(rows[5]["actual_resolve_time"][:10])
    loaded = e.load_cases(paths["runs"], paths["outcomes"], paths["scaling"], resolved_after=cut)
    assert {c.question_id for c in loaded.cases} == {r["question_id"] for r in rows[6:]}
    assert loaded.dropped[e.DROP_NO_DATE] == 1
    assert loaded.dropped[e.DROP_NOT_FRESH] == 5
    assert all(date.fromisoformat(c.resolved_at) > cut for c in loaded.cases)


def _args(paths, *extra):
    return ["--runs-dir", str(paths["runs"]), "--outcomes", str(paths["outcomes"]), "--scaling", str(paths["scaling"]), *extra]


def test_n_gate_refuses_a_verdict_below_40_questions(tmp_path, capsys):
    paths = _write(tmp_path / "d", 39, seed=2)
    out = tmp_path / "r.md"
    assert T.main(_args(paths, "--out", str(out), "--reps", "200")) == 3
    text = capsys.readouterr().out
    assert "not enough data: N = 39" in text and "ADOPT" not in text and "INCONCLUSIVE" not in text
    assert not out.exists()


def test_rerun_and_confirm_need_a_cut_off(tmp_path, capsys):
    paths = _write(tmp_path / "d", 5, seed=2)
    assert T.main(_args(paths, "--run-kind", "rerun")) == 2
    assert T.main(_args(paths, "--confirm-s", "0.8")) == 2
    assert "--resolved-after" in capsys.readouterr().err


def test_full_run_writes_a_deterministic_report_and_fills_the_card(tmp_path, capsys):
    paths = _write(tmp_path / "d", 44, seed=5, true_s=0.6, shadow_share=0.5)
    card = tmp_path / "card.md"
    card.write_text("# EXP-010\n\n## Results (generated)\n## Decision\n")
    out1, out2 = tmp_path / "r1.md", tmp_path / "r2.md"
    assert T.main(_args(paths, "--out", str(out1), "--reps", "500", "--card", str(card))) == 0
    assert T.main(_args(paths, "--out", str(out2), "--reps", "500")) == 0
    text = out1.read_text()
    assert text == out2.read_text()
    assert "Synthetic question" not in text and "SECRET" not in text
    for needle in ("## Provenance", "seed 1010 (pre-registered)", "combined sha256", "forecasting-tools", "### Decision",
                   "By era", "By type", "By tournament", "outer fold", "N: 44 questions"):
        assert needle in text, needle
    assert report.BEGIN in card.read_text() and "## Decision" in card.read_text()
    printed = capsys.readouterr().out
    assert "Decision for arm" in printed


def test_count_mode(tmp_path, capsys):
    paths = _write(tmp_path / "d", 12, seed=6, shadow_share=1.0)
    lines = paths["scaling"].read_text().splitlines()
    paths["scaling"].write_text("\n".join(lines[:9]) + "\n")  # three questions without scaling yet
    assert e.main(_args(paths, "--count")) == 0
    text = capsys.readouterr().out
    assert "resolved questions: 12 (ready to score, scaling stored: 9)" in text and "not reached (31 more needed)" in text
    assert "--fetch-scaling" in text
    assert "frontier" in text and "cheap" in text
    assert "baseline" not in text.lower().replace("baseline_score", "")  # no scores in count mode


# ---------------------------------------------------------------- decision rule
def _cmp(point, lo, hi, n=50):
    return report.Comparison(arm="x", baseline="s = 1.0", metric=e.PRIMARY.key, higher_is_better=True, n=n, n_clusters=25,
                             mean_arm=0, mean_base=0, mean_diff=point, ci_low=lo, ci_high=hi, se_boot=1.0, median_diff=point,
                             wins=1, ties=0, losses=1, sd_diff=5.0, mde=1.0)


def _folds(*s):
    return [e.Fold(i + 1, 40, 10, 5, x) for i, x in enumerate(s)]


def _era(name, point):
    return e.Group(name, 20, 20, _cmp(point, point - 5, point + 5, 20), 0.8, 0.1, 0.2)


def test_decision_rule_as_code():
    stable = _folds(0.7, 0.8, 0.7, 0.7, 0.8)
    eras = [_era("frontier", 3.0), _era("cheap", 2.0)]
    assert e.decide(_cmp(3.0, 1.0, 5.0), stable, eras, "screen").verdict == "adopt"
    assert e.decide(_cmp(3.0, 1.0, 5.0), _folds(0.7, 1.0, 0.7, 0.7, 0.8), eras, "screen").verdict == "inconclusive"  # unstable
    disagree = [_era("frontier", -1.0), _era("cheap", 4.0)]
    assert e.decide(_cmp(3.0, 1.0, 5.0), stable, disagree, "screen").verdict == "inconclusive"  # live era doesn't gain
    assert e.decide(_cmp(3.0, 1.0, 5.0), stable, [_era("cheap", 4.0)], "screen").verdict == "inconclusive"  # no frontier data
    assert e.decide(_cmp(1.0, -2.0, 4.0), stable, eras, "screen").verdict == "inconclusive"
    assert e.decide(_cmp(-0.5, -2.0, 1.0), stable, eras, "screen").verdict == "kill"  # point ≤ 0
    small = e.decide(_cmp(0.4, 0.1, 0.7), stable, eras, "screen")  # real but below the 0.75-point threshold: conflict -> kill
    assert small.verdict == "kill" and "on_conflict" in small.note
    narrow = e.decide(_cmp(2.0, 0.3, 3.7), stable, eras, "screen")
    assert narrow.verdict == "adopt" and "Narrow win" in narrow.next_step
    assert "Tier 1" in e.decide(_cmp(3.0, 1.0, 5.0), stable, eras, "screen").next_step
    assert "Dropped" in e.decide(_cmp(1.0, -2.0, 4.0), stable, eras, "rerun").next_step


def test_confirm_mode_rule():
    ok = e.decide(_cmp(2.0, 0.2, 3.8), [], [], "confirm")
    assert ok.verdict == "adopt" and "Confirmed" in ok.next_step
    fail = e.decide(_cmp(2.0, -0.2, 3.8), [], [], "confirm")
    assert fail.verdict != "adopt" and "goes back to the old config" in fail.next_step


def test_confirm_run_uses_the_fixed_s_and_a_one_sided_bound(tmp_path):
    paths = _write(tmp_path / "d", 32, seed=8, true_s=0.6)
    out = tmp_path / "c.md"
    assert T.main(_args(paths, "--confirm-s", "0.7", "--resolved-after", "2024-01-01", "--out", str(out), "--reps", "500")) == 0
    text = out.read_text()
    assert "Tier 1 confirmation" in text and "(90%)" in text and "s = 0.7 (fixed, adopted)" in text
    assert "| outer fold |" not in text and ("**CONFIRMED**" in text or "**NOT CONFIRMED**" in text)


# ---------------------------------------------------------------- tagging and scaling fetch
def test_era_source_and_tournament_tags(tmp_path):
    assert e.era_of(f.model for f in e.TOURNAMENT_CONFIG.forecasters) == "frontier"
    assert e.era_of(f.model for f in e.DEFAULT_CONFIG.forecasters) == "cheap"
    assert e.era_of(["some/model"]) == "other"
    assert e.source_of(tmp_path / "20261010T000000Z-tournament-shadow-cheap" / "q1.json") == "shadow-cheap"
    assert e.source_of(tmp_path / "20261010T000000Z-tournament" / "q1.json") == "live"
    assert e.tournament_of(["minibench-2026-10-05", "minibench-2026-10-12"]) == "minibench"
    assert e.tournament_of([]) == "none"


CLOCK = FixedClock(datetime(2025, 3, 14, 12, 0, tzinfo=timezone.utc))


class FakeResp:
    status_code, ok = 200, True

    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d


class FakeSession:
    def __init__(self, posts):
        self.posts, self.calls = posts, []

    def get(self, url, timeout=None):
        pid = int(url.rstrip("/").split("/")[-1])
        self.calls.append(pid)
        return FakeResp(self.posts[pid])


def test_fetch_scaling_stores_each_numeric_question_once(tmp_path):
    runs = tmp_path / "runs" / "r1-tournament"
    runs.mkdir(parents=True)
    for qid, pid, t in ((1, 10, "numeric"), (2, 20, "discrete"), (3, 30, "binary")):
        (runs / f"q{qid}.json").write_text(json.dumps({"question": {"question_id": qid, "post_id": pid, "question_type": t}}))
    posts = {10: {"question": {"id": 1, "type": "numeric", "open_lower_bound": False, "open_upper_bound": True,
                               "scaling": {"range_min": 1.0, "range_max": 1000.0, "zero_point": 0.0, "inbound_outcome_count": 200}}},
             20: {"group_of_questions": {"questions": [{"id": 2, "type": "discrete", "scaling": {
                 "range_min": -0.5, "range_max": 4.5, "zero_point": None, "inbound_outcome_count": 5}}]}}}
    out = tmp_path / "scaling.jsonl"
    s = FakeSession(posts)
    c = e.fetch_scaling(s, CLOCK, tmp_path / "runs", out, gap_s=0)
    assert c["fetched"] == 2 and sorted(s.calls) == [10, 20]
    stored = e.load_scaling(out)
    assert stored[1]["zero_point"] == 0.0 and stored[2]["inbound_outcome_count"] == 5
    s.calls.clear()
    assert e.fetch_scaling(s, CLOCK, tmp_path / "runs", out, gap_s=0)["fetched"] == 0 and s.calls == []


# ---------------------------------------------------------------- ADR-0005 split and the tool's self-check
def test_evals_module_runs_without_the_adapter_and_points_to_the_tool(tmp_path, capsys):
    """evals/exp010.py never imports main.py or forecasting-tools (tests/test_hygiene.py); the scored run needs the builder."""
    src = (ROOT / "evals" / "exp010.py").read_text(encoding="utf-8")
    assert "import main" not in src and "from forecasting_tools" not in src
    paths = _write(tmp_path / "d", 3, seed=9)
    assert e.main(_args(paths)) == 2
    assert e.main(["--power"]) == 2
    assert "tools/exp010.py" in capsys.readouterr().err


def test_tool_validation_cli(capsys):
    assert T.main(["--validate"]) == 0
    out = capsys.readouterr().out
    assert "EXP-010 tool validation" in out and "4 passed, 0 failed" in out and "TOOL VALIDATED" in out
    for name in ("reproduces live", "valid CDFs", "too wide detected", "calibrated kept"):
        assert f"PASS  EXP-010 tool: {name}" in out


def test_a_sharpen_that_ignores_s_fails_the_tool_validation(monkeypatch, tmp_path):
    real = e.sharpen
    monkeypatch.setattr(e, "sharpen", lambda points, s, question=None: real(points, 1.0, question))
    res = T.check_offline(tmp_path, n_questions=80, reps=500)
    assert any(r.status == vh.FAIL and "too wide detected" in r.headline for r in res)


class _Drifting:
    """A builder that is not quite the live one."""

    def build(self, axis, points):
        return [round(x, 3) for x in B.build(axis, points)]

    def max_step(self, n):
        return B.max_step(n)


def test_a_builder_that_drifts_from_live_fails_the_tool_validation(tmp_path):
    res = T.check_offline(tmp_path, n_questions=80, reps=500, builder=_Drifting())
    assert any(r.status == vh.FAIL and "reproduces live" in r.headline for r in res)
