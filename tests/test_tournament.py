"""B-16 / B-62: season spend cap with cheap fallback, and shadow configs on tournament questions."""
import asyncio

import pytest

import main
from forecast_engine.clock import FixedClock
from forecast_engine.config import DEFAULT_CONFIG, TOURNAMENT_CONFIG
from forecast_engine.records import RecordWriter
from forecast_engine.spend import SeasonSpend
from tests.test_core import AS_OF, Q, FakeLlm, _bundle


def test_season_spend_persists_and_caps(tmp_path):
    path = tmp_path / "spend.json"
    s = SeasonSpend("fall-2026", 1.0, path)
    s.add(0.6)
    s.save()
    s2 = SeasonSpend("fall-2026", 1.0, path)
    assert s2.spent_usd == pytest.approx(0.6) and not s2.exhausted
    s2.add(0.5)
    assert s2.exhausted
    assert SeasonSpend("spring-2027", 1.0, path).spent_usd == 0  # a new season starts at $0
    bad = tmp_path / "bad.json"
    bad.write_text("{oops")
    assert SeasonSpend("fall-2026", 1.0, bad).spent_usd == 0


def _bot(tmp_path, monkeypatch, season):
    monkeypatch.chdir(tmp_path)  # shadow writers create data/forecasts/<run_id>-shadow-<name>/ here
    llm = FakeLlm({f.model: "Probability: 30%" for f in DEFAULT_CONFIG.forecasters + TOURNAMENT_CONFIG.forecasters})
    llm.outputs[DEFAULT_CONFIG.polarity_check_model] = '{"refers_to": "YES", "why": "ok"}'
    return main.EngineBot(
        clock=FixedClock(AS_OF), cfg=TOURNAMENT_CONFIG, llm=llm, writer=RecordWriter("run", root=tmp_path / "live"),
        publish=False, max_run_cost_usd=10, season=season, fallback_cfg=DEFAULT_CONFIG,
        shadows={"cheap": DEFAULT_CONFIG}, run_id="run",
    )


def test_frontier_until_cap_then_cheap_fallback(tmp_path, monkeypatch):
    season = SeasonSpend("fall-2026", 0.01, tmp_path / "s.json")
    bot = _bot(tmp_path, monkeypatch, season)
    assert bot._live_cfg() is TOURNAMENT_CONFIG
    bot._charge(TOURNAMENT_CONFIG, 0.02)
    assert season.exhausted and bot._live_cfg() is DEFAULT_CONFIG
    bot._charge(DEFAULT_CONFIG, 0.5)  # cheap forecasts don't count against the frontier budget
    assert season.spent_usd == pytest.approx(0.02) and bot.spent_usd == pytest.approx(0.52)


def test_shadow_writes_unpublished_record_and_skips_when_live_is_the_same_config(tmp_path, monkeypatch):
    bot = _bot(tmp_path, monkeypatch, SeasonSpend("fall-2026", 300, tmp_path / "s.json"))
    asyncio.run(bot._run_shadows(Q, _bundle(), TOURNAMENT_CONFIG))
    shadow = tmp_path / "data" / "forecasts" / "run-shadow-cheap" / f"q{Q.question_id}.json"
    assert shadow.exists() and '"status": "shadow"' in shadow.read_text(encoding="utf-8")
    shadow.unlink()
    asyncio.run(bot._run_shadows(Q, _bundle(), DEFAULT_CONFIG))  # after the fallback, live is already the cheap config
    assert not shadow.exists()


def test_shadow_failure_never_raises(tmp_path, monkeypatch):
    bot = _bot(tmp_path, monkeypatch, None)
    bot.llm.outputs = {}  # every call fails -> ForecastFailed inside the shadow
    asyncio.run(bot._run_shadows(Q, _bundle(), TOURNAMENT_CONFIG))
    rec = tmp_path / "data" / "forecasts" / "run-shadow-cheap" / f"q{Q.question_id}.json"
    assert '"status": "shadow_failed"' in rec.read_text(encoding="utf-8")
