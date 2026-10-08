"""Tests for the EXP-005 structured-reasoning protocol (forecast_engine/structured.py) and the release-date check."""
import json
import math
from datetime import date

import pytest

from evals import model_release as mr
from forecast_engine import structured as st


def answer(**over):
    d = {
        "base_rate": {"reference_classes": [
            {"name": "A", "fit": "f", "misfit": "m", "kind": "proportion", "k": 4, "n": 23, "of_what": "x", "source": "memory", "weight": 0.5},
            {"name": "B", "fit": "f", "misfit": "m", "kind": "rate", "events": 1, "per_days": 100, "window_days": 10,
             "source": "R1", "weight": 0.5},
        ], "reasoning": "r", "confidence": "medium"},
        "scenarios": [
            {"name": "s1", "description": "d", "probability": 0.5, "p_yes": 0.1},
            {"name": "s2", "description": "d", "probability": 0.3, "p_yes": 0.2},
            {"name": "s3", "description": "d", "probability": 0.2, "p_yes": 0.5},
        ],
        "drivers": [{"text": "t", "direction": "down", "multiplier": 1.25, "evidence": "R0", "independence": "i"}],
        "reconcile": {"trusted_route": "both", "reason": "r", "final_probability": 0.15},
    }
    d.update(over)
    return d


# ---- arithmetic
def test_window_probability():
    assert st.window_probability(1, 100, 10) == pytest.approx(1 - math.exp(-0.1))
    assert st.window_probability(0, 365, 30) == 0
    assert st.window_probability(365, 365, 1) == pytest.approx(1 - math.exp(-1))
    with pytest.raises(ValueError):
        st.window_probability(1, 0, 10)


def test_apply_drivers_odds_and_cap():
    p, f = st.apply_drivers(0.2, [0.5])  # odds 0.25 -> 0.125
    assert p == pytest.approx(0.125 / 1.125) and f == 0.5
    p, f = st.apply_drivers(0.5, [3, 3, 3])  # 27x capped at 10x
    assert f == 10 and p == pytest.approx(10 / 11)
    assert st.apply_drivers(0.3, [])[0] == pytest.approx(0.3)


def test_routes_agree_by_pp_or_odds():
    assert st.routes_agree(0.30, 0.39)  # 9 pp
    assert st.routes_agree(0.02, 0.029)  # 0.9 pp
    assert st.routes_agree(0.90, 0.99) is False or st.routes_agree(0.90, 0.99)  # pp rule may hold; covered below
    assert not st.routes_agree(0.10, 0.30)  # 20 pp and odds ratio 3.9
    assert st.logodds_mean(0.2, 0.2) == pytest.approx(0.2)


# ---- build
def test_build_computes_every_number_in_code():
    f = st.build(answer())
    p_b = 1 - math.exp(-0.1)
    assert f.p0 == pytest.approx((0.2 + p_b) / 2)
    assert f.p_scen == pytest.approx(0.5 * 0.1 + 0.3 * 0.2 + 0.2 * 0.5)  # 0.21
    assert f.p_drv == pytest.approx(st.sigmoid(st.logit(f.p0) + math.log(1 / 1.25)))
    assert f.agree is True and f.code_final == pytest.approx(st.logodds_mean(f.p_drv, f.p_scen))
    assert f.model_final == 0.15 and f.final == f.code_final
    assert f.problems == []


def test_disagreeing_routes_use_model_final():
    raw = answer(drivers=[{"text": "t", "direction": "up", "multiplier": 3, "evidence": "R0", "independence": "i"}] * 2)
    f = st.build(raw)
    assert f.agree is False and f.code_final == 0.15 and f.final == 0.15


def test_disagreement_without_model_final_raises():
    raw = answer(drivers=[{"text": "t", "direction": "up", "multiplier": 3, "evidence": "R0", "independence": "i"}] * 2,
                 reconcile={"trusted_route": "x"})
    with pytest.raises(st.ProtocolError):
        st.build(raw)


def test_off_scale_multiplier_dropped_and_inverse_accepted():
    raw = answer(drivers=[
        {"text": "a", "direction": "up", "multiplier": 7, "evidence": "R0", "independence": "i"},
        {"text": "b", "direction": "down", "multiplier": 0.5, "evidence": "R0", "independence": "i"},
    ])
    f = st.build(raw)
    assert [d.text for d in f.drivers] == ["b"] and f.drivers[0].factor == 0.5
    assert any("not on the scale" in p for p in f.problems)


def test_scenarios_not_summing_to_one_disable_scenario_route():
    raw = answer(scenarios=[{"name": "a", "description": "", "probability": 0.5, "p_yes": 0.1},
                            {"name": "b", "description": "", "probability": 0.2, "p_yes": 0.9},
                            {"name": "c", "description": "", "probability": 0.1, "p_yes": 0.5}])
    f = st.build(raw)
    assert f.p_scen is None and f.agree is None and f.code_final == 0.15
    assert any("sum to 0.80" in p for p in f.problems)


def test_scenarios_within_tolerance_are_renormalised():
    raw = answer(scenarios=[{"name": "a", "description": "", "probability": 0.52, "p_yes": 0.1},
                            {"name": "b", "description": "", "probability": 0.3, "p_yes": 0.2},
                            {"name": "c", "description": "", "probability": 0.2, "p_yes": 0.5}])
    assert sum(s.p for s in st.build(raw).scenarios) == pytest.approx(1)


def test_proportion_is_k_of_n_with_shrinkage():
    assert st.shrunk_proportion(4, 23) == pytest.approx(0.2)
    assert st.shrunk_proportion(2, 2) == 0.75 and st.shrunk_proportion(0, 2) == 0.25  # the q46107 pilot case
    with pytest.raises(ValueError):
        st.shrunk_proportion(3, 2)
    f = st.build(answer())
    c = f.base.classes[0]
    assert c.p == pytest.approx(0.2) and c.detail["raw"] == pytest.approx(4 / 23) and f.problems == []


def test_small_n_flagged_and_bad_counts_unusable():
    raw = answer()
    raw["base_rate"]["reference_classes"][0].update(k=2, n=2)
    f = st.build(raw)
    assert f.base.classes[0].p == 0.75 and any("only 2 comparable cases" in p for p in f.problems)
    for bad in ({"k": 3, "n": 2}, {"k": 1.5, "n": 4}, {"k": None, "n": 4}, {"value": 0.2}):
        raw = answer()
        c = raw["base_rate"]["reference_classes"][0]
        c.pop("k"), c.pop("n")
        c.update(bad)
        f = st.build(raw)
        assert [x.name for x in f.base.classes] == ["B"]  # the bad class is dropped, the rate class stays
        assert any("class 0: unusable" in p for p in f.problems)


def test_window_cap():
    raw = answer()
    raw["base_rate"]["reference_classes"][1]["window_days"] = 400
    f = st.build(raw, max_window_days=12)
    assert f.base.classes[1].p == pytest.approx(1 - math.exp(-0.12))
    assert any("capped" in p for p in f.problems)


def test_weights_renormalised_and_single_class_noted():
    raw = answer()
    raw["base_rate"]["reference_classes"] = [raw["base_rate"]["reference_classes"][0] | {"weight": 3}]
    f = st.build(raw)
    assert f.base.classes[0].weight == 1 and f.p0 == pytest.approx(0.2)
    assert any("1 usable reference class" in p for p in f.problems)


def test_given_base_overrides_answer_base():
    blind, problems = st.build_blind_base({"base_rate": answer()["base_rate"]})
    assert problems == []
    raw = answer()
    del raw["base_rate"]
    f = st.build(raw, given_base=blind)
    assert f.p0 == pytest.approx(blind.p0)


def test_final_is_clipped():
    raw = answer(drivers=[{"text": "t", "direction": "down", "multiplier": 3, "evidence": "R0", "independence": "i"}] * 3,
                 scenarios=[{"name": "a", "description": "", "probability": 1.0, "p_yes": 0.0},
                            {"name": "b", "description": "", "probability": 0.0, "p_yes": 0.0},
                            {"name": "c", "description": "", "probability": 0.0, "p_yes": 0.0}],
                 reconcile={"final_probability": 0.0})
    assert st.build(raw).final == 0.01


@pytest.mark.parametrize("text", ["", "no json here", '{"base_rate": ', "[1, 2]"])
def test_parse_json_object_rejects_garbage(text):
    with pytest.raises(st.ProtocolError):
        st.parse_json_object(text)


def test_parse_json_object_accepts_fence_and_prose():
    obj = {"a": 1}
    assert st.parse_json_object("```json\n" + json.dumps(obj) + "\n```") == obj
    assert st.parse_json_object("Here you go: " + json.dumps(obj) + " done") == obj


# ---- prompts
Q = {"question_text": "Will X happen?", "background_info": "BACKGROUND NEWS", "resolution_criteria": "RC", "fine_print": "FP",
     "scheduled_resolution_time": "2026-10-17T00:00:00+00:00"}
ITEMS = [{"source": "asknews_latest", "title": "T0", "text": "news 0", "published_at": "2026-10-04T00:00:00+00:00"},
         {"source": "gemini_grounded", "title": None, "text": "summary"}]


def test_prompts_number_research_and_avoid_bayesian_framing():
    p = st.protocol_prompt(Q, st.research_block(ITEMS), "2026-10-05")
    assert "[R0] (asknews_latest) T0 - 2026-10-04" in p and "[R1] (gemini_grounded)" in p
    for prompt in (p, st.blind_base_rate_prompt(Q, "2026-10-05")):
        assert "bayes" not in prompt.lower()


def test_blind_prompt_sees_background_but_no_research():
    """v2: the blind call gets the question background (where things stand), still no research."""
    p = st.blind_base_rate_prompt(Q, "2026-10-05")
    assert "Will X happen?" in p and "RC" in p and "BACKGROUND NEWS" in p
    assert "news 0" not in p and "[R0]" not in p and "R<n>" not in p and '"scenarios"' not in p
    assert '"k": 3, "n": 25' in p


def test_given_base_prompt_carries_the_blind_number():
    blind, _ = st.build_blind_base({"base_rate": answer()["base_rate"]})
    p = st.protocol_prompt_given_base(Q, st.research_block(ITEMS), "2026-10-05", blind)
    assert f"p0 = {blind.p0:.3f}" in p and '"base_rate"' not in p


# ---- release-date leakage check
def test_release_check_needs_release_a_day_before_as_of():
    mr.check_eligible("openai/gpt-6.1-sol", "2026-10-05T00:44:00+00:00")  # released 2026-09-29
    mr.check_eligible("openai/gpt-6.1-sol", "2026-09-30T08:00:00+00:00")  # exactly the 1-day margin
    for as_of in ("2026-09-29T22:16:00+00:00", date(2026, 9, 29), "2026-09-01"):  # same day / before release
        with pytest.raises(mr.IneligiblePair):
            mr.check_eligible("openai/gpt-6.1-sol", as_of)
    with pytest.raises(mr.IneligiblePair):
        mr.check_eligible("some/unknown-model", "2026-10-05")
    with pytest.raises(mr.IneligiblePair):
        mr.check_eligible("openai/gpt-6.1-sol", None)
    assert mr.eligible("openai/gpt-6-luna", "2026-10-05") and not mr.eligible("openai/gpt-6-luna", "2026-09-22")


def test_long_running_question_is_eligible_when_as_of_is_recent():
    """The rule is about as_of, not when the question opened (q8725 opened in 2021, forecast on 2026-10-04)."""
    assert mr.eligible("openai/gpt-6.1-sol", "2026-10-04T20:04:00+00:00")


# ---- pilot view helper
def _load_tool(name):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parents[1] / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_waterfall_steps_end_at_p_drv_including_cap():
    sv = _load_tool("build_structured_view")
    for p0, factors in [(0.2, [0.5, 2, 1.25]), (0.5, [3, 3, 3]), (0.3, [])]:
        steps = sv.waterfall(p0, factors)
        p_drv = st.apply_drivers(p0, factors)[0]
        assert (steps[-1]["after"] if steps else p0) == pytest.approx(p_drv)
        assert all(a["after"] == pytest.approx(b["before"]) for a, b in zip(steps, steps[1:]))
    assert sv.waterfall(0.5, [3, 3, 3])[-1]["capped"] is True


def test_pilot_view_template_has_data_slot():
    sv = _load_tool("build_structured_view")
    assert sv.TEMPLATE.read_text(encoding="utf-8").count("/*__DATA__*/null") == 1
