"""B-17 / EXP-008: research follow-up step, its shadow wiring and replay. Offline; fake critic and fake Gemini only, made-up text."""
import asyncio
import hashlib
import json
from dataclasses import replace

import pytest

import main
from evals.replay import assert_record_intact, check_record, replay_record
from forecast_engine import research_followup as rf
from forecast_engine.clock import FixedClock
from forecast_engine.config import (
    CONFIG_VERSION,
    DEFAULT_CONFIG,
    EXP008_ARMS,
    SHADOW_CONFIGS,
    TOURNAMENT_CONFIG,
    WIDE_CONFIG,
    FollowupConfig,
)
from forecast_engine.core import forecast
from forecast_engine.prompts import forecast_prompt
from forecast_engine.records import RecordWriter
from forecast_engine.schema import LlmCall, ResearchBundle, ResearchItem
from forecast_engine.spend import SeasonSpend
from tests.test_core import AS_OF, Q

BRIEF = "Synthetic briefing: the committee meets next month and the vote looks close."


def _bundle():
    call = LlmCall(purpose="research", provider="gemini", model=DEFAULT_CONFIG.research.gemini_model, params={}, prompt="p",
                   requested_at=AS_OF, output=BRIEF, cost_usd=0.002)
    return ResearchBundle(as_of=AS_OF, providers=["gemini_grounded"], calls=[call],
                          items=[ResearchItem(source="gemini_grounded", text=BRIEF)])


def _cfg(**kw):
    return replace(DEFAULT_CONFIG.research, followup=FollowupConfig(**kw))


class FakeSearch:
    """Stands in for Gemini grounded search: a made-up answer per prompt, two search queries, two made-up sources."""

    def __init__(self, n_queries=2, tokens_out=1000, fail_on=()):
        self.prompts, self.n_queries, self.tokens_out, self.fail_on = [], n_queries, tokens_out, fail_on

    async def __call__(self, model, prompt):
        self.prompts.append(prompt)
        if any(f in prompt for f in self.fail_on):
            raise RuntimeError("Gemini HTTP 500: synthetic failure")
        n = len(self.prompts)
        return {
            "candidates": [{
                "content": {"parts": [{"text": f"Made-up finding number {n}."}]},
                "groundingMetadata": {
                    "webSearchQueries": [f"made-up query {n}.{i}" for i in range(self.n_queries)],
                    "groundingChunks": [{"web": {"title": f"example.org page {n}", "uri": f"https://example.org/{n}"}},
                                        {"web": {"title": f"example.net page {n}", "uri": f"https://example.net/{n}"}}],
                },
            }],
            "usageMetadata": {"promptTokenCount": 300, "candidatesTokenCount": self.tokens_out, "thoughtsTokenCount": 0},
        }


class FakeLlm:
    """Forecasters answer 'Probability: 30%'; the polarity check says YES; the critic answers from a script, one entry per round."""

    def __init__(self, critic_answers=(), cost=0.001):
        self.critic_answers, self.cost, self.calls = list(critic_answers), cost, []

    async def complete(self, *, model, prompt, purpose, max_tokens, reasoning_effort=None):
        self.calls.append((purpose, model, prompt))
        if purpose == "research_followup_critic":
            out = self.critic_answers.pop(0) if self.critic_answers else ""
        elif purpose == "polarity_check":
            out = '{"refers_to": "YES", "why": "ok"}'
        else:
            out = "Probability: 30%"
        return LlmCall(purpose=purpose, provider="openrouter", model=model, params={}, prompt=prompt, requested_at=AS_OF,
                       output=out, tokens_in=100, tokens_out=50, cost_usd=self.cost, error=None if out else "empty completion")

    def purposes(self, purpose):
        return [c for c in self.calls if c[0] == purpose]


def _gaps(*gaps, lead=None):
    obj = {"gaps": list(gaps)}
    if lead:
        obj = {"leading_conclusion": lead, **obj}
    return "Thinking it over.\n" + json.dumps(obj)


def _run(cfg, llm, search, bundle=None):
    return asyncio.run(rf.followup(Q, bundle or _bundle(), FixedClock(AS_OF), cfg, llm, search=search))


# --- off by default ------------------------------------------------------------------------------------------------------

def test_off_by_default_and_live_hashes_unchanged():
    """The follow-up field must not move any existing config's hash, version or record (hashes as on main 532bdfb)."""
    assert (DEFAULT_CONFIG.config_hash(), TOURNAMENT_CONFIG.config_hash(), WIDE_CONFIG.config_hash()) == (
        "c18ebdd3d1ef", "11fb02f5ac67", "9785b336fd21")
    assert CONFIG_VERSION == "frontier-2026-10-08"
    for cfg in (DEFAULT_CONFIG, TOURNAMENT_CONFIG, WIDE_CONFIG, *SHADOW_CONFIGS.values()):
        assert cfg.research.followup is None and "followup" not in cfg.to_dict()["research"]
    assert SHADOW_CONFIGS == {"cheap": DEFAULT_CONFIG}
    assert not set(EXP008_ARMS) & set(SHADOW_CONFIGS)


def test_exp008_arms_are_the_cheap_ensemble_plus_one_follow_up_mode():
    assert {n: c.research.followup.critic_mode for n, c in EXP008_ARMS.items()} == {
        "exp008-b-gaps": "gaps", "exp008-c-against": "against", "exp008-d-control": "control"}
    hashes = {c.config_hash() for c in EXP008_ARMS.values()}
    assert len(hashes) == 3 and DEFAULT_CONFIG.config_hash() not in hashes
    for c in EXP008_ARMS.values():
        assert c.forecasters == DEFAULT_CONFIG.forecasters and c.research.providers == DEFAULT_CONFIG.research.providers
        assert c.research.followup.max_cost_usd_per_q == 0.06


def test_bad_follow_up_settings_are_refused():
    for kw in ({"critic_mode": "devil"}, {"rounds": 0}, {"max_gaps": 0}, {"max_cost_usd_per_q": 0}):
        with pytest.raises(ValueError):
            FollowupConfig(**kw)


def test_no_follow_up_config_returns_an_equal_copy():
    b = _bundle()
    out = _run(DEFAULT_CONFIG.research, FakeLlm(), FakeSearch(), b)
    assert out is not b and out.to_dict() == b.to_dict()


def test_live_question_makes_zero_follow_up_calls(tmp_path, monkeypatch):
    """The whole live path (research, live forecast, the default shadows) never reaches the follow-up step."""
    from forecasting_tools import BinaryQuestion

    async def no_followup(*a, **k):
        raise AssertionError("follow-up called on the live path")

    async def fake_research(snapshot, clock, cfg):
        return _bundle()

    monkeypatch.setattr(rf, "followup", no_followup)
    monkeypatch.setattr(main, "gather_research", fake_research)
    bot = _bot(tmp_path, monkeypatch, None, dict(SHADOW_CONFIGS), FakeLlm())
    question = BinaryQuestion(question_text=Q.question_text, id_of_question=Q.question_id, id_of_post=Q.post_id,
                              page_url="https://example.org/questions/2")
    asyncio.run(bot._run_individual_question(question))
    assert not [c for c in bot.llm.calls if c[0].startswith("research_followup")]
    live = json.loads((tmp_path / "live" / "run" / f"q{Q.question_id}.json").read_text(encoding="utf-8"))
    assert live["research"] == json.loads(json.dumps(_bundle().to_dict()))


# --- the step itself ------------------------------------------------------------------------------------------------------

def test_gaps_mode_one_critic_call_one_search_per_gap_and_a_labelled_section():
    llm, search = FakeLlm([_gaps("Has the committee chair stated a position?", "How often did past votes pass?")]), FakeSearch()
    b = _bundle()
    out = _run(_cfg(critic_mode="gaps", max_cost_usd_per_q=1.0), llm, search, b)
    critic = llm.purposes("research_followup_critic")
    assert len(critic) == 1 and BRIEF in critic[0][2] and "MISSING" in critic[0][2] and critic[0][1] == "openai/gpt-5-mini"
    assert len(search.prompts) == 2 and "committee chair" in search.prompts[0] and "past votes" in search.prompts[1]
    assert "Today is 2026-05-01." in search.prompts[0] and "Today is 2026-05-01." in critic[0][2]
    new = [i for i in out.items if i.source == rf.SOURCE]
    assert [i.title for i in new] == [rf.SECTION_TITLE, "Follow-up search 1: Has the committee chair stated a position?",
                                      "Follow-up search 2: How often did past votes pass?"]
    assert "Made-up finding number 1." in new[1].text and "example.org page 1" in new[1].text
    text = out.as_prompt_text()
    assert text.startswith(b.as_prompt_text()) and "[research_followup] Additional research" in text
    assert out.providers == ["gemini_grounded", rf.SOURCE]
    # every call recorded: prompt, model, tokens, cost, search queries, source titles/URLs
    s = [c for c in out.calls if c.purpose == "research_followup_search"]
    assert len(s) == 2 and s[0].prompt == search.prompts[0] and s[0].model == DEFAULT_CONFIG.research.gemini_model
    assert s[0].tokens_in == 300 and s[0].tokens_out == 1000
    assert s[0].extra["search_queries"] == ["made-up query 1.0", "made-up query 1.1"]
    assert s[0].extra["sources"][0] == {"title": "example.org page 1", "uri": "https://example.org/1"}
    assert s[0].extra["grounding_fee_usd"] == pytest.approx(2 * 0.014)
    assert s[0].cost_usd == pytest.approx((300 * 0.30 + 1000 * 2.50) / 1e6 + 0.028)
    assert s[-1].extra["followup_summary"]["searches"] == 2
    assert [c.purpose for c in out.calls] == ["research", "research_followup_critic"] + ["research_followup_search"] * 2
    # the input bundle is untouched
    assert b.to_dict() == _bundle().to_dict() and out.cost_usd == pytest.approx(b.cost_usd + 0.001 + 2 * s[0].cost_usd)


def test_against_mode_asks_for_evidence_against_the_leading_conclusion():
    llm, search = FakeLlm([_gaps("Reports that the vote may be postponed", lead="The vote passes.")]), FakeSearch()
    out = _run(_cfg(critic_mode="against"), llm, search)
    prompt = llm.purposes("research_followup_critic")[0][2]
    assert "AGAINST" in prompt and '"leading_conclusion"' in prompt
    assert len(search.prompts) == 1 and "postponed" in search.prompts[0]
    assert [i.title for i in out.items if i.source == rf.SOURCE][0] == rf.SECTION_TITLE  # same label in every mode


def test_control_mode_no_critic_same_number_of_generic_searches():
    llm, search = FakeLlm(), FakeSearch(n_queries=0)  # free searches, so the cap doesn't bind
    out = _run(_cfg(critic_mode="control", max_gaps=5), llm, search)
    assert llm.calls == []
    assert len(search.prompts) == 5
    for prompt, query in zip(search.prompts, rf.CONTROL_QUERIES):
        assert query in prompt
    assert len([i for i in out.items if i.source == rf.SOURCE]) == 6  # label + 5 entries


def test_cost_cap_stops_searching_and_records_the_stop():
    llm, search = FakeLlm([_gaps("gap one", "gap two", "gap three", "gap four", "gap five")]), FakeSearch(n_queries=2)
    out = _run(_cfg(max_cost_usd_per_q=0.06), llm, search)  # each search ~$0.031 here, critic $0.001
    s = [c for c in out.calls if c.purpose == "research_followup_search"]
    assert len(s) == 1 and len(search.prompts) == 1
    own = sum(c.cost_usd for c in out.calls[1:])
    assert own <= 0.06
    assert any("stopped: cost cap $0.06" in e for e in out.errors)
    assert s[-1].extra["followup_summary"]["stopped"].startswith("cost cap")
    # a looser cap lets all five run
    out = _run(_cfg(max_cost_usd_per_q=0.50), FakeLlm([_gaps("a1", "a2", "a3", "a4", "a5")]), FakeSearch(n_queries=2))
    assert len([c for c in out.calls if c.purpose == "research_followup_search"]) == 5


def test_max_gaps_trims_the_critic_list():
    out = _run(_cfg(max_gaps=2, max_cost_usd_per_q=1.0), FakeLlm([_gaps("x1", "x2", "x3", "x2")]), FakeSearch())
    assert len([c for c in out.calls if c.purpose == "research_followup_search"]) == 2


def test_rounds_critic_sees_enlarged_brief_and_no_gaps_ends_the_loop():
    llm = FakeLlm([_gaps("first gap"), _gaps("second gap"), _gaps(), _gaps("never asked")])
    search = FakeSearch(n_queries=0)
    out = _run(_cfg(rounds=5, max_cost_usd_per_q=1.0), llm, search)
    critic = llm.purposes("research_followup_critic")
    assert len(critic) == 3  # round 3 returned no gaps: no rounds 4-5
    assert "Made-up finding number 1." in critic[1][2] and "Made-up finding number 2." in critic[2][2]
    assert len(search.prompts) == 2
    summary = [c for c in out.calls if c.extra.get("followup_summary")][0].extra["followup_summary"]
    assert summary["stopped"] == "critic found no further gaps in round 3" and summary["searches"] == 2


def test_critic_returning_no_gaps_adds_nothing():
    b = _bundle()
    out = _run(_cfg(), FakeLlm([_gaps()]), FakeSearch(), b)
    assert out.as_prompt_text() == b.as_prompt_text() and out.errors == []
    assert len(out.calls) == len(b.calls) + 1  # the critic call is still recorded (and paid)


@pytest.mark.parametrize("answer", ["", "No JSON here", '{"gaps": "not a list"}'])
def test_critic_failure_leaves_prompt_unchanged_with_error_recorded(answer):
    b = _bundle()
    search = FakeSearch()
    out = _run(_cfg(), FakeLlm([answer]), search, b)
    assert out.as_prompt_text() == b.as_prompt_text() and out.items == b.items
    assert search.prompts == []
    assert any(e.startswith("research_followup failed") for e in out.errors)
    assert [c.purpose for c in out.calls] == ["research", "research_followup_critic"]


def test_one_failed_search_is_recorded_and_the_others_kept():
    out = _run(_cfg(max_cost_usd_per_q=1.0), FakeLlm([_gaps("good gap", "bad gap", "other gap")]),
               FakeSearch(fail_on=("bad gap",)))
    titles = [i.title for i in out.items if i.source == rf.SOURCE]
    assert titles == [rf.SECTION_TITLE, "Follow-up search 1: good gap", "Follow-up search 3: other gap"]
    assert any("search 2 failed" in e for e in out.errors)
    assert [c.error is not None for c in out.calls if c.purpose == "research_followup_search"] == [False, True, False]


def test_unexpected_error_keeps_the_original_items(monkeypatch):
    def boom(*a, **k):
        raise KeyError("synthetic bug")

    monkeypatch.setattr(rf, "search_prompt", boom)
    b = _bundle()
    out = _run(_cfg(), FakeLlm([_gaps("a gap")]), FakeSearch(), b)
    assert out.items == b.items and out.providers == b.providers and out.as_of == b.as_of
    assert any("KeyError" in e for e in out.errors)


def test_no_gemini_key_skips_without_calling_the_critic(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    llm = FakeLlm([_gaps("a gap")])
    out = asyncio.run(rf.followup(Q, _bundle(), FixedClock(AS_OF), _cfg(), llm))
    assert llm.calls == [] and any("GEMINI_API_KEY not set" in e for e in out.errors)


def test_forecast_on_the_enlarged_bundle_sees_the_section():
    enlarged = _run(_cfg(max_cost_usd_per_q=1.0), FakeLlm([_gaps("a gap")]), FakeSearch())
    llm = FakeLlm()
    rec = asyncio.run(forecast(Q, FixedClock(AS_OF), enlarged, DEFAULT_CONFIG, llm))
    assert all("[research_followup] Follow-up search 1: a gap" in f.call.prompt for f in rec.forecasters)
    assert rec.cost_usd == pytest.approx(enlarged.cost_usd + 6 * 0.001)


# --- shadow wiring --------------------------------------------------------------------------------------------------------

def _bot(tmp_path, monkeypatch, season, shadows, llm):
    monkeypatch.chdir(tmp_path)
    return main.EngineBot(
        clock=FixedClock(AS_OF), cfg=TOURNAMENT_CONFIG, llm=llm, writer=RecordWriter("run", root=tmp_path / "live"),
        publish=False, max_run_cost_usd=10, season=season, fallback_cfg=DEFAULT_CONFIG, shadows=shadows, run_id="run",
    )


def _shadow_record(tmp_path, name):
    path = tmp_path / "data" / "forecasts" / f"run-shadow-{name}" / f"q{Q.question_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_shadow_arm_gets_enlarged_bundle_live_and_cheap_keep_the_original(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    search = FakeSearch()
    monkeypatch.setattr(rf, "_gemini_search", search)
    season = SeasonSpend("fall-2026", 300, tmp_path / "s.json")
    llm = FakeLlm([_gaps("Has the committee chair stated a position?")])
    arm = "exp008-b-gaps"
    bot = _bot(tmp_path, monkeypatch, season, {"cheap": DEFAULT_CONFIG, arm: EXP008_ARMS[arm]}, llm)
    live = _bundle()
    before = live.to_dict()
    asyncio.run(bot._run_shadows(Q, live, TOURNAMENT_CONFIG))

    assert live.to_dict() == before  # the live forecast's research is not touched
    cheap, rec = _shadow_record(tmp_path, "cheap"), _shadow_record(tmp_path, arm)
    assert cheap["research"] == json.loads(json.dumps(before))
    assert rec["status"] == "shadow" and rec["published"] is False and cheap["published"] is False
    assert rec["config_hash"] == EXP008_ARMS[arm].config_hash()
    assert [i["source"] for i in rec["research"]["items"]] == ["gemini_grounded", rf.SOURCE, rf.SOURCE]
    assert all("Additional research" in f["call"]["prompt"] for f in rec["forecasters"])
    assert not any("Additional research" in f["call"]["prompt"] for f in cheap["forecasters"])
    assert len(search.prompts) == 1  # live research not re-run: only the follow-up search hit "Gemini"

    # costs: each shadow's spend minus the live research (paid by live) goes to the run cap; the follow-up is part of it
    followup_cost = sum(c["cost_usd"] for c in rec["research"]["calls"][1:])
    assert followup_cost > 0.02
    expected = (cheap["cost_usd"] - live.cost_usd) + (rec["cost_usd"] - live.cost_usd)
    assert bot.spent_usd == pytest.approx(expected)
    assert rec["cost_usd"] - cheap["cost_usd"] == pytest.approx(followup_cost)
    assert season.spent_usd == 0  # shadow spend never counts against the frontier season budget (spend.py), follow-up included


def test_shadow_arm_follow_up_failure_still_forecasts(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    monkeypatch.setattr(rf, "_gemini_search", FakeSearch())
    arm = "exp008-c-against"
    bot = _bot(tmp_path, monkeypatch, None, {arm: EXP008_ARMS[arm]}, FakeLlm(["not json"]))
    asyncio.run(bot._run_shadows(Q, _bundle(), TOURNAMENT_CONFIG))
    rec = _shadow_record(tmp_path, arm)
    assert rec["status"] == "shadow" and rec["aggregate"] == pytest.approx(0.3)
    assert any("research_followup failed" in e for e in rec["research"]["errors"])
    assert rec["forecasters"][0]["call"]["prompt"] == forecast_prompt(Q, _bundle().as_prompt_text(), "2026-05-01")
    assert bot.spent_usd == pytest.approx(rec["cost_usd"] - _bundle().cost_usd)  # the failed critic call is still charged


def test_follow_up_spend_is_charged_even_if_the_shadow_forecast_crashes(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    monkeypatch.setattr(rf, "_gemini_search", FakeSearch())

    async def crash(*args, **kwargs):
        raise RuntimeError("synthetic crash outside ForecastFailed")

    monkeypatch.setattr(main, "forecast", crash)
    arm = "exp008-b-gaps"
    bot = _bot(tmp_path, monkeypatch, None, {arm: EXP008_ARMS[arm]}, FakeLlm([_gaps("one gap")]))
    asyncio.run(bot._run_shadows(Q, _bundle(), TOURNAMENT_CONFIG))  # must not raise
    assert bot.spent_usd > 0.02  # the critic call and the paid search still count to the run cap


# --- replay ---------------------------------------------------------------------------------------------------------------

def test_existing_records_keep_byte_identical_prompts_and_format():
    """Golden values computed on main before this change: an old-format bundle's text, prompt and serialised keys."""
    b = ResearchBundle(as_of=AS_OF, providers=["asknews_latest", "gemini_grounded"], calls=[],
                       items=[ResearchItem(source="asknews_latest", text="Made-up article.", title="Made-up title"),
                              ResearchItem(source="gemini_grounded", text="Made-up briefing.\n")])
    text = b.as_prompt_text()
    assert text == "[asknews_latest] Made-up title\nMade-up article.\n\n[gemini_grounded]\nMade-up briefing."
    prompt = forecast_prompt(Q, text, "2026-05-01")
    assert hashlib.sha256(prompt.encode()).hexdigest() == GOLDEN_PROMPT_SHA256
    assert list(b.to_dict()) == ["as_of", "providers", "items", "calls", "errors"]
    rec = json.loads(json.dumps(asyncio.run(forecast(Q, FixedClock(AS_OF), b, DEFAULT_CONFIG, FakeLlm())).to_dict()))
    assert check_record(rec)
    assert_record_intact(rec)


GOLDEN_PROMPT_SHA256 = "2c5d2b464cf0a03c0f3621d00cb63f692d0d5fc51c8c0443d6fae3baabf518cc"


def test_follow_up_record_passes_check_record_and_replays(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key")
    monkeypatch.setattr(rf, "_gemini_search", FakeSearch())
    arm = "exp008-b-gaps"
    bot = _bot(tmp_path, monkeypatch, None, {arm: EXP008_ARMS[arm]}, FakeLlm([_gaps("a made-up gap")]))
    asyncio.run(bot._run_shadows(Q, _bundle(), TOURNAMENT_CONFIG))
    rec = _shadow_record(tmp_path, arm)
    assert check_record(rec)
    assert_record_intact(rec)
    llm = FakeLlm()
    replayed = asyncio.run(replay_record(rec, DEFAULT_CONFIG, llm))
    assert replayed.forecasters[0].call.prompt == rec["forecasters"][0]["call"]["prompt"]
    assert not llm.purposes("research_followup_critic")  # replay uses the stored section, it never searches again
    rec["research"]["items"][-1]["text"] = "Edited later."
    assert not check_record(rec)
