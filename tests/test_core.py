import asyncio
import re
from datetime import datetime, timezone

import pytest

from forecast_engine.clock import FixedClock
from forecast_engine.config import DEFAULT_CONFIG
from forecast_engine.core import ForecastFailed, forecast
from forecast_engine.schema import LlmCall, QuestionSnapshot, ResearchBundle, ResearchItem

AS_OF = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
Q = QuestionSnapshot(
    question_id=1,
    post_id=2,
    question_type="binary",
    question_text="Will X happen by the resolution date?",
    resolution_criteria="Resolves Yes if X happens.",
)


class FakeLlm:
    def __init__(self, outputs: dict[str, str], cost: float | None = 0.001):
        self.outputs, self.cost, self.prompts = outputs, cost, []

    async def complete(self, *, model, prompt, purpose, max_tokens, reasoning_effort=None):
        self.prompts.append(prompt)
        out = self.outputs.get(model, "")
        return LlmCall(
            purpose=purpose, provider="openrouter", model=model, params={}, prompt=prompt,
            requested_at=AS_OF, output=out, tokens_in=100, tokens_out=50, cost_usd=self.cost,
            error=None if out else "empty completion",
        )


def _bundle():
    return ResearchBundle(as_of=AS_OF, providers=["test"], items=[ResearchItem(source="test", text="Some news.")], calls=[])


def _models():
    return [f.model for f in DEFAULT_CONFIG.forecasters]


def test_median_of_three_and_cost():
    m = _models()
    llm = FakeLlm({m[0]: "Probability: 20%", m[1]: "Probability: 60%", m[2]: "Probability: 40%"})
    llm.outputs[DEFAULT_CONFIG.polarity_check_model] = '{"refers_to": "YES", "why": "ok"}'
    rec = asyncio.run(forecast(Q, FixedClock(AS_OF), _bundle(), DEFAULT_CONFIG, llm))
    assert rec.aggregate == pytest.approx(0.4)
    assert rec.n_ok == 3
    assert rec.cost_usd == pytest.approx(0.006)  # 3 forecasts + 3 polarity checks
    assert all(f.check is not None for f in rec.forecasters) and rec.flags == []
    assert rec.cost_complete
    assert rec.config_hash == DEFAULT_CONFIG.config_hash()


def test_two_of_three_is_enough_one_is_not():
    m = _models()
    llm = FakeLlm({m[0]: "Probability: 20%", m[1]: "no answer", m[2]: "Probability: 40%"})
    rec = asyncio.run(forecast(Q, FixedClock(AS_OF), _bundle(), DEFAULT_CONFIG, llm))
    assert rec.aggregate == pytest.approx(0.3)
    assert rec.forecasters[1].parse_error
    llm = FakeLlm({m[0]: "Probability: 20%"})
    with pytest.raises(ForecastFailed) as exc:
        asyncio.run(forecast(Q, FixedClock(AS_OF), _bundle(), DEFAULT_CONFIG, llm))
    assert exc.value.record.errors


def test_missing_provider_cost_is_flagged():
    llm = FakeLlm({x: "Probability: 50%" for x in _models()}, cost=None)
    rec = asyncio.run(forecast(Q, FixedClock(AS_OF), _bundle(), DEFAULT_CONFIG, llm))
    assert not rec.cost_complete


@pytest.mark.parametrize(
    "qtype,extra",
    [
        ("binary", {}),
        ("multiple_choice", {"options": ("A", "B")}),
        ("numeric", {"lower_bound": 0.0, "upper_bound": 100.0, "unit_of_measure": "units"}),
    ],
)
def test_prompt_uses_injected_clock_and_no_other_dates(qtype, extra):
    """Protocol T5: the only date in a rendered prompt is the Clock's as-of date."""
    q = QuestionSnapshot(question_id=1, post_id=2, question_type=qtype, question_text="Will X?", **extra)
    llm = FakeLlm({})
    with pytest.raises(ForecastFailed):
        asyncio.run(forecast(q, FixedClock(AS_OF), _bundle(), DEFAULT_CONFIG, llm))
    prompt = llm.prompts[0]
    assert "Today is 2026-05-01." in prompt
    dates = set(re.findall(r"\b(?:19|20)\d\d-\d\d-\d\d\b", prompt))
    assert dates == {"2026-05-01"}


class PolarityLlm(FakeLlm):
    """Forecasts per model; the polarity checker answers NO for answers containing 'FOR-NO'."""

    async def complete(self, *, model, prompt, purpose, max_tokens, reasoning_effort=None):
        if purpose == "polarity_check":
            verdict = "NO" if "FOR-NO" in prompt else "YES"
            return LlmCall(purpose=purpose, provider="openrouter", model=model, params={}, prompt=prompt, requested_at=AS_OF,
                           output='{"refers_to": "%s", "why": "x"}' % verdict, cost_usd=0.0001)
        return await super().complete(model=model, prompt=prompt, purpose=purpose, max_tokens=max_tokens)


def test_polarity_check_excludes_a_member_answering_for_no():
    m = _models()
    llm = PolarityLlm({m[0]: "Probability: 20%", m[1]: "Status quo favors No. FOR-NO Probability: 78%", m[2]: "Probability: 17%"})
    rec = asyncio.run(forecast(Q, FixedClock(AS_OF), _bundle(), DEFAULT_CONFIG, llm))
    assert rec.forecasters[1].excluded and rec.forecasters[1].prediction == pytest.approx(0.78)  # kept in the record, not used
    assert rec.aggregate == pytest.approx((0.20 + 0.17) / 2)
    assert any("refers to NO" in f for f in rec.flags)


def test_two_members_answering_for_no_fail_the_question():
    m = _models()
    llm = PolarityLlm({m[0]: "FOR-NO Probability: 80%", m[1]: "FOR-NO Probability: 78%", m[2]: "Probability: 17%"})
    with pytest.raises(ForecastFailed):
        asyncio.run(forecast(Q, FixedClock(AS_OF), _bundle(), DEFAULT_CONFIG, llm))


def test_failed_polarity_check_keeps_the_member():
    m = _models()
    llm = FakeLlm({m[0]: "Probability: 20%", m[1]: "Probability: 60%", m[2]: "Probability: 40%"})  # checker: empty -> error
    rec = asyncio.run(forecast(Q, FixedClock(AS_OF), _bundle(), DEFAULT_CONFIG, llm))
    assert rec.n_ok == 3 and rec.aggregate == pytest.approx(0.4)
    assert len([f for f in rec.flags if "polarity check failed" in f]) == 3


def test_polarity_check_only_for_binary():
    m = _models()
    q = QuestionSnapshot(question_id=1, post_id=2, question_type="multiple_choice", question_text="Which?", options=("A", "B"))
    llm = PolarityLlm({x: "A: 60%\nB: 40%" for x in m})
    rec = asyncio.run(forecast(q, FixedClock(AS_OF), _bundle(), DEFAULT_CONFIG, llm))
    assert all(f.check is None for f in rec.forecasters)
