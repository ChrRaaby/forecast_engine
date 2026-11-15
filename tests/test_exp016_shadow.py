"""F-24 (Christian's OK 2026-11-15): EXP-016's per-value arm as an unpublished shadow, only on discrete questions with <= 25
values. Offline; synthetic only."""
import asyncio
import json
from dataclasses import replace

import main
from evals import exp010
from evals import prompt_archive as pa
from forecast_engine import prompt_versions
from forecast_engine.clock import FixedClock
from forecast_engine.config import DEFAULT_CONFIG, EXP016_SHADOWS, SHADOW_CONFIGS, TOURNAMENT_CONFIG
from forecast_engine.records import RecordWriter
from forecast_engine.schema import LlmCall
from tests.test_core import AS_OF, Q, _bundle

NAME = "exp016-s1-per-value"
SMALL = replace(Q, question_id=880, question_type="discrete", lower_bound=21.5, upper_bound=26.5, nominal_lower_bound=22.0,
                nominal_upper_bound=26.0, open_upper_bound=True)
BIG = replace(SMALL, question_id=881, lower_bound=-0.5, upper_bound=25.5, nominal_lower_bound=0.0, nominal_upper_bound=25.0)


class PromptLlm:
    def __init__(self):
        self.prompts = []

    async def complete(self, *, model, prompt, purpose, max_tokens, reasoning_effort=None):
        if purpose == "forecast":
            self.prompts.append(prompt)
        if "The outcome will be one of these values" in prompt:
            out = "24: 60%\n25: 40%"
        elif "Percentile 10" in prompt:
            out = "\n".join(f"Percentile {p}: {v}" for p, v in zip((10, 20, 40, 60, 80, 90), (22, 23, 23, 24, 25, 26)))
        elif purpose == "polarity_check":
            out = '{"refers_to": "YES", "why": "ok"}'
        else:
            out = "Probability: 30%"
        return LlmCall(purpose=purpose, provider="fake", model=model, params={}, prompt=prompt, requested_at=AS_OF, output=out,
                       cost_usd=0.001)


def _bot(tmp_path, monkeypatch, llm):
    monkeypatch.chdir(tmp_path)
    return main.EngineBot(clock=FixedClock(AS_OF), cfg=TOURNAMENT_CONFIG, llm=llm, writer=RecordWriter("run", root=tmp_path / "live"),
                          publish=False, max_run_cost_usd=10, season=None, fallback_cfg=DEFAULT_CONFIG,
                          shadows={"cheap": DEFAULT_CONFIG, **EXP016_SHADOWS}, run_id="run")


def _shadow(tmp_path, name, q):
    return tmp_path / "data" / "forecasts" / f"run-shadow-{name}" / f"q{q.question_id}.json"


def test_the_shadow_is_switched_on_and_uses_the_variant():
    assert SHADOW_CONFIGS[NAME].prompt_version == "per-value-discrete-2026-11-15"
    assert SHADOW_CONFIGS[NAME].forecasters == DEFAULT_CONFIG.forecasters  # the cheap roster: paired with the "cheap" shadow
    assert pa.drift_problems() == []  # a variant in a shadow arm is allowed; published configs still use the live prompt


def test_runs_on_a_small_discrete_question_and_stores_a_per_value_record(tmp_path, monkeypatch):
    llm = PromptLlm()
    asyncio.run(_bot(tmp_path, monkeypatch, llm)._run_shadows(SMALL, _bundle(), TOURNAMENT_CONFIG))
    rec = json.loads(_shadow(tmp_path, NAME, SMALL).read_text(encoding="utf-8"))
    assert rec["status"] == "shadow" and rec["published"] is False
    assert rec["aggregate"]["kind"] == "per_value" and rec["aggregate"]["labels"][-1] == "above"
    assert json.loads(_shadow(tmp_path, "cheap", SMALL).read_text(encoding="utf-8"))["aggregate"][0][0] == 0.1  # cheap unchanged


def test_skipped_where_the_per_value_format_does_not_apply(tmp_path, monkeypatch):
    for q in (Q, BIG):
        llm = PromptLlm()
        asyncio.run(_bot(tmp_path, monkeypatch, llm)._run_shadows(q, _bundle(), TOURNAMENT_CONFIG))
        assert not _shadow(tmp_path, NAME, q).exists() and _shadow(tmp_path, "cheap", q).exists()
        assert len(llm.prompts) == len(DEFAULT_CONFIG.forecasters)  # forecast calls: the cheap shadow only
    assert prompt_versions.skips_question("per-value-discrete-2026-11-15", BIG)
    assert not prompt_versions.skips_question(prompt_versions.LIVE, BIG)
    assert not prompt_versions.skips_question("range-calibrated-2026-11-14", Q)


def test_exp010_loader_drops_per_value_records_instead_of_crashing(tmp_path, monkeypatch):
    asyncio.run(_bot(tmp_path, monkeypatch, PromptLlm())._run_shadows(SMALL, _bundle(), TOURNAMENT_CONFIG))
    out = tmp_path / "o.jsonl"
    out.write_text(json.dumps({"question_id": SMALL.question_id, "status": "resolved", "resolution": "24",
                               "actual_resolve_time": "2026-10-20T00:00:00Z"}) + "\n", encoding="utf-8")
    loaded = exp010.load_cases(tmp_path / "data", out, tmp_path / "none.jsonl", need_scaling=False)
    assert loaded.dropped[exp010.DROP_PER_VALUE] == 1
