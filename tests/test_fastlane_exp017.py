"""EXP-017 prerequisites: arm table, experiment caps and stops, picker across days, ledger, host pin, the locked look. Offline."""
import asyncio
import json
import random
from datetime import date, datetime, timedelta, timezone

import pytest

from fastlane import analyze, arms, experiments, picker, run, series
from fastlane.guard import AskNewsGuard, GuardStop, SpendGuard
from forecast_engine import provenance
from forecast_engine.clock import FixedClock
from forecast_engine.config import CHALLENGER_SHADOWS, EXP008_ARMS, EXP013_ARMS, EXP016_SHADOWS, SHADOW_CONFIGS
from forecast_engine.schema import LlmCall

NOW = datetime(2026, 11, 26, 21, 0, tzinfo=timezone.utc)
EXP = experiments.EXPERIMENTS["EXP-017"]


# ---- arm table ----

def test_lane_only_arms_are_in_no_tournament_list():
    tournament_lists = [SHADOW_CONFIGS, EXP008_ARMS, EXP013_ARMS, CHALLENGER_SHADOWS, EXP016_SHADOWS]
    for name in arms.LANE_ONLY:
        assert all(name not in d for d in tournament_lists)
    hashes = {c.config_hash() for d in tournament_lists for c in d.values()}
    for name in ("effort-high", "writer-effort-high"):
        assert arms.ARMS[name].cfg.config_hash() not in hashes


def test_shadow_arms_are_the_tournament_shadows_unchanged():
    for name in ("cheap", "cheap-old-prompt", "exp013-c-writer"):
        assert arms.ARMS[name].cfg.config_hash() == SHADOW_CONFIGS[name].config_hash()
    assert arms.ARMS["cheap-aa"].cfg == SHADOW_CONFIGS["cheap"] and arms.ARMS["cheap-aa"].research == "shared"
    assert arms.ARMS["no-research"].cfg == SHADOW_CONFIGS["cheap"] and arms.ARMS["no-research"].research == "none"


def test_high_effort_changes_only_the_forecasters():
    base, high = SHADOW_CONFIGS["exp013-c-writer"], arms.ARMS["writer-effort-high"].cfg
    assert all(f.reasoning_effort == "high" and f.max_tokens == 32000 for f in high.forecasters)
    assert [f.model for f in high.forecasters] == [f.model for f in base.forecasters]
    assert high.research == base.research and high.polarity_check_model == base.polarity_check_model
    assert high.prompt_version == base.prompt_version


def test_exp017_on_card():
    assert EXP.arms == ("cheap", "cheap-old-prompt", "exp013-c-writer", "cheap-aa", "no-research")
    assert EXP.level == pytest.approx(0.975) and EXP.cap_usd == 45.0 and EXP.max_picked == 320 and EXP.look_n == 280
    assert set(EXP.arms) <= set(arms.ARMS) and run.DEFAULT_ARMS == EXP.arms
    assert experiments.PINNED_HOSTS == {"deepseek/deepseek-v4.1-flash": "wafer"}


def test_fingerprint_files_untouched_by_the_lane():
    root = provenance.ROOT
    assert not any(p.relative_to(root).parts[0] == "fastlane" for p in provenance.code_paths())


# ---- runner with the arm table ----

def _market(i, end_days=5, price=0.4):
    end = (NOW + timedelta(days=end_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": str(i), "question": f"Will thing {i} happen?", "outcomes": '["Yes", "No"]',
            "outcomePrices": json.dumps([str(price), str(1 - price)]), "clobTokenIds": '["t1", "t2"]',
            "createdAt": "2026-11-01T00:00:00Z", "endDate": end, "liquidityNum": 20000, "closed": False, "active": True,
            "description": "Resolves Yes if it happens."}


def _event(eid, markets, tags=("politics",)):
    return {"id": str(eid), "slug": f"ev-{eid}", "title": f"Event {eid}", "tags": [{"slug": t} for t in tags], "markets": markets}


class _Llm:
    def __init__(self):
        self.calls = []

    async def complete(self, *, model, prompt, purpose, max_tokens, reasoning_effort=None):
        self.calls.append((purpose, model, prompt))
        out = '{"refers_to": "YES", "why": "ok"}' if purpose != "forecast" else "Probability: 30%"
        return LlmCall(purpose=purpose, provider="openrouter", model=model, params={}, prompt=prompt, requested_at=NOW,
                       output=out, tokens_in=10, tokens_out=5, cost_usd=0.01)


def _news(tmp_path):
    u = tmp_path / "usage.json"
    u.write_text(json.dumps({"as_of": "2026-11-26", "renews": "2026-12-29", "period_used": 100, "plan_credits": 500,
                             "wallet_usd": 20.0, "usd_per_credit": 0.0092}))
    return AskNewsGuard(usage_path=u, state_path=tmp_path / "a.json", today=date(2026, 11, 26), init_ok=True)


def _no_keys(monkeypatch):
    for k in ("ASKNEWS_API_KEY", "ASKNEWS_CLIENT_ID", "ASKNEWS_SECRET", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)


def _research_texts(llm):
    return [p for purpose, _, p in llm.calls if purpose == "forecast"]


def test_controls_run_their_own_calls_and_no_research_sees_none(tmp_path, monkeypatch):
    _no_keys(monkeypatch)
    from forecast_engine import research as research_mod
    from forecast_engine.schema import ResearchBundle, ResearchItem

    async def fake_gather(q, clock, cfg):
        return ResearchBundle(as_of=clock.now(), providers=["t"], items=[ResearchItem(source="t", text="SECRET-FACT 42")], calls=[])

    monkeypatch.setattr(run, "gather_research", fake_gather)
    qs = picker.pick([_event(1, [_market(1)])], NOW)
    llm = _Llm()
    out = asyncio.run(run.run_questions(qs, ("cheap", "cheap-aa", "no-research"), FixedClock(NOW), llm,
                                        SpendGuard(state_path=tmp_path / "s.json", init_ok=True), _news(tmp_path), root=tmp_path,
                                        experiment="EXP-017"))
    assert out["records"] == 3
    prompts = _research_texts(llm)
    assert len(prompts) == 9  # three forecasters per arm: the A/A copy makes its own calls
    assert sum("SECRET-FACT 42" in p for p in prompts) == 6 and sum("No research is available" in p for p in prompts) == 3
    rec = json.loads(next((tmp_path / "forecasts").glob("*-fast-no-research/q*.json")).read_text(encoding="utf-8"))
    assert rec["research"]["items"] == [] and rec["provenance"]["roster"] == "no-research"
    assert json.loads((tmp_path / "picks.jsonl").read_text().splitlines()[0])["experiment"] == "EXP-017"


def test_unknown_arm_or_a_baseline_without_research_is_refused(tmp_path):
    for bad in (("nope",), ("no-research", "cheap")):
        with pytest.raises(ValueError):
            asyncio.run(run.run_questions([], bad, FixedClock(NOW), _Llm(), SpendGuard(state_path=tmp_path / "s.json", init_ok=True),
                                          _news(tmp_path), root=tmp_path))


def test_writer_search_fee_is_counted_once():
    plain = LlmCall(purpose="research", provider="gemini", model="m", params={}, prompt="", requested_at=NOW,
                    extra={"search_queries": ["a", "b"]})
    writer = LlmCall(purpose="research", provider="gemini", model="w", params={}, prompt="", requested_at=NOW, cost_usd=0.05,
                     extra={"search_queries": ["a", "b", "c"], "grounding_fee_usd": 0.042})
    assert run._google_queries([plain, writer]) == 2


def test_experiment_cap_stops_inside_the_lane_cap(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_LIVE_KEY", raising=False)
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"ledger_usd": 10.0, "by_experiment": {"EXP-017": 44.5}}))
    g = SpendGuard(state_path=path, experiment="EXP-017", experiment_cap_usd=45.0)
    g.start(date(2026, 11, 26))
    with pytest.raises(GuardStop, match="EXP-017"):
        g.add(0.6)
    g.save()
    st = json.loads(path.read_text())
    assert st["ledger_usd"] == pytest.approx(10.6) and st["by_experiment"]["EXP-017"] == pytest.approx(45.1)
    with pytest.raises(GuardStop):
        SpendGuard(state_path=path, experiment="EXP-017", experiment_cap_usd=45.0).start(date(2026, 11, 27))
    SpendGuard(state_path=path, experiment="EXP-018", experiment_cap_usd=25.0).start(date(2026, 11, 27))  # another card's line


def test_picking_stops_at_the_cards_limits(tmp_path):
    assert run.picking_over(EXP, tmp_path, NOW) is None
    assert "passed" in run.picking_over(EXP, tmp_path, datetime(2027, 1, 21, tzinfo=timezone.utc))
    (tmp_path / "picks.jsonl").write_text("".join(json.dumps({"key": f"k{i}", "experiment": "EXP-017"}) + "\n" for i in range(320)))
    assert "maximum" in run.picking_over(EXP, tmp_path, NOW)


def test_main_pins_deepseek_and_serves_one_experiment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENROUTER_LIVE_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("FASTLANE_ON", "1")
    lane = tmp_path / "data/fastlane"
    lane.mkdir(parents=True)
    (lane / "spend.json").write_text('{"ledger_usd": 0}')
    seen = {}
    monkeypatch.setattr(run.AskNewsGuard, "why_not", lambda self, credits=1: None)
    monkeypatch.setattr(run, "collect", lambda *a, **k: [])

    class FakeClient:
        def __init__(self, clock, pinned_hosts=None):
            seen["pins"] = pinned_hosts

    async def fake_run(qs, arm_names, clock, llm, spend, news, **kw):
        seen.update(arms=arm_names, experiment=kw.get("experiment"), cap=spend.experiment_cap_usd)
        return {}

    monkeypatch.setattr(run, "OpenRouterClient", FakeClient)
    monkeypatch.setattr(run, "run_questions", fake_run)
    assert run.main(["--run", "--go"]) == 0
    assert seen == {"pins": experiments.PINNED_HOSTS, "arms": EXP.arms, "experiment": "EXP-017", "cap": 45.0}


# ---- picker across days ----

def test_an_event_is_never_picked_twice_and_all_tags_are_kept():
    tags = ("politics", "elections", "spain", "spain-elections", "psoe", "catalonia", "world", "pp")
    ev = _event(1, [_market(1), _market(2)], tags=tags)
    first = picker.pick([ev], NOW)
    assert len(first) == 1 and first[0].meta["tags"] == list(tags)
    assert picker.pick([ev], NOW, {first[0].key}, already_events={"ev-1"}) == []


def test_collect_skips_events_already_picked(tmp_path, monkeypatch):
    (tmp_path / "picks.jsonl").write_text(json.dumps({"key": "pm:1", "source": "polymarket", "meta": {"event_slug": "ev-1"}}) + "\n")
    monkeypatch.setattr(picker, "fetch_events", lambda get, now, cfg: [_event(1, [_market(1), _market(2)]), _event(2, [_market(3)])])
    monkeypatch.setattr(series, "make_questions", lambda *a, **k: [])
    assert [q.key for q in run.collect(None, NOW, tmp_path)] == ["pm:3"]


# ---- the look ----

def _write_look_set(tmp_path, n, shift=None, seed=0, arms_=EXP.arms):
    """n resolved binary questions; arm a forecasts p + shift[a] around a well-calibrated p."""
    rng = random.Random(seed)
    shift = shift or {}
    picks, outs = [], []
    for i in range(n):
        q = 1_000_000_000 + i
        p = rng.uniform(0.15, 0.85)
        o = int(rng.random() < p)
        closes = (NOW + timedelta(days=4 + i % 20)).isoformat()
        picks.append({"key": f"pm:{i}", "source": "polymarket", "question_id": q, "closes": closes, "experiment": "EXP-017",
                      "meta": {"tags": [f"topic-{i}"], "market_id": str(i)}})
        outs.append({"key": f"pm:{i}", "question_id": q, "outcome": o, "void": False})
        for a in arms_:
            noise = rng.gauss(0, 0.05)
            pa = min(0.97, max(0.03, p + noise + shift.get(a, 0.0) * (1 if o else -1)))
            d = tmp_path / "forecasts" / f"20261201T000000Z-fast-{a}"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"q{q}.json").write_text(json.dumps({"status": "fastlane", "aggregate": pa, "flags": [],
                                                      "question": {"question_id": q, "question_type": "binary"}}))
    (tmp_path / "picks.jsonl").write_text("".join(json.dumps(p) + "\n" for p in picks))
    (tmp_path / "outcomes.jsonl").write_text("".join(json.dumps(o) + "\n" for o in outs))


def test_look_is_locked_before_the_count_or_date(tmp_path):
    _write_look_set(tmp_path, 50)
    text, ok = analyze.look(EXP, tmp_path, NOW, reps=200)
    assert not ok and "Locked" in text and "+" not in text.split("Locked")[0].split("complete")[1]
    text, ok = analyze.look(EXP, tmp_path, datetime(2027, 1, 31, tzinfo=timezone.utc), reps=200)
    assert ok and "Too few" in text and "|" not in text  # past the date but under 140: counts only


def test_look_aa_passes_and_a_large_loss_is_seen(tmp_path):
    # the no-research arm pushed away from the truth, the A/A copy unchanged
    _write_look_set(tmp_path, 300, shift={"no-research": -0.25})
    text, ok = analyze.look(EXP, tmp_path, NOW, reps=2000)
    assert ok and "1. A/A: passes" in text and "2. Sensitivity: passes" in text
    again, _ = analyze.look(EXP, tmp_path, NOW, reps=2000)
    assert again == text  # re-scoring gives identical numbers (protocol §7)


def test_look_flags_a_broken_aa(tmp_path):
    _write_look_set(tmp_path, 300, shift={"cheap-aa": 0.2})
    text, _ = analyze.look(EXP, tmp_path, NOW, reps=2000)
    assert "1. A/A: FAILS" in text


def test_a_question_missing_any_arm_is_left_out_and_counted(tmp_path):
    _write_look_set(tmp_path, 300)
    (tmp_path / "forecasts" / "20261201T000000Z-fast-no-research" / "q1000000000.json").unlink()
    text, ok = analyze.look(EXP, tmp_path, NOW, reps=200)
    assert ok and "no valid forecast from no-research: 1" in text and "complete 299" in text


def test_clusters_join_shared_themes_in_a_week_and_series_by_family():
    picks = [
        {"key": "pm:1", "source": "polymarket", "question_id": 1, "closes": "2026-11-30T00:00:00+00:00", "meta": {"tags": ["politics", "spain"]}},
        {"key": "pm:2", "source": "polymarket", "question_id": 2, "closes": "2026-12-01T00:00:00+00:00", "meta": {"tags": ["spain", "psoe"]}},
        {"key": "pm:3", "source": "polymarket", "question_id": 3, "closes": "2026-11-29T00:00:00+00:00", "meta": {"tags": ["politics", "taiwan"]}},
        {"key": "pm:4", "source": "polymarket", "question_id": 4, "closes": "2026-12-10T00:00:00+00:00", "meta": {"tags": ["spain"]}},
        {"key": "s1", "source": "series", "question_id": 5, "closes": "2026-11-30T00:00:00+00:00", "meta": {"family": "weather", "city": "Oslo"}},
        {"key": "s2", "source": "series", "question_id": 6, "closes": "2026-12-02T00:00:00+00:00", "meta": {"family": "weather", "city": "Oslo"}},
        {"key": "s3", "source": "series", "question_id": 7, "closes": "2026-11-29T00:00:00+00:00", "meta": {"family": "weather", "city": "Mumbai"}},
        {"key": "s4", "source": "series", "question_id": 8, "closes": "2026-11-29T00:00:00+00:00", "meta": {"family": "fx", "symbol": "EUR"}},
        {"key": "s5", "source": "series", "question_id": 9, "closes": "2026-11-29T00:00:00+00:00", "meta": {"family": "fx", "symbol": "JPY"}},
    ]
    c = analyze.clusters(picks)
    assert c[1] == c[2] != c[3]  # Spain together; "politics" is a broad section, not a theme
    assert c[4] != c[1]  # same theme, another week
    assert c[5] == c[6] != c[7] and c[8] == c[9]


def test_number_questions_are_scored_on_the_live_cdf():
    now = NOW
    feed = lambda url, params: ({"daily": {"temperature_2m_max": [3.0 + i % 5 for i in range(15)]}} if "open-meteo" in url
                                else {"price": "70000"} if "ticker" in url else {"rates": {params["symbols"]: 0.9}})
    qs = {q.meta["family"]: q for q in series.make_questions(feed, now, n=12)}
    for fam, (agg, outcome) in {
        "weather": ([[0.1, 1], [0.2, 2], [0.4, 3], [0.6, 4], [0.8, 5], [0.9, 6]], 3.4),
        "quakes": ([[0.1, 6], [0.2, 8], [0.4, 10], [0.6, 12], [0.8, 14], [0.9, 16]], 11),
    }.items():
        rec = {"status": "fastlane", "aggregate": agg, "question": qs[fam].snapshot.to_dict()}
        good = analyze.score(rec, outcome, analyze.live_builder)
        far = analyze.score(rec, qs[fam].snapshot.upper_bound - 0.6, analyze.live_builder)
        assert good is not None and far is not None and good > far
