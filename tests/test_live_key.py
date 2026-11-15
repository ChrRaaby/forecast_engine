"""B-85 (option 2): published forecasts on their own OpenRouter key; the season total is read from that key's usage at start-up."""
import asyncio
import json
import logging
from types import SimpleNamespace

import pytest
from forecasting_tools import BinaryQuestion

import main
from forecast_engine import spend
from forecast_engine.clock import FixedClock
from forecast_engine.config import (
    DEFAULT_CONFIG,
    TOURNAMENT_CONFIG,
    TOURNAMENT_SEASON_CAP_USD,
    TOURNAMENT_SEASON_SPEND_BEFORE_LIVE_KEY_USD,
)
from forecast_engine.records import RecordWriter
from forecast_engine.spend import SeasonSpend
from tests.test_core import AS_OF, Q, FakeLlm, _bundle

BEFORE = TOURNAMENT_SEASON_SPEND_BEFORE_LIVE_KEY_USD


def _cache(path, usd):
    path.write_text(json.dumps({"season": "fall-2026", "cap_usd": 300.0, "spent_usd": usd}), encoding="utf-8")


def test_total_is_spend_before_the_key_plus_its_usage(tmp_path):
    path = tmp_path / "s.json"
    _cache(path, 12.24)  # the stale cache value is ignored when OpenRouter answers
    s = SeasonSpend.from_live_key("fall-2026", 300.0, "k", BEFORE, path, usage=lambda key: 10.0)
    assert s.spent_usd == pytest.approx(BEFORE + 10.0) and s.source == "OpenRouter live key" and not s.exhausted
    s.add(1.0)
    s.save()
    assert json.loads(path.read_text(encoding="utf-8"))["spent_usd"] == pytest.approx(BEFORE + 11.0)


def test_cap_reached_leaves_key_room_for_the_cheap_fallback():
    """Key limit $300 (Christian): the season cap is reached at $300 - BEFORE of key usage, ~$31 before the key's own limit."""
    at_cap = SeasonSpend.from_live_key("fall-2026", 300.0, "k", BEFORE, "nowhere.json", usage=lambda key: 300.0 - BEFORE)
    assert at_cap.exhausted and 300.0 - (300.0 - BEFORE) == pytest.approx(BEFORE) and BEFORE > 25
    assert TOURNAMENT_SEASON_CAP_USD == 300.0


def test_openrouter_unreachable_uses_the_cache_then_the_safe_side(tmp_path, caplog):
    def down(key):
        raise ConnectionError("openrouter.ai unreachable")

    path = tmp_path / "s.json"
    _cache(path, 50.0)
    s = SeasonSpend.from_live_key("fall-2026", 300.0, "k", BEFORE, path, usage=down)
    assert s.spent_usd == 50.0 and s.source.startswith("cache file") and not s.exhausted
    missing = tmp_path / "none.json"
    with caplog.at_level(logging.WARNING):
        s = SeasonSpend.from_live_key("fall-2026", 300.0, "k", BEFORE, missing, usage=down)
    assert s.exhausted and s.source.startswith("unknown") and "cheap config" in caplog.text
    s.save()
    assert not missing.exists()  # an unknown total is never written over the cache


def test_key_usage_reads_openrouter_response(monkeypatch):
    seen = {}

    def fake_get(url, headers, timeout):
        seen.update(url=url, auth=headers["Authorization"])
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"data": {"usage": 12.5, "limit": 300}})

    monkeypatch.setattr(spend.requests, "get", fake_get)
    assert spend.openrouter_key_usage("sk-test") == 12.5
    assert seen == {"url": "https://openrouter.ai/api/v1/key", "auth": "Bearer sk-test"}


def _bot(tmp_path, monkeypatch, live_llm):
    monkeypatch.chdir(tmp_path)

    async def fake_research(snapshot, clock, cfg):
        return _bundle()

    monkeypatch.setattr(main, "gather_research", fake_research)
    llm = FakeLlm({f.model: "Probability: 30%" for f in DEFAULT_CONFIG.forecasters})
    llm.outputs[DEFAULT_CONFIG.polarity_check_model] = '{"refers_to": "YES", "why": "ok"}'
    return main.EngineBot(clock=FixedClock(AS_OF), cfg=TOURNAMENT_CONFIG, llm=llm, writer=RecordWriter("run", root=tmp_path / "l"),
                          publish=False, max_run_cost_usd=10, shadows={"cheap": DEFAULT_CONFIG}, run_id="run",
                          live_llm=live_llm)


def _question():
    return BinaryQuestion(question_text=Q.question_text, id_of_question=Q.question_id, id_of_post=Q.post_id,
                          page_url="https://example.org/questions/2")


def test_only_the_published_forecast_uses_the_live_key(tmp_path, monkeypatch):
    live = FakeLlm({f.model: "Probability: 40%" for f in TOURNAMENT_CONFIG.forecasters})
    live.outputs[TOURNAMENT_CONFIG.polarity_check_model] = '{"refers_to": "YES", "why": "ok"}'
    bot = _bot(tmp_path, monkeypatch, live)
    asyncio.run(bot._run_individual_question(_question()))
    assert len(live.prompts) == 2 * len(TOURNAMENT_CONFIG.forecasters)  # frontier forecasts + their polarity checks
    assert len(bot.llm.prompts) == 2 * len(DEFAULT_CONFIG.forecasters)  # the cheap shadow, on the other key
    assert (tmp_path / "data" / "forecasts" / "run-shadow-cheap" / f"q{Q.question_id}.json").exists()


def test_without_a_live_key_everything_uses_one_client(tmp_path, monkeypatch):
    bot = _bot(tmp_path, monkeypatch, None)
    assert bot.live_llm is bot.llm


def test_key_limit_refusal_is_reported_and_the_run_goes_on(tmp_path, monkeypatch, caplog):
    class Refusing(FakeLlm):
        async def complete(self, **kw):
            call = await super().complete(**kw)
            call.output, call.error = "", "Error code: 403 - Key limit exceeded"
            return call

    bot = _bot(tmp_path, monkeypatch, Refusing({}))
    with caplog.at_level(logging.ERROR), pytest.raises(main.ForecastFailed):
        asyncio.run(bot._run_individual_question(_question()))
    assert "credit limit reached" in caplog.text
    live = json.loads((tmp_path / "l" / "run" / f"q{Q.question_id}.json").read_text(encoding="utf-8"))
    assert live["status"] == "failed"
    assert (tmp_path / "data" / "forecasts" / "run-shadow-cheap" / f"q{Q.question_id}.json").exists()  # shadows still run


def test_key_limit_detection():
    rec = SimpleNamespace(forecasters=[SimpleNamespace(call=SimpleNamespace(error=e)) for e in
                                       ("Error code: 403 - Key limit exceeded", "402 Payment Required: insufficient credits")])
    assert main._key_limit_hit(rec)
    rec.forecasters[1].call.error = "timeout"
    assert not main._key_limit_hit(rec)
