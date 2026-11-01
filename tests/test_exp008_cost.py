"""tools/exp008_cost.py (F-14 groundwork): known inputs → known outputs, the free-tier overflow case, the power arithmetic, and a
cross-check that the script's search counts match what forecast_engine/research_followup.py actually does under its cap.
Offline; pure arithmetic plus the existing fake critic and fake Gemini."""
import importlib.util
import math
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from forecast_engine.config import FollowupConfig
from tests.test_research_followup import FakeLlm, FakeSearch, _cfg, _gaps, _run

_spec = importlib.util.spec_from_file_location("exp008_cost", Path(__file__).resolve().parents[1] / "tools" / "exp008_cost.py")
ec = importlib.util.module_from_spec(_spec)
sys.modules["exp008_cost"] = ec  # dataclasses look the module up
_spec.loader.exec_module(ec)

A, B, C, D = (s for s in ec.SETTINGS if s.name in "abcd")


def _simple(**kw):
    """Round numbers: every search runs exactly 2 queries and costs $0.004 in tokens ($0.032 with the fee at list price); the
    critic costs $0.004; forecasting is left out; 250 questions a month."""
    base = dict(queries_dist=((2, 1),), followup_queries_dist=((2, 1),), search_tokens_in=0, search_tokens_out=1600,
                critic_usd=0.004, gaps_per_q=4, forecast_usd_base=0.0, forecast_usd_per_search=0.0,
                questions_per_week=250 / ec.WEEKS_PER_MONTH)
    base.update(kw)
    return ec.Inputs(**base)


def test_defaults_match_the_measured_inputs_and_config():
    inp = ec.Inputs()
    assert sum(c for _, c in inp.queries_dist) == 276  # PR #13 comment: N = 276 live Gemini calls
    assert round(inp.mean_queries, 2) == 1.85
    f = FollowupConfig()
    assert (inp.grounding_usd_per_query, inp.search_cost_prior_usd) == (f.grounding_usd_per_query, f.search_cost_prior_usd)
    assert (A.cap_usd, A.booked_fee_per_query, A.max_gaps) == (f.max_cost_usd_per_q, None, f.max_gaps)
    assert inp.free_per_month == 5000


def test_setting_a_cap_fits_one_search_at_two_queries():
    inp = _simple()
    assert inp.search_token_usd == pytest.approx(0.004)
    b = ec.arm_expectation(inp, A, "gaps")  # critic 0.004 + prior 0.032 fits; 0.004 + 2 × 0.032 = 0.068 > 0.06 doesn't
    assert (b.searches, b.queries) == (1, 2)
    assert b.booked_usd == pytest.approx(0.036)
    assert b.real_usd_free == pytest.approx(0.008)
    d = ec.arm_expectation(inp, A, "control")  # 0.032 fits; 0.064 > 0.06 doesn't
    assert (d.searches, d.booked_usd) == (1, pytest.approx(0.032))


def test_settings_b_and_c_let_d_search_more_than_b():
    inp = _simple()
    for s in (B, C):
        b, d = ec.arm_expectation(inp, s, "gaps"), ec.arm_expectation(inp, s, "control")
        assert (b.searches, d.searches) == (4, 5)  # the critic lists 4 gaps; D always runs max_gaps = 5
    c_b = ec.arm_expectation(inp, C, "gaps")
    assert c_b.booked_usd == pytest.approx(0.004 + 4 * 0.004)  # (c) books no grounding fee
    assert c_b.real_usd_list == pytest.approx(0.004 + 4 * 0.004 + 8 * 0.014)  # what it costs once the free tier is used up
    assert ec.arm_expectation(inp, B, "gaps").booked_usd == pytest.approx(0.004 + 4 * 0.032)
    # option (d): at most 3 searches, so all three arms match while the critic lists >= 3 gaps
    assert [ec.arm_expectation(inp, D, m).searches for m in inp.arms] == [3, 3, 3]


def test_expectation_over_a_query_mix():
    # half the searches run 1 query ($0.018), half 3 ($0.046); setting (a), arm D, cap 0.06:
    # first search always (prior 0.032); after a 1-query search the next expected cost is 0.018: 0.036 fits, then the third
    # needs spent + dearest <= 0.06; after a 3-query search 0.046 + 0.046 > 0.06 stops.
    inp = _simple(followup_queries_dist=((1, 1), (3, 1)))
    d = ec.arm_expectation(inp, A, "control")
    # paths: q1=3 → 1 search. q1=1 → second search: q2=1 → spent 0.036, dearest 0.018 → third fits (0.054); third q=any → stop
    # at 3 (0.054 + max >= 0.072 > 0.06). q2=3 → spent 0.064 → stop at 2. E = 0.5·1 + 0.25·2 + 0.25·3 = 1.75
    assert d.searches == pytest.approx(1.75)


def test_month_inside_the_free_tier():
    m = ec.month(_simple(), A)  # live 2 + arms 3 × 2 = 8 queries per question × 250 = 2,000
    assert m.live_queries == pytest.approx(500) and m.exp_queries == pytest.approx(1500)
    assert m.free_used_share == pytest.approx(0.4)
    assert m.overflow_day is None and m.google_bill == 0
    assert m.booked_per_month == pytest.approx(250 * (0.036 * 2 + 0.032))
    assert m.real_per_month == pytest.approx(250 * (0.008 * 2 + 0.004))


def test_free_tier_overflow_turns_c_into_b_partway_through_the_month():
    m = ec.month(_simple(), C)  # live 2 + (4 + 4 + 5) × 2 = 28 queries per question × 250 = 7,000
    assert m.live_queries + m.exp_queries == pytest.approx(7000)
    assert m.free_used_share == pytest.approx(1.4)
    assert m.overflow_day == pytest.approx(5000 / 7000 * ec.DAYS_PER_MONTH)
    assert m.google_bill == pytest.approx(2000 * 0.014)  # $28 that the records (fee booked at $0) never show
    assert m.google_bill_without_exp == 0
    assert m.booked_per_month == pytest.approx(250 * 0.060)
    assert m.real_per_month == pytest.approx(250 * 0.060 + 28)
    # counted per grounded call instead, the same month stays inside: 250 live + 250 × 13 searches = 3,500 calls
    p = ec.month(replace(_simple(), free_tier_unit="prompt"), C)
    assert p.free_used_share == pytest.approx(0.7) and p.google_bill == 0
    # the experiment is only charged the part of the bill it causes
    o = ec.month(replace(_simple(), other_queries_per_month=6000), C)
    assert o.google_bill_without_exp == pytest.approx((500 + 6000 - 5000) * 0.014)
    assert o.real_per_month == pytest.approx(250 * 0.060 + 6500 * 0.014)
    with pytest.raises(ValueError):
        ec.month(replace(_simple(), free_tier_unit="request"), C)


def test_run_cap_counts_questions_before_the_cap_stops_new_ones():
    inp = _simple(live_usd_per_q=0.5, cheap_shadow_usd_per_q=0.0, run_cap_usd=1.0, max_questions_per_run=20)
    m = ec.month(inp, B)  # 0.5 + B 0.132 + C 0.132 + D 0.160 = 0.924 per question: 1st starts at $0, 2nd at $0.924 < $1
    assert m.questions_per_run_cap == 2
    assert ec.month(replace(inp, run_cap_usd=100.0), B).questions_per_run_cap == 20


@pytest.mark.parametrize("cap,fee,n_queries", [(0.06, 0.014, 2), (0.06, 0.014, 1), (0.20, 0.014, 2), (0.06, 0.0, 2),
                                               (0.06, 0.014, 0)])
@pytest.mark.parametrize("mode", ["gaps", "control"])
def test_script_search_count_matches_the_real_follow_up_step(cap, fee, n_queries, mode):
    """The script replays research_followup._run's cap rule; check it against the real code with the fake critic/Gemini."""
    gaps = ["g1", "g2", "g3", "g4"]
    out = _run(_cfg(critic_mode=mode, max_cost_usd_per_q=cap, grounding_usd_per_query=fee),
               FakeLlm([_gaps(*gaps)], cost=0.001), FakeSearch(n_queries=n_queries, tokens_out=1000))
    real = len([c for c in out.calls if c.purpose == "research_followup_search"])
    inp = ec.Inputs(followup_queries_dist=((n_queries, 1),), search_tokens_in=300, search_tokens_out=1000, critic_usd=0.001,
                    gaps_per_q=len(gaps), grounding_usd_per_query=0.014)
    s = ec.Setting("t", "test", cap, fee)
    est = ec.arm_expectation(inp, s, mode)
    assert est.searches == real
    assert est.booked_usd == pytest.approx(sum(c.cost_usd for c in out.calls[1:]))  # the bundle's own live call is $0.002


def test_mde_reproduces_the_protocol_and_the_card():
    assert ec.z(1 - 0.05 / 2) + ec.z(0.8) == pytest.approx(2.80, abs=0.01)  # protocol §5 factor, one arm
    assert ec.mde(0.06, 100, m=1) == pytest.approx(0.017, abs=0.0005)  # protocol §5 illustration
    assert ec.mde(0.06, 150, m=2) == pytest.approx(0.015, abs=0.0005)  # EXP-008 card, 97.5% level
    assert ec.mde(0.06, 100, m=2) == pytest.approx(0.0185, abs=0.0005)  # card: "≈ 0.019"
    assert ec.mde(0.12, 150) == pytest.approx(2 * ec.mde(0.06, 150))


def test_outcome_probabilities_are_a_partition_and_behave():
    for eff in (-0.02, 0.0, 0.015, 0.03, 0.1):
        p = ec.outcome_probs(eff, 0.12, 120)
        assert sum(p.values()) == pytest.approx(1.0)
        assert all(0 <= v <= 1 for v in p.values())
    assert ec.outcome_probs(0.0, 0.12, 120)["adopt"] < 0.01
    assert ec.outcome_probs(0.2, 0.12, 120)["adopt"] > 0.99
    assert ec.outcome_probs(-0.1, 0.12, 120)["kill"] > 0.99


def test_resolved_questions_by_date():
    on = date(2026, 11, 15)
    assert ec.n_resolved_by(on, date(2027, 1, 31), per_week=13, lag_weeks=2) == pytest.approx(13 * (77 / 7 - 2))
    assert ec.n_resolved_by(on, date(2026, 11, 20), per_week=13, lag_weeks=2) == 0
    assert ec.date_for_n(on, 150, per_week=15, lag_weeks=2) == date(2027, 2, 7)  # 2 + 10 weeks = 84 days


def test_cli_prints_both_sections(capsys):
    ec.main(["--questions_per_week", "80", "--free_tier_unit", "prompt", "--followup_queries_dist", "1:3,2:1"])
    out = capsys.readouterr().out
    assert "80 questions/week" in out and "counted per prompt" in out
    assert "| (c)" in out and "MDE" in out
    assert not math.isnan(ec.Inputs().search_token_usd)
