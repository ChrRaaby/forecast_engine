"""Spiky counts arm S1, prompt variant "per-value-discrete-2026-11-15": a probability per value on discrete questions with at most
25 values; every other question gets the live prompt and today's parsing. Offline; synthetic only."""
import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from evals import exp010
from evals import prompt_archive as pa
from forecast_engine import prompt_versions
from forecast_engine.clock import FixedClock
from forecast_engine.config import DEFAULT_CONFIG
from forecast_engine.core import forecast
from forecast_engine.parsing import ParseError
from forecast_engine.prompt_variants import per_value_discrete_2026_11_15 as pv
from forecast_engine.schema import LlmCall, QuestionSnapshot, ResearchBundle, ResearchItem

VERSION = pv.VERSION
AS_OF = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
BASE = dict(question_id=71, post_id=72, question_text="How many storms?", background_info="bg", resolution_criteria="rc",
            unit_of_measure="storms")


def disc(lo=21.5, hi=26.5, nlo=22.0, nhi=26.0, open_lo=False, open_hi=True, **kw):
    """Values 22..26 (5 values) by default, upper bound open, like MiniBench 46380."""
    return QuestionSnapshot(question_type="discrete", lower_bound=lo, upper_bound=hi, nominal_lower_bound=nlo,
                            nominal_upper_bound=nhi, open_lower_bound=open_lo, open_upper_bound=open_hi, **{**BASE, **kw})


def test_values_come_from_the_snapshot_bounds():
    assert pv.values(disc()) == ["22", "23", "24", "25", "26"]
    assert pv.labels(disc()) == ["22", "23", "24", "25", "26", "above"]
    q = disc(lo=2.55, hi=3.65, nlo=2.6, nhi=3.6, open_lo=True)  # 46099: 11 values, step 0.1, float noise in the bounds
    assert pv.values(q) == [f"{x / 10:.1f}" for x in range(26, 37)]
    assert pv.labels(q)[0] == "below" and pv.labels(q)[-1] == "above"


@pytest.mark.parametrize("q", [
    disc(lo=-0.5, hi=25.5, nlo=0.0, nhi=25.0),  # 26 values: more than 25
    disc(nlo=None),  # no nominal bounds: values unknown
    disc(nlo=21.9),  # bounds give no whole number of steps
    replace(disc(), question_type="numeric"),
])
def test_questions_outside_the_arm_get_the_live_prompt(q):
    assert not pv.applies(q)
    assert pv.forecast_prompt(q, "R", "2026-10-05") == __import__("forecast_engine.prompts").prompts.forecast_prompt(q, "R",
                                                                                                                     "2026-10-05")
    assert prompt_versions.answer_path(VERSION, q) is None


def test_live_and_prompt_only_variants_have_no_answer_path():
    assert prompt_versions.answer_path(prompt_versions.LIVE, disc()) is None
    assert prompt_versions.answer_path("range-calibrated-2026-11-14", disc()) is None
    assert prompt_versions.answer_path(VERSION, disc()) is pv


def test_per_value_prompt_lists_every_value_line():
    p = pv.forecast_prompt(disc(), "R", "2026-10-05")
    assert "The outcome will be one of these values: 22, 23, 24, 25, 26." in p
    assert '"Above 26" means any outcome higher than 26.' in p and "Below" not in p
    for line in ("22: XX%", "26: XX%", "Above 26: XX%"):
        assert line in p
    assert "Percentile 10" not in p


def test_frozen_copy_renders_what_the_variant_renders():
    items = [ResearchItem(source="s", text="t", title="h", url=None, published_at=None)]
    for q in (disc(), disc(open_lo=True), disc(lo=-0.5, hi=25.5, nlo=0.0, nhi=25.0)):
        assert pa.ARCHIVE[VERSION].build(q, items, AS_OF) == pa.variant_prompt(VERSION, q, items, AS_OF)
    for _, q, items, as_of in pa.fixture_cases():  # no fixture case is a <= 25-value discrete question: all live text
        assert pa.variant_prompt(VERSION, q, items, as_of) == pa.live_prompt(q, items, as_of)


def test_parse_takes_the_last_line_per_value_and_normalises():
    assert pv.parse_answer(disc(), "24: 100%") == pytest.approx({"22": 0, "23": 0, "24": 1, "25": 0, "26": 0, "above": 0})
    text = "draft\n22: 50%\n...\nFinal:\n**22: 10%**\n- 23: 30%\n24: 40\nAbove 26: 20%\n27: 99%\nbelow 22: 5%"
    got = pv.parse_answer(disc(), text)
    assert got == pytest.approx({"22": 0.1, "23": 0.3, "24": 0.4, "25": 0.0, "26": 0.0, "above": 0.2})


@pytest.mark.parametrize("text", ["Percentile 10: 22", "22: 30%", "22: 0%\n23: 0%", "22: -5%\n23: 105%",
                                  "22: 60%\n23: 60%"])
def test_unusable_answers_are_parse_errors(text):
    with pytest.raises(ParseError):
        pv.parse_answer(disc(), text)


def test_aggregate_is_member_mean_with_a_half_percent_floor():
    q = disc()
    a = {"22": 1.0, "23": 0.0, "24": 0.0, "25": 0.0, "26": 0.0, "above": 0.0}
    b = {"22": 0.0, "23": 1.0, "24": 0.0, "25": 0.0, "26": 0.0, "above": 0.0}
    agg = pv.aggregate(q, [a, b])
    ps = agg["probabilities"]
    assert agg["labels"] == pv.labels(q) and sum(ps) == pytest.approx(1.0)
    assert min(ps) == pytest.approx(pv.FLOOR) and ps[0] == pytest.approx(ps[1]) and ps[0] > 0.48


def test_cdf_from_values_meets_metaculus_limits_and_scores_the_true_value():
    q = disc()
    agg = pv.aggregate(q, [{"22": 0.0, "23": 0.0, "24": 1.0, "25": 0.0, "26": 0.0, "above": 0.0}])
    cdf = pv.cdf_from_values(agg, q.open_lower_bound, q.open_upper_bound)
    axis = exp010.Axis("discrete", 21.5, 26.5, False, True, None, 6)
    assert len(cdf) == 6 and cdf[0] == 0.0 and 1 - cdf[-1] == pytest.approx(pv.FLOOR)
    assert exp010.cdf_problems(cdf, axis, 1.0) == []
    hit = exp010.score_cdf(cdf, exp010.outcome_location("24", axis), axis, metrics=("baseline",))["baseline"]
    miss = exp010.score_cdf(cdf, exp010.outcome_location("22", axis), axis, metrics=("baseline",))["baseline"]
    assert hit > 50 > 0 > miss


class PerValueLlm:
    def __init__(self, answers):
        self.answers, self.i = answers, 0

    async def complete(self, *, model, prompt, purpose, max_tokens, reasoning_effort=None):
        out = self.answers[self.i % len(self.answers)]
        self.i += 1
        return LlmCall(purpose=purpose, provider="fake", model=model, params={}, prompt=prompt, requested_at=AS_OF, output=out,
                       cost_usd=0.0)


def _run(q, answers, version=VERSION):
    cfg = replace(DEFAULT_CONFIG, prompt_version=version, polarity_check_model=None)
    bundle = ResearchBundle(as_of=AS_OF, providers=["s"], items=[ResearchItem(source="s", text="t", title=None, url=None,
                                                                                 published_at=None)], calls=[])
    return asyncio.run(forecast(q, FixedClock(AS_OF), bundle, cfg, PerValueLlm(answers)))


def test_core_uses_the_variants_answer_path_and_stores_a_per_value_aggregate():
    rec = _run(disc(), ["24: 100%", "24: 50%\n25: 50%", "garbage"])
    assert [f.parse_error is None for f in rec.forecasters] == [True, True, False]
    assert pv.is_per_value(rec.aggregate)
    probs = dict(zip(rec.aggregate["labels"], rec.aggregate["probabilities"]))
    assert probs["24"] > probs["25"] > probs["22"] == pytest.approx(pv.FLOOR)
    assert json.loads(json.dumps(rec.to_dict()))["aggregate"]["kind"] == pv.KIND  # survives the record writer


def test_core_keeps_todays_path_for_a_question_outside_the_arm():
    q = disc(lo=-0.5, hi=25.5, nlo=0.0, nhi=25.0)
    rec = _run(q, ["\n".join(f"Percentile {p}: {v}" for p, v in zip((10, 20, 40, 60, 80, 90), (1, 2, 4, 6, 8, 9)))])
    assert isinstance(rec.aggregate, list) and rec.aggregate[0] == (0.1, 1.0)
