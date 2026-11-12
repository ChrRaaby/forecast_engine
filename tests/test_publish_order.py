"""B-86: the live forecast is published (or its failure is written) before any shadow forecast runs, and shadows still run on
every failure path, on the same snapshot and research as the live forecast."""
import asyncio
import json

import pytest
from forecasting_tools import BinaryQuestion, BinaryReport

import main
from forecast_engine.clock import FixedClock
from forecast_engine.config import DEFAULT_CONFIG, TOURNAMENT_CONFIG
from forecast_engine.records import RecordWriter
from forecast_engine.schema import ResearchBundle
from tests.test_core import AS_OF, Q, FakeLlm, _bundle

LIVE, SHADOW = TOURNAMENT_CONFIG.config_hash(), DEFAULT_CONFIG.config_hash()


def _question():
    return BinaryQuestion(question_text=Q.question_text, id_of_question=Q.question_id, id_of_post=Q.post_id,
                          page_url="https://example.org/questions/2")


def _setup(tmp_path, monkeypatch, *, research=None, live_fails=False, forecastable=True, publish_fails=False):
    """An EngineBot with --publish on, whose forecasts, publish call and research are faked offline; returns (bot, events)."""
    monkeypatch.chdir(tmp_path)  # shadow writers create data/forecasts/<run_id>-shadow-<name>/ here
    events: list[tuple[str, object]] = []
    real_forecast = main.forecast

    async def fake_research(snapshot, clock, cfg):
        events.append(("research", snapshot))
        return research if research is not None else _bundle()

    async def logged_forecast(snapshot, clock, bundle, cfg, llm):
        role = "live" if cfg.config_hash() == LIVE else "shadow"
        events.append((role, (snapshot, bundle)))
        if role == "live" and live_fails:
            llm = FakeLlm({})  # every call fails -> a real ForecastFailed
        return await real_forecast(snapshot, clock, bundle, cfg, llm)

    async def fake_publish(self, metaculus_client=None):
        events.append(("publish", None))
        if publish_fails:
            raise ConnectionError("Metaculus is down")

    monkeypatch.setattr(main, "gather_research", fake_research)
    monkeypatch.setattr(main, "forecast", logged_forecast)
    monkeypatch.setattr(main, "question_forecastable", lambda q, clock: (forecastable, "" if forecastable else "closed"))
    monkeypatch.setattr(BinaryReport, "publish_report_to_metaculus", fake_publish)
    llm = FakeLlm({f.model: "Probability: 30%" for f in DEFAULT_CONFIG.forecasters + TOURNAMENT_CONFIG.forecasters})
    llm.outputs[DEFAULT_CONFIG.polarity_check_model] = '{"refers_to": "YES", "why": "ok"}'
    llm.outputs[TOURNAMENT_CONFIG.polarity_check_model] = '{"refers_to": "YES", "why": "ok"}'
    bot = main.EngineBot(
        clock=FixedClock(AS_OF), cfg=TOURNAMENT_CONFIG, llm=llm, writer=RecordWriter("run", root=tmp_path / "live"),
        publish=True, max_run_cost_usd=10, shadows={"cheap": DEFAULT_CONFIG}, run_id="run",
    )
    return bot, events


def _status(path):
    return json.loads(path.read_text(encoding="utf-8"))["status"]


def _live(tmp_path):
    return tmp_path / "live" / "run" / f"q{Q.question_id}.json"


def _shadow(tmp_path):
    return tmp_path / "data" / "forecasts" / "run-shadow-cheap" / f"q{Q.question_id}.json"


def _same_inputs(events):
    """The shadow forecast used the very snapshot and research object the live forecast used."""
    (live_snap, live_bundle), = [e[1] for e in events if e[0] == "live"]
    (shadow_snap, shadow_bundle), = [e[1] for e in events if e[0] == "shadow"]
    return shadow_snap is live_snap and shadow_bundle is live_bundle


def test_publish_happens_before_any_shadow_forecast(tmp_path, monkeypatch):
    bot, events = _setup(tmp_path, monkeypatch)
    asyncio.run(bot._run_individual_question(_question()))
    assert [e[0] for e in events] == ["research", "live", "publish", "shadow"]
    assert _same_inputs(events)
    assert _status(_live(tmp_path)) == "ok" and _status(_shadow(tmp_path)) == "shadow"


def test_live_record_is_written_before_shadows_run(tmp_path, monkeypatch):
    bot, events = _setup(tmp_path, monkeypatch)
    real_run_shadows = bot._run_shadows

    async def check_then_run(snapshot, research, cfg):
        assert _status(_live(tmp_path)) == "ok"  # the live record (published=True) exists before the first shadow starts
        await real_run_shadows(snapshot, research, cfg)

    bot._run_shadows = check_then_run
    asyncio.run(bot._run_individual_question(_question()))
    assert _shadow(tmp_path).exists()


@pytest.mark.parametrize("case, kw, error, live_status", [
    ("live forecast fails", {"live_fails": True}, main.ForecastFailed, "failed"),
    ("refused: no research", {"research": ResearchBundle(as_of=AS_OF, providers=[], items=[], calls=[])}, RuntimeError,
     "refused_no_research"),
    ("question closed meanwhile", {"forecastable": False}, RuntimeError, "failed"),
    ("publishing fails", {"publish_fails": True}, ConnectionError, "failed"),
])
def test_shadows_still_run_after_every_live_failure(tmp_path, monkeypatch, case, kw, error, live_status):
    bot, events = _setup(tmp_path, monkeypatch, **kw)
    with pytest.raises(error):
        asyncio.run(bot._run_individual_question(_question()))
    assert _status(_live(tmp_path)) == live_status, case
    assert events[-1][0] == "shadow", case  # the shadow ran last, after the live outcome
    assert _same_inputs(events), case
    assert _status(_shadow(tmp_path)) == "shadow", case


def test_a_failing_shadow_never_hides_the_published_report(tmp_path, monkeypatch):
    bot, events = _setup(tmp_path, monkeypatch)

    async def broken_followup(*a, **k):
        raise RuntimeError("boom")

    # a shadow with a follow-up step whose step crashes: _run_shadows swallows it, the live report is still returned
    from forecast_engine.config import EXP008_ARMS
    bot.shadows = {"exp008-b-gaps": EXP008_ARMS["exp008-b-gaps"]}
    monkeypatch.setattr(main.research_followup, "followup", broken_followup)
    report = asyncio.run(bot._run_individual_question(_question()))
    assert report is not None and [e[0] for e in events] == ["research", "live", "publish"]
    assert _status(_live(tmp_path)) == "ok"
