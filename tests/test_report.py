"""Tests for the experiment report generator (evals/report.py, B-31). Synthetic data only; offline."""
import math

import numpy as np
import pytest

from evals import report as r
from evals import scoring
from evals.report_demo import synthetic_rows, write_csv


def row(q, arm, member, p, outcome=1, repeat=1, cluster=None, **kw):
    d = {"question_id": q, "cluster_id": cluster or f"c-{q}", "arm": arm, "member": member, "repeat": repeat,
         "p": "" if p is None else p, "outcome": outcome}
    d.update(kw)
    return r.parse_row(d)


def comparison(mean_diff, lo, hi, n=100, mde=0.04, higher_is_better=True, se_boot=0.0):
    return r.Comparison(
        arm="B", baseline="A", metric="log", higher_is_better=higher_is_better, n=n, n_clusters=n, mean_arm=0.0, mean_base=0.0,
        mean_diff=mean_diff, ci_low=lo, ci_high=hi, se_boot=se_boot, median_diff=mean_diff, wins=0, ties=0, losses=0, sd_diff=0.1,
        mde=mde,
    )


def decide(c, rel=(0.010, 0.010), fail=(0.01, 0.01), rule=r.EXP005_RULE, mde_value=None, cost=None):
    return r.apply_decision_rule(
        rule, c, reliability_arm=rel[0], reliability_base=rel[1], failure_rate_arm=fail[0], failure_rate_base=fail[1],
        cost_per_question_arm=cost, mde_value=mde_value,
    )


# ---------------------------------------------------------------- bootstrap
def test_known_effect_is_covered_end_to_end():
    """A forecasts 50% everywhere, B forecasts the true probability q ~ U(0, 1). The expected log-score gain is
    ln 2 − E[H(q)] = ln 2 − 1/2 nats (E of the binary entropy over a uniform q is 1/2). The CI must cover it and exclude 0."""
    rng = np.random.default_rng(1)
    n = 400
    q = rng.uniform(0.02, 0.98, n)
    y = (rng.random(n) < q).astype(int)
    rows = []
    for i in range(n):
        cl = f"c{i // 4}"
        rows.append(row(f"q{i}", "A", "aggregate", 0.5, int(y[i]), cluster=cl))
        rows.append(row(f"q{i}", "B", "aggregate", float(q[i]), int(y[i]), cluster=cl))
    an = r.analyse(rows, "A", reps=2000, seed=7)
    c = an.comparisons["B"]["log"]
    true_effect = math.log(2) - 0.5
    assert c.n == n and c.n_clusters == 100
    assert c.ci_low < true_effect < c.ci_high
    assert c.ci_low > 0
    assert c.verdict_words() == "arm better (CI excludes 0)"
    # the mean difference is the mean of the per-question scorer differences, nothing reimplemented
    expect = np.mean([scoring.binary_log_score(q[i], y[i]) - scoring.binary_log_score(0.5, y[i]) for i in range(n)])
    assert c.mean_diff == pytest.approx(expect)


def test_null_effect_ci_contains_zero_about_95_percent():
    """Clustered null data (cluster shocks + noise, true mean 0): the cluster-bootstrap CI should contain 0 in ~95% of trials."""
    trials, covered = 80, 0
    for t in range(trials):
        rng = np.random.default_rng(1000 + t)
        n_clusters, per = 40, 3
        shocks = rng.normal(0, 0.1, n_clusters)
        diffs = np.repeat(shocks, per) + rng.normal(0, 0.1, n_clusters * per)
        clusters = np.repeat([f"c{i}" for i in range(n_clusters)], per)
        lo, hi, _ = r.cluster_bootstrap_ci(diffs, clusters, reps=1000, seed=t)
        covered += lo <= 0 <= hi
    assert 0.85 <= covered / trials <= 1.0


def test_cluster_resampling_moves_questions_together():
    """Two clusters of ten questions each, all +1 in one and all −1 in the other. Whole clusters are drawn, so every replicate
    mean is −1, 0 or +1; resampling single questions would give many other values."""
    diffs = [1.0] * 10 + [-1.0] * 10
    clusters = ["a"] * 10 + ["b"] * 10
    means = r.bootstrap_means(diffs, clusters, reps=500, seed=3)
    assert set(np.round(means, 9)) <= {-1.0, 0.0, 1.0}
    assert len(set(np.round(means, 9))) == 3


def test_cluster_ci_is_wider_than_treating_questions_as_independent():
    rng = np.random.default_rng(5)
    shocks = rng.normal(0, 0.2, 20)
    diffs = np.repeat(shocks, 10) + rng.normal(0, 0.02, 200)
    clustered = r.cluster_bootstrap_ci(diffs, np.repeat(np.arange(20).astype(str), 10), reps=2000, seed=1)
    naive = r.cluster_bootstrap_ci(diffs, np.arange(200).astype(str), reps=2000, seed=1)
    assert clustered[1] - clustered[0] > 2 * (naive[1] - naive[0])


def test_bootstrap_is_reproducible_with_the_seed():
    d, c = [0.1, -0.2, 0.3, 0.05], ["a", "a", "b", "c"]
    assert r.cluster_bootstrap_ci(d, c, 500, seed=9) == r.cluster_bootstrap_ci(d, c, 500, seed=9)


def test_mde_formula():
    assert r.mde(0.06, 100) == pytest.approx(2.8 * 0.06 / 10)
    assert math.isnan(r.mde(float("nan"), 10))


# ---------------------------------------------------------------- aggregation and repeats
def test_repeats_are_scored_one_by_one_by_default():
    """Default (protocol §5 as amended 2026-10-08): each repeat is scored, then the scores are averaged (one live run)."""
    rows = [
        row("q1", "A", "aggregate", 0.5),
        row("q1", "B", "aggregate", 0.2, repeat=1),
        row("q1", "B", "aggregate", 0.8, repeat=2),
    ]
    an = r.analyse(rows, "A", reps=100)
    expected = (math.log(0.2) + math.log(0.8)) / 2
    assert an.summaries["B"].mean_log == pytest.approx(expected)
    assert an.comparisons["B"]["log"].mean_diff == pytest.approx(expected - math.log(0.5))
    assert an.summaries["B"].repeat_sd_p == pytest.approx(np.std([0.2, 0.8], ddof=1))
    # the other mode is shown as a descriptive line: forecasts averaged first gives 0.5 -> ln 0.5, no difference
    assert an.other_mode["B"].mean_diff == pytest.approx(0.0)
    assert "Descriptive only" in r.render_report(an)


def test_forecast_mode_averages_the_repeats_before_scoring():
    rows = [
        row("q1", "A", "aggregate", 0.5),
        row("q1", "B", "aggregate", 0.2, repeat=1),
        row("q1", "B", "aggregate", 0.8, repeat=2),
    ]
    an = r.analyse(rows, "A", reps=100, repeat_mode="forecast")
    assert an.summaries["B"].mean_log == pytest.approx(math.log(0.5))
    assert an.comparisons["B"]["log"].mean_diff == pytest.approx(0.0)


def test_score_mode_gives_no_bonus_to_noise_between_repeats():
    """Arm B = arm A plus symmetric noise between repeats. Averaging forecasts first cancels the noise and hides it; scoring
    each repeat shows that a single live run of B is worse."""
    rng = np.random.default_rng(11)
    rows = []
    for i in range(200):
        p = float(rng.uniform(0.2, 0.8))
        y = int(rng.random() < p)
        rows.append(row(f"q{i}", "A", "aggregate", p, y, repeat=1))
        rows.append(row(f"q{i}", "A", "aggregate", p, y, repeat=2))
        rows.append(row(f"q{i}", "B", "aggregate", p - 0.15, y, repeat=1))
        rows.append(row(f"q{i}", "B", "aggregate", p + 0.15, y, repeat=2))
    by_score = r.analyse(rows, "A", reps=500).comparisons["B"]["log"]
    by_forecast = r.analyse(rows, "A", reps=500, repeat_mode="forecast").comparisons["B"]["log"]
    assert by_forecast.mean_diff == pytest.approx(0.0, abs=1e-12)
    assert by_score.mean_diff < 0 and by_score.ci_high < 0


def test_calibration_counts_every_repeat_in_score_mode():
    rows = [row("q1", "A", "aggregate", 0.2, repeat=1), row("q1", "A", "aggregate", 0.8, repeat=2)]
    arm = r.prepare(rows).arms["A"]
    assert arm.calibration_points({"q1": 1}, "score") == ([0.2, 0.8], [1, 1])
    assert arm.calibration_points({"q1": 1}, "forecast") == ([pytest.approx(0.5)], [1])


def test_members_are_aggregated_with_the_live_median_per_repeat():
    rows = [
        row("q1", "A", "m1", 0.2), row("q1", "A", "m2", 0.6), row("q1", "A", "m3", None),  # failed member: median of 0.2, 0.6
        row("q1", "A", "m1", 0.9, repeat=2), row("q1", "A", "m2", 0.7, repeat=2), row("q1", "A", "m3", 0.1, repeat=2),
    ]
    prep = r.prepare(rows)
    assert prep.arms["A"].aggregate["q1"] == {1: pytest.approx(0.4), 2: pytest.approx(0.7)}
    assert prep.arms["A"].forecasts()["q1"] == pytest.approx(0.55)
    assert prep.arms["A"].member_failures == 1 and prep.arms["A"].member_rows == 6


def test_given_aggregate_rows_win_in_auto_mode_and_median_mode_recomputes():
    rows = [row("q1", "A", "m1", 0.2), row("q1", "A", "m2", 0.4), row("q1", "A", "aggregate", 0.9)]
    assert r.prepare(rows, "auto").arms["A"].forecasts()["q1"] == pytest.approx(0.9)
    assert r.prepare(rows, "median").arms["A"].forecasts()["q1"] == pytest.approx(0.3)


# ---------------------------------------------------------------- drops
def test_missing_pairs_are_dropped_and_counted():
    rows = []
    for q in ("q1", "q2", "q3", "q4", "q5"):
        rows.append(row(q, "A", "m1", 0.6 if q != "q4" else None))  # A fails on q4
        if q != "q3":  # B never ran q3
            rows.append(row(q, "B", "m1", 0.7))
    rows.append(row("q6", "A", "m1", 0.5, outcome=""))  # unresolved
    rows.append(row("q6", "B", "m1", 0.5, outcome=""))
    rows.append(row("q7", "A", "m1", 0.5, question_type="numeric"))
    an = r.analyse(rows, "A", reps=100)
    c = an.comparisons["B"]["log"]
    assert c.n == 3
    assert c.dropped == {"not run by B": 1, "every forecast failed in A": 1}
    assert an.prepared.dropped == {"q6": r.DROP_NO_OUTCOME, "q7": r.DROP_NOT_BINARY}
    md = r.render_report(an)
    assert "not run by B: 1" in md and "every forecast failed in A: 1" in md
    assert r.DROP_NO_OUTCOME + ": 1" in md and r.DROP_NOT_BINARY + ": 1" in md


def test_inconsistent_input_is_refused():
    with pytest.raises(ValueError, match="conflicting outcomes"):
        r.prepare([row("q1", "A", "m1", 0.5, 1), row("q1", "B", "m1", 0.5, 0)])
    with pytest.raises(ValueError, match="more than one cluster"):
        r.prepare([row("q1", "A", "m1", 0.5, cluster="x"), row("q1", "B", "m1", 0.5, cluster="y")])
    with pytest.raises(ValueError, match="out of"):
        row("q1", "A", "m1", 1.5)
    with pytest.raises(ValueError, match="duplicate"):
        r.prepare([row("q1", "A", "m1", 0.5), row("q1", "A", "m1", 0.6)])


# ---------------------------------------------------------------- decision rule: every branch
def test_rule_adopt():
    d = decide(comparison(0.05, 0.02, 0.08))
    assert d.verdict == "adopt"
    assert all(c.met for c in d.checks if c.kind == "adopt") and not any(c.met for c in d.checks if c.kind == "kill")


def test_rule_kill_on_point_estimate():
    d = decide(comparison(-0.01, -0.05, 0.03))
    assert d.verdict == "kill"
    assert [c.met for c in d.checks if c.kind == "kill"] == [True, False]


def test_rule_kill_when_ci_upper_is_below_the_fixed_threshold():
    d = decide(comparison(0.004, -0.010, 0.012))  # EXP-005: upper 0.012 < 0.015
    assert d.verdict == "kill"
    assert [c.met for c in d.checks if c.kind == "kill"] == [False, True]
    assert math.isnan(d.mde_used)  # the amended EXP-005 rule does not use an MDE
    assert decide(comparison(0.004, -0.010, 0.016)).verdict == "inconclusive"


MDE_RULE = r.DecisionRule(name="MDE/2 rule", kill_if_ci_upper_below_mde_fraction=0.5)


def test_rule_kill_when_ci_upper_is_below_half_the_mde():
    d = decide(comparison(0.004, -0.010, 0.015), mde_value=0.04, rule=MDE_RULE)  # upper 0.015 < 0.02
    assert d.verdict == "kill"
    assert d.mde_source.startswith("pre-registered")
    assert [c.met for c in d.checks if c.kind == "kill"] == [False, True]


def test_rule_without_a_preregistered_mde_uses_the_cluster_aware_one():
    d = decide(comparison(0.004, -0.010, 0.015, se_boot=0.02), rule=MDE_RULE)  # 2.8 * 0.02 = 0.056 -> half 0.028
    assert d.mde_used == pytest.approx(0.056) and "cluster-aware" in d.mde_source
    assert d.verdict == "kill"


def test_rule_inconclusive():
    d = decide(comparison(0.01, -0.01, 0.03), mde_value=0.04)  # positive, CI includes 0, upper 0.03 ≥ 0.02
    assert d.verdict == "inconclusive"


def test_rule_reliability_worsening_blocks_adoption():
    d = decide(comparison(0.05, 0.02, 0.08), rel=(0.020, 0.010))
    assert d.verdict == "inconclusive"
    assert [c.met for c in d.checks if "reliability" in c.condition] == [False]


def test_rule_failure_rate_increase_blocks_adoption():
    d = decide(comparison(0.05, 0.02, 0.08), fail=(0.05, 0.01))
    assert d.verdict == "inconclusive"
    assert [c.met for c in d.checks if "failure rate" in c.condition] == [False]
    assert decide(comparison(0.05, 0.02, 0.08), fail=(0.03, 0.01)).verdict == "adopt"  # exactly +2 pp is allowed


def test_rule_conflict_between_adopt_and_kill_goes_to_on_conflict():
    c = comparison(0.006, 0.002, 0.010)  # significant, but the whole CI is below the 0.015 threshold
    d = decide(c)
    assert d.verdict == "kill" and "both hold" in d.note  # EXP-005 (amended): a real but too small effect is killed
    generic = r.DecisionRule(name="x", kill_if_ci_upper_below=0.015)
    assert decide(c, rule=generic).verdict == "inconclusive"  # the generic default


def test_rule_with_no_paired_questions_is_inconclusive():
    assert decide(comparison(float("nan"), float("nan"), float("nan"), n=0)).verdict == "inconclusive"


def test_rule_is_generic_and_orients_lower_is_better_metrics():
    rule = r.DecisionRule(name="brier rule", metric="brier", max_reliability_increase=None, max_failure_rate_increase=None,
                          kill_if_ci_upper_below_mde_fraction=None, max_cost_per_question=0.5)
    c = comparison(-0.02, -0.03, -0.01, higher_is_better=False)  # Brier went down: the arm is better
    c.metric = "brier"
    assert decide(c, rule=rule, cost=0.10).verdict == "adopt"
    assert decide(c, rule=rule, cost=0.90).verdict == "inconclusive"
    with pytest.raises(ValueError):
        decide(comparison(0.05, 0.02, 0.08), rule=rule)  # rule on brier, comparison on log


def test_rule_from_json_never_keeps_the_preregistered_name_when_modified():
    rule = r.rule_from_json('{"base": "exp005", "max_reliability_increase": 0.01}')
    assert rule.max_reliability_increase == 0.01 and rule.kill_if_ci_upper_below == 0.015 and rule.on_conflict == "kill"
    assert rule.name != r.EXP005_RULE.name and "modified" in rule.name


# ---------------------------------------------------------------- report
def test_report_never_calls_a_win_when_the_ci_includes_zero():
    rows = synthetic_rows(n_questions=60, n_clusters=30, arm_noise={"A": 1.0, "B": 1.0}, seed=2)
    an = r.analyse([r.parse_row(x) for x in rows], "A", reps=500)
    c = an.comparisons["B"]["log"]
    assert c.ci_low <= 0 <= c.ci_high
    assert "better" not in c.verdict_words()


def test_full_report_from_csv_has_every_section(tmp_path):
    rows = synthetic_rows(n_questions=50, n_clusters=25, arm_noise={"A": 1.0, "B": 0.5}, arm_scale={"A": 1.5},
                          arm_failure={"B": 0.05}, seed=4)
    path = tmp_path / "t.csv"
    write_csv(rows, path)
    out = tmp_path / "rep.md"
    assert r.main([str(path), "--baseline", "A", "--name", "T", "--rule", "exp005", "--reps", "300", "--out", str(out)]) == 0
    md = out.read_text(encoding="utf-8")
    for heading in ("## Reproducibility", "## Decision", "## Paired comparison", "## Scores per arm", "## Calibration",
                    "## Each member alone", "## Breakdowns", "forward set (benchmark-forward) vs own records",
                    "## Repeats, failures and cost", "## Diversity (B-47)"):
        assert heading in md, heading
    assert r.sha256_file(path) in md and "seed 3101" in md and "300 reps" in md
    assert "`score`: each repeat scored on its own" in md and "under _EXP-005 confirmation rule (card amended 2026-10-08)_" in md
    # deterministic: the same input gives the same report
    out2 = tmp_path / "rep2.md"
    r.main([str(path), "--baseline", "A", "--name", "T", "--rule", "exp005", "--reps", "300", "--out", str(out2)])
    assert out2.read_text(encoding="utf-8") == md


def test_extra_sections_hook_replaces_the_diversity_placeholder():
    rows = [row("q1", "A", "m1", 0.6), row("q1", "B", "m1", 0.7)]
    an = r.analyse(rows, "A", reps=50)
    md = r.render_report(an, extra_sections=[lambda a: "## Diversity (B-47)\n\nspread: 0.1"])
    assert "spread: 0.1" in md and "Not computed yet" not in md


def test_fill_card_is_idempotent():
    card = "# EXP\n\n## Results (generated)\n## Decision\nkeep me\n"
    once = r.fill_card(card, "# T: experiment report\n\n## Reproducibility\nv1\n")
    assert "#### Reproducibility" in once and once.endswith("## Decision\nkeep me\n")
    twice = r.fill_card(once, "# T: experiment report\n\n## Reproducibility\nv2\n")
    assert "v2" in twice and "v1" not in twice and twice.count(r.BEGIN) == 1


def test_costs_and_tokens_are_per_question_per_repeat():
    rows = [
        row("q1", "A", "m1", 0.6, repeat=1, cost_usd=0.01, prompt_tokens=100, completion_tokens=50),
        row("q1", "A", "m2", 0.6, repeat=1, cost_usd=0.03, tokens=150),
        row("q1", "A", "m1", 0.6, repeat=2, cost_usd=0.01, tokens=150),
        row("q1", "A", "m2", 0.6, repeat=2, cost_usd=0.03, tokens=150),
    ]
    s = r.analyse(rows, "A", reps=50).summaries["A"]
    assert s.cost_per_question == pytest.approx(0.04)
    assert s.tokens_per_question == pytest.approx(300)


def test_failures_without_a_kind_are_flagged():
    rows = [row("q1", "A", "m1", 0.6), row("q1", "A", "m2", None), row("q1", "B", "m1", None, error="timeout"),
            row("q1", "B", "m2", 0.7)]
    an = r.analyse(rows, "A", reps=50)
    assert any("without a kind" in w and "A: 1" in w for w in an.warnings)
    assert an.arms["B"].errors == {"timeout": 1}



# ---------------------------------------------------------------- several arms, control, labels (B-66, protocol §5/§6)
BASIC_RULE = r.DecisionRule(
    name="test rule", max_reliability_increase=None, max_failure_rate_increase=None, kill_if_point_at_most=None
)


def arm_rows(noise, n=160, seed=11, **kw):
    return [r.parse_row(x) for x in synthetic_rows(n_questions=n, n_clusters=n // 2, arm_noise=noise, seed=seed, **kw)]


def test_family_level_is_bonferroni():
    assert r.family_level(1) == pytest.approx(0.95)
    assert r.family_level(3) == pytest.approx(1 - 0.05 / 3)  # 98.3%
    assert r.family_level(10) == pytest.approx(0.995)
    assert r.family_level(0) == pytest.approx(0.95)


def test_three_candidates_get_a_wider_ci_than_one():
    rows = arm_rows({"A": 1.0, "B": 0.6, "C": 0.7, "D": 0.8})
    one = r.analyse(rows, "A", reps=1000, decision_arms=["B"]).comparisons["B"]["log"]
    three = r.analyse(rows, "A", reps=1000).comparisons["B"]["log"]
    assert one.level == pytest.approx(0.95) and three.level == pytest.approx(1 - 0.05 / 3)
    assert one.mean_diff == three.mean_diff
    assert three.ci_high - three.ci_low > one.ci_high - one.ci_low
    assert "@98.3%" in r._ci(three)


def test_control_arm_does_not_count_towards_m():
    rows = arm_rows({"A": 1.0, "B": 0.6, "C": 1.0})
    an = r.analyse(rows, "A", reps=300, control="C")
    assert an.candidates == ["B"] and an.level == pytest.approx(0.95)
    assert an.comparisons["B"]["log"].level == pytest.approx(0.95)
    assert "C" in an.comparisons  # still shown, descriptive
    assert set(an.control_comparisons) == {"B"} and an.control_comparisons["B"].baseline == "C"


def test_win_over_the_baseline_is_not_adopted_unless_it_also_beats_the_control():
    # B and the control are equally good: both beat A, B does not beat the control
    same = r.analyse(arm_rows({"A": 1.5, "B": 0.3, "C": 0.3}), "A", reps=500, rule=BASIC_RULE, control="C")
    assert same.comparisons["B"]["log"].ci_low > 0
    d = same.decisions["B"]
    assert d.verdict != "adopt"
    ctl = [c for c in d.checks if "compute-matched control" in c.condition]
    assert len(ctl) == 1 and not ctl[0].met
    # the control is much worse than B: now B is adopted
    better = r.analyse(arm_rows({"A": 1.5, "B": 0.3, "C": 1.5}), "A", reps=500, rule=BASIC_RULE, control="C")
    assert better.decisions["B"].verdict == "adopt"
    assert all(c.met for c in better.decisions["B"].checks if "compute-matched control" in c.condition)


def test_no_control_means_no_control_check():
    an = r.analyse(arm_rows({"A": 1.5, "B": 0.3}), "A", reps=300, rule=BASIC_RULE)
    assert not any("control" in c.condition for c in an.decisions["B"].checks)


def test_control_must_be_a_real_other_arm():
    rows = arm_rows({"A": 1.0, "B": 0.6})
    with pytest.raises(ValueError, match="control"):
        r.analyse(rows, "A", reps=50, control="A")
    with pytest.raises(ValueError, match="control"):
        r.analyse(rows, "A", reps=50, control="Z")
    with pytest.raises(ValueError, match="compute-matched control"):
        r.analyse(arm_rows({"A": 1.0, "B": 0.6, "C": 1.0}), "A", reps=50, control="C", decision_arms=["C"])


def test_stricter_bar_widens_the_same_interval():
    """Same data, same point estimate: ten candidates judge it at 99.5% instead of 95%, so the interval is wider."""
    rows = arm_rows({"A": 1.0, "B": 0.7}, n=80)
    prep = r.prepare(rows)
    sa = prep.arms["B"].question_scores(r.METRICS["log"], prep.outcomes)
    sb = prep.arms["A"].question_scores(r.METRICS["log"], prep.outcomes)

    def cmp(level):
        return r.paired_compare(sa, sb, prep.clusters, r.METRICS["log"], arm="B", baseline="A", reps=4000, level=level)

    alone, crowded = cmp(0.95), cmp(r.family_level(10))
    assert crowded.ci_low < alone.ci_low and crowded.ci_high > alone.ci_high
    assert crowded.mean_diff == alone.mean_diff


def test_calls_and_dollars_per_question_per_arm():
    rows = [
        row("q1", "A", "m1", 0.6, cost_usd=0.01), row("q1", "A", "m2", 0.6, cost_usd=0.01),
        row("q1", "B", "m1", 0.6, cost_usd=0.02, calls=3), row("q1", "B", "m2", 0.6, cost_usd=0.02, calls=3),
        row("q1", "B", "aggregate", 0.6, cost_usd=0.01, calls=1),  # an auditor call attached to the aggregate row
    ]
    an = r.analyse(rows, "A", reps=50)
    assert an.summaries["A"].calls_per_question == pytest.approx(2.0)  # counted: one per member row
    assert an.summaries["A"].calls_source == "one per member row"
    assert an.summaries["B"].calls_per_question == pytest.approx(7.0)  # from the calls column
    assert an.summaries["B"].cost_per_question == pytest.approx(0.05)
    md = r.render_report(an)
    assert "calls per question" in md and "7.0" in md
    assert any("`calls` column" in w and "member-row counts" in w for w in an.warnings)


def test_calls_are_per_repeat_like_cost():
    rows = [row("q1", "A", "m1", 0.6, repeat=1, calls=2), row("q1", "A", "m1", 0.6, repeat=2, calls=2)]
    assert r.analyse(rows, "A", reps=50).summaries["A"].calls_per_question == pytest.approx(2.0)


def test_warns_when_an_arm_uses_more_calls_without_a_control_and_when_the_control_is_not_matched():
    rows = []
    for i in range(4):
        rows += [
            row(f"q{i}", "A", "m1", 0.6, calls=1), row(f"q{i}", "B", "m1", 0.7, calls=4), row(f"q{i}", "C", "m1", 0.6, calls=2),
        ]
    no_ctl = r.analyse(rows, "A", reps=50, decision_arms=["B"])
    assert any("no compute-matched control" in w and "B makes 4.0" in w for w in no_ctl.warnings)
    off = r.analyse(rows, "A", reps=50, control="C", decision_arms=["B"])
    assert any("not compute-matched" in w for w in off.warnings)
    assert not any("no compute-matched control was given" in w for w in off.warnings)


def test_next_step_screen_win_by_tier():
    c = comparison(0.05, 0.03, 0.07)
    t1 = r.next_step_words("adopt", c, r.EXP005_RULE, "screen", 1)
    t2 = r.next_step_words("adopt", c, r.EXP005_RULE, "screen", 2)
    assert "goes live at once" in t1 and "shadow" in t1
    assert "before it goes live" in t2 and "Tier 2" in t2
    assert "No tier given" in r.next_step_words("adopt", c, r.EXP005_RULE, "screen", None)


def test_next_step_narrow_win_and_inconclusive_get_one_rerun():
    narrow = comparison(0.03, 0.005, 0.055)  # CI excludes 0 but the lower end is below the 0.015 smallest effect
    assert "Narrow win" in r.next_step_words("adopt", narrow, r.EXP005_RULE, "screen", 1)
    assert "rerun" in r.next_step_words("inconclusive", comparison(0.01, -0.02, 0.04), r.EXP005_RULE, "screen", 2)


def test_next_step_rerun_decides_and_kill_is_never_rerun():
    c = comparison(0.05, 0.03, 0.07)
    assert "counts as the confirmation" in r.next_step_words("adopt", c, r.EXP005_RULE, "rerun", 2)
    assert "Dropped" in r.next_step_words("inconclusive", comparison(0.01, -0.02, 0.04), r.EXP005_RULE, "rerun", 2)
    killed = r.next_step_words("kill", comparison(0.001, -0.01, 0.01), r.EXP005_RULE, "screen", 1)
    assert "Dropped" in killed and "not rerun" in killed


def test_next_step_failed_confirmation_by_tier():
    c = comparison(0.0, -0.02, 0.02)
    assert "back to the old config" in r.next_step_words("inconclusive", c, r.EXP005_RULE, "confirm", 1)
    assert "does not go live" in r.next_step_words("inconclusive", c, r.EXP005_RULE, "confirm", 2)
    with pytest.raises(ValueError):
        r.next_step_words("adopt", c, r.EXP005_RULE, "bogus", 1)
    with pytest.raises(ValueError):
        r.next_step_words("adopt", c, r.EXP005_RULE, "screen", 3)


def test_labels_never_change_the_verdict():
    rows = arm_rows({"A": 1.5, "B": 0.3})
    verdicts = {
        (k, t): r.analyse(rows, "A", reps=300, rule=BASIC_RULE, run_kind=k, tier=t).decisions["B"].verdict
        for k in r.RUN_KINDS
        for t in (None, 1, 2)
    }
    assert len(set(verdicts.values())) == 1


def test_cli_multi_arm_report(tmp_path):
    rows = synthetic_rows(n_questions=60, n_clusters=30, arm_noise={"A": 1.5, "B": 0.4, "C": 1.5, "D": 1.0}, seed=8)
    path = tmp_path / "t.csv"
    write_csv(rows, path)
    out = tmp_path / "rep.md"
    assert r.main([
        str(path), "--baseline", "A", "--name", "T", "--rule", "exp005", "--reps", "400", "--out", str(out),
        "--arms", "B", "--arms", "D", "--control", "C", "--tier", "2", "--run-kind", "rerun", "--label", "rerun of EXP-004",
    ]) == 0
    md = out.read_text(encoding="utf-8")
    assert "CI level 97.5%" in md and "(B, D)" in md
    assert "compute-matched control | C" in md
    assert "deciding rerun on fresh questions" in md and "rerun of EXP-004" in md
    assert "Against the compute-matched control C" in md
    assert "What happens next" in md and "calls per question" in md
