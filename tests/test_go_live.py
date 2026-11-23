"""EXP-013 go-live (ADR-0013): the published tournament forecast uses the full research (arm F exactly as tested); every shadow keeps
the base research; paid steps run side by side and once per question; shadows run side by side outside the live slot. Offline."""
import asyncio
import json
import time
from dataclasses import replace

import pytest
from forecasting_tools import BinaryQuestion, BinaryReport

import main
from evals.replay import check_record
from forecast_engine import research_v2 as rv
from forecast_engine.clock import FixedClock
from forecast_engine.config import (
    CONFIG_VERSION,
    DEFAULT_CONFIG,
    EXP013_ARMS,
    MAX_CONCURRENT_SHADOWS,
    SHADOW_CONFIGS,
    TOURNAMENT_BASE_CONFIG,
    TOURNAMENT_CONFIG,
    SourceBlockConfig,
)
from forecast_engine.records import RecordWriter
from forecast_engine.schema import ResearchBundle
from forecast_engine.spend import SeasonSpend
from tests.test_core import AS_OF
from tests.test_research_v2 import LIVE_BRIEF, FakeGet, FakeLlm, Q, _bundle, _fake_asknews, _found, _gemini

BASE_TITLES = ["Live 1", "Live 2", None]


def _question(n=0):
    return BinaryQuestion(question_text=Q.question_text, id_of_question=Q.question_id + n, id_of_post=Q.post_id + n,
                          page_url=f"https://example.org/questions/{8 + n}", resolution_criteria=Q.resolution_criteria,
                          fine_print=Q.fine_print)


def _setup(tmp_path, monkeypatch, *, shadows=None, base=None, season=None, max_concurrent=3, max_cost=10, pages=None):
    """(bot, live llm, shadow llm, log): publishing on, every network call faked, separate clients for live and shadows."""
    monkeypatch.chdir(tmp_path)
    log = {"news": [], "writer": [], "get": FakeGet(pages), "published": []}

    async def fake_research(snapshot, clock, cfg):
        return base if base is not None else _bundle()

    async def fake_publish(self, metaculus_client=None):
        log["published"].append(self.question.id_of_question)

    monkeypatch.setattr(main, "gather_research", fake_research)
    monkeypatch.setattr(main, "question_forecastable", lambda q, clock: (True, ""))
    monkeypatch.setattr(BinaryReport, "publish_report_to_metaculus", fake_publish)
    monkeypatch.setattr(rv, "_http_get", log["get"])
    monkeypatch.setattr(rv, "_asknews_search", _fake_asknews(log["news"]))
    monkeypatch.setattr(rv, "_post_writer", lambda *a: log["writer"].append(a) or _gemini())
    live_llm, llm = FakeLlm(extraction=_found()), FakeLlm(extraction=_found())
    bot = main.EngineBot(clock=FixedClock(AS_OF), cfg=TOURNAMENT_CONFIG, llm=llm, live_llm=live_llm,
                         writer=RecordWriter("run", root=tmp_path / "live"), publish=True, max_run_cost_usd=max_cost, season=season,
                         fallback_cfg=DEFAULT_CONFIG if season else None, max_concurrent=max_concurrent, run_id="run",
                         shadows={"cheap": DEFAULT_CONFIG, **EXP013_ARMS} if shadows is None else shadows)
    return bot, live_llm, llm, log


def _rec(tmp_path, shadow=None, n=0):
    d = tmp_path / "live" / "run" if shadow is None else tmp_path / "data" / "forecasts" / f"run-shadow-{shadow}"
    return json.loads((d / f"q{Q.question_id + n}.json").read_text(encoding="utf-8"))


def _titles(rec):
    return [i["title"] for i in rec["research"]["items"]]


# --- configs ---------------------------------------------------------------------------------------------------------------

def test_live_is_exactly_the_tested_arm_f_and_no_shadow_moved():
    assert CONFIG_VERSION == "frontier-2026-11-24"
    assert TOURNAMENT_CONFIG.config_hash() == "d7d19c6f8464"  # the hash of every exp013-f-frontier-full record since 2026-11-15
    assert TOURNAMENT_BASE_CONFIG.config_hash() == "f3c50c57f8d1"  # the live hash until the go-live
    assert {n: c.config_hash() for n, c in SHADOW_CONFIGS.items()} == {
        "cheap": "714ff99803b1", "cheap-old-prompt": "cd5e67c675c1", "exp008-b-gaps": "8c4994f4f24f",
        "exp008-c-against": "356fe2183b98", "exp008-d-control": "a6f01cf6b06d", "exp013-b-source": "76e6c4634237",
        "exp013-c-writer": "aabd61c581b0", "exp013-d-news": "c2d6fa167d8d", "exp013-frontier-old": "f3c50c57f8d1",
        "challenger-gemini-4-carbon": "3a166d58d59b", "exp016-s1-per-value": "045d4c18569c"}


# --- who reads what, who pays ----------------------------------------------------------------------------------------------

def test_live_reads_the_full_package_and_shadows_read_the_base(tmp_path, monkeypatch):
    bot, live_llm, llm, log = _setup(tmp_path, monkeypatch)
    asyncio.run(bot._run_individual_question(_question()))
    live = _rec(tmp_path)
    assert live["status"] == "ok" and live["published"] and log["published"] == [Q.question_id]
    assert _titles(live) == ["Resolution source, fetched 2026-05-01 12:00 UTC", "kw 1", "kw 2", "hist 1", "hist 2", None]
    assert live["research"]["items"][-1]["text"].startswith("Made-up strict brief") and live["config_hash"] == "d7d19c6f8464"
    for n in ("cheap", "exp013-frontier-old"):
        assert _titles(_rec(tmp_path, n)) == BASE_TITLES, n
        assert _rec(tmp_path, n)["research"]["items"][-1]["text"] == LIVE_BRIEF
    assert _titles(_rec(tmp_path, "exp013-b-source"))[1:] == BASE_TITLES  # one change each, on the base
    assert _titles(_rec(tmp_path, "exp013-d-news")) == ["kw 1", "kw 2", "hist 1", "hist 2", None]
    for rec in (live, *(_rec(tmp_path, n) for n in bot.shadows)):
        assert check_record(rec)  # the stored prompt rebuilds from the stored research


def test_every_paid_step_runs_once_and_on_the_live_key(tmp_path, monkeypatch):
    bot, live_llm, llm, log = _setup(tmp_path, monkeypatch)
    asyncio.run(bot._run_individual_question(_question()))
    assert len(log["news"]) == 2 and len(log["writer"]) == 1 and len(log["get"].urls) == 2  # AskNews 1 + 5 credits, not twice
    assert len(live_llm.purposes("exp013_source_extract")) == 1 and len(live_llm.purposes("exp013_news_keywords")) == 1
    assert not llm.purposes("exp013_source_extract") and not llm.purposes("exp013_news_keywords")
    writer = (1000 * 1.50 + 1500 * 7.50) / 1e6 + 2 * 0.014
    # base research $0.002; live 5 + 5, frontier-old 5 + 5, cheap/B/C/D 3 + 3 each; extraction, keywords and writer once
    assert bot.spent_usd == pytest.approx(0.002 + 0.001 * (10 + 10 + 4 * 6 + 2) + writer)


def test_after_the_season_cap_the_cheap_fallback_reads_the_base(tmp_path, monkeypatch):
    season = SeasonSpend("fall-2026", 1.0, tmp_path / "s.json")
    season.add(2.0)
    bot, live_llm, llm, log = _setup(tmp_path, monkeypatch, season=season)
    asyncio.run(bot._run_individual_question(_question()))
    live = _rec(tmp_path)
    assert live["config_hash"] == DEFAULT_CONFIG.config_hash() and _titles(live) == BASE_TITLES and live["published"]
    assert not live_llm.purposes("exp013_source_extract")  # the arms then pay their steps themselves, on their own key
    assert len(llm.purposes("exp013_source_extract")) == 1 and len(log["writer"]) == 1
    assert not (tmp_path / "data" / "forecasts" / "run-shadow-cheap").exists()  # live already is the cheap config


# --- failures never cost the live forecast ---------------------------------------------------------------------------------

def test_a_failing_step_leaves_the_base_text_and_the_forecast_is_published(tmp_path, monkeypatch):
    bot, _, _, log = _setup(tmp_path, monkeypatch)

    def boom(*a):
        raise RuntimeError("Gemini HTTP 500: synthetic")

    monkeypatch.setattr(rv, "_post_writer", boom)
    asyncio.run(bot._run_individual_question(_question()))
    live = _rec(tmp_path)
    assert live["published"] and live["research"]["items"][-1]["text"] == LIVE_BRIEF and _titles(live)[1] == "kw 1"
    assert any("exp013 brief_writer failed" in e for e in live["research"]["errors"])


def test_a_hanging_step_is_dropped_after_its_time_limit(tmp_path, monkeypatch):
    bot, _, _, log = _setup(tmp_path, monkeypatch)

    async def hang(*a, **k):
        await asyncio.sleep(60)

    monkeypatch.setattr(rv, "_asknews_search", hang)
    monkeypatch.setitem(rv.STEP_TIMEOUT_S, "news", 0.05)
    t = time.monotonic()
    asyncio.run(bot._run_individual_question(_question()))
    live = _rec(tmp_path)
    assert time.monotonic() - t < 20 and live["published"] and _titles(live)[1:3] == ["Live 1", "Live 2"]
    assert any("exp013 news failed: TimeoutError" in e for e in live["research"]["errors"])
    assert _titles(_rec(tmp_path, "exp013-d-news")) == BASE_TITLES  # the shared failure hits arm D alike


def test_even_a_crash_of_the_whole_step_code_falls_back_to_the_base(tmp_path, monkeypatch):
    bot, *_ = _setup(tmp_path, monkeypatch)

    async def crash(*a, **k):
        raise KeyError("synthetic bug")

    monkeypatch.setattr(main.research_v2, "apply", crash)
    asyncio.run(bot._run_individual_question(_question()))
    assert _rec(tmp_path)["published"] and _titles(_rec(tmp_path)) == BASE_TITLES


@pytest.mark.parametrize("writer_works", [False, True])
def test_a_not_found_block_alone_is_not_research(tmp_path, monkeypatch, writer_works):
    """Base research empty and the source not retrievable: the block's NOT FOUND line must not count as research."""
    empty = ResearchBundle(as_of=AS_OF, providers=[], items=[], calls=[])
    bot, _, _, log = _setup(tmp_path, monkeypatch, base=empty, pages={}, shadows={})
    monkeypatch.setattr(rv, "_asknews_search", _fake_asknews([], fail=("latest news", "news knowledge")))
    if not writer_works:
        def boom(*a):
            raise RuntimeError("synthetic")
        monkeypatch.setattr(rv, "_post_writer", boom)
        with pytest.raises(RuntimeError, match="refusing to publish without research"):
            asyncio.run(bot._run_individual_question(_question()))
        assert _rec(tmp_path)["status"] == "refused_no_research" and not log["published"]
    else:
        asyncio.run(bot._run_individual_question(_question()))
        assert log["published"]  # the writer's brief is research


# --- side by side ----------------------------------------------------------------------------------------------------------

def test_steps_run_side_by_side(monkeypatch):
    """The three paid steps start together: each waits until all three have started, so one after another would time out."""
    started, go = [], []

    async def meet(name):
        started.append(name)
        for _ in range(400):
            if len(started) == 3:
                return
            await asyncio.sleep(0.01)
        raise AssertionError(f"steps did not overlap: {started}")

    async def news(q, clock, cfg, llm):
        await meet("news")
        return [], [], [], [], None

    async def brief(q, clock, cfg):
        await meet("writer")
        return [], [], None, None

    async def block(q, clock, cfg, llm):
        await meet("source")
        return [], None, []

    monkeypatch.setattr(rv, "_build_news", news)
    monkeypatch.setattr(rv, "_build_brief", brief)
    monkeypatch.setattr(rv, "_build_source_block", block)
    out = asyncio.run(rv.apply(Q, _bundle(), FixedClock(AS_OF), TOURNAMENT_CONFIG.research, FakeLlm()))
    assert not out.errors and sorted(started) == ["news", "source", "writer"]


def test_configs_running_at_the_same_time_share_one_step(monkeypatch):
    get = FakeGet()
    monkeypatch.setattr(rv, "_http_get", get)
    llm, cache = FakeLlm(extraction=_found()), {}
    cfg = replace(DEFAULT_CONFIG.research, source_block=SourceBlockConfig())

    async def both():
        return await asyncio.gather(*[rv.apply(Q, _bundle(), FixedClock(AS_OF), cfg, llm, cache) for _ in range(4)])

    outs = asyncio.run(both())
    assert len(get.urls) == 2 and len(llm.purposes("exp013_source_extract")) == 1
    assert all(o.items[0] is outs[0].items[0] for o in outs)


def test_shadows_run_side_by_side_within_the_limit(tmp_path, monkeypatch):
    shadows = {f"s{n}": replace(DEFAULT_CONFIG, binary_clip=(0.01 + n / 1000, 0.99)) for n in range(10)}
    bot, _, _, _ = _setup(tmp_path, monkeypatch, shadows=shadows)
    now, peak = [0], [0]
    real = main.forecast

    async def slow(snapshot, clock, research, cfg, llm):
        if cfg is TOURNAMENT_CONFIG:
            return await real(snapshot, clock, research, cfg, llm)
        now[0] += 1
        peak[0] = max(peak[0], now[0])
        await asyncio.sleep(0.03)
        now[0] -= 1
        return await real(snapshot, clock, research, cfg, llm)

    monkeypatch.setattr(main, "forecast", slow)

    async def two():
        await asyncio.gather(*[bot._run_individual_question(_question(n)) for n in (0, 1)])

    asyncio.run(two())
    assert peak[0] == MAX_CONCURRENT_SHADOWS == 6  # over both questions together
    assert all(_rec(tmp_path, n, k)["status"] == "shadow" for n in shadows for k in (0, 1))


def test_shadows_do_not_hold_up_the_next_live_question(tmp_path, monkeypatch):
    """One live slot, two questions: the second is published while the first one's shadow is still running."""
    bot, _, _, log = _setup(tmp_path, monkeypatch, shadows={"cheap": DEFAULT_CONFIG}, max_concurrent=1)
    real = main.forecast

    async def wait_for_both_posts(snapshot, clock, research, cfg, llm):
        if cfg is DEFAULT_CONFIG:
            for _ in range(500):
                if len(log["published"]) == 2:
                    break
                await asyncio.sleep(0.01)
            assert len(log["published"]) == 2, "a shadow held up the next live question"
        return await real(snapshot, clock, research, cfg, llm)

    monkeypatch.setattr(main, "forecast", wait_for_both_posts)

    async def two():
        await asyncio.gather(*[bot._run_individual_question(_question(n)) for n in (0, 1)])

    asyncio.run(two())
    assert all(_rec(tmp_path, "cheap", n)["status"] == "shadow" for n in (0, 1))


def test_one_crashing_shadow_does_not_touch_the_others(tmp_path, monkeypatch):
    bot, *_ = _setup(tmp_path, monkeypatch, shadows={"cheap": DEFAULT_CONFIG, "exp013-frontier-old": TOURNAMENT_BASE_CONFIG})
    real = main.forecast

    async def crash_one(snapshot, clock, research, cfg, llm):
        if cfg is DEFAULT_CONFIG:
            raise ValueError("synthetic")
        return await real(snapshot, clock, research, cfg, llm)

    monkeypatch.setattr(main, "forecast", crash_one)
    report = asyncio.run(bot._run_individual_question(_question()))
    assert report is not None and _rec(tmp_path, "exp013-frontier-old")["status"] == "shadow"


def test_shadows_stop_at_the_run_cost_cap(tmp_path, monkeypatch, caplog):
    bot, _, llm, log = _setup(tmp_path, monkeypatch, max_cost=0.02)  # the live forecast alone passes it
    asyncio.run(bot._run_individual_question(_question()))
    assert log["published"] and not llm.calls and "run cost cap reached" in caplog.text
