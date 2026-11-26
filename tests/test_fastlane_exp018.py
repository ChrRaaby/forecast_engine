"""EXP-018 build: joint run with EXP-017, split ledger, effort settings, shared writer, the 2x2 contrast, the pilot. Offline."""
import asyncio
import json
import random
from datetime import date, datetime, timedelta, timezone

import pytest

from fastlane import analyze, experiments, picker, run
from fastlane.guard import AskNewsGuard, SpendGuard
from forecast_engine import research_v2
from forecast_engine.clock import FixedClock
from forecast_engine.schema import LlmCall, ResearchBundle, ResearchItem

NOW = datetime(2026, 11, 26, 22, 0, tzinfo=timezone.utc)
E17, E18 = experiments.EXPERIMENTS["EXP-017"], experiments.EXPERIMENTS["EXP-018"]


def _market(i, price=0.4):
    end = (NOW + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": str(i), "question": f"Will thing {i} happen?", "outcomes": '["Yes", "No"]',
            "outcomePrices": json.dumps([str(price), str(1 - price)]), "clobTokenIds": '["t1", "t2"]',
            "createdAt": "2026-11-01T00:00:00Z", "endDate": end, "liquidityNum": 20000, "closed": False, "active": True,
            "description": "Resolves Yes if it happens."}


def _qs(n, start=0):
    return picker.pick([{"id": str(i), "slug": f"ev-{i}", "title": f"E{i}", "tags": [{"slug": f"t{i}"}], "markets": [_market(i)]}
                        for i in range(start, start + n)], NOW)


class _Llm:
    def __init__(self, cost=0.01):
        self.calls, self.cost = [], cost

    async def complete(self, *, model, prompt, purpose, max_tokens, reasoning_effort=None):
        self.calls.append({"purpose": purpose, "model": model, "max_tokens": max_tokens, "effort": reasoning_effort, "prompt": prompt})
        out = '{"refers_to": "YES", "why": "ok"}' if purpose != "forecast" else "Probability: 30%"
        return LlmCall(purpose=purpose, provider="openrouter", model=model, params={}, prompt=prompt, requested_at=NOW, output=out,
                       tokens_in=10, tokens_out=5, cost_usd=self.cost, latency_s=1.0)


def _news(tmp_path):
    u = tmp_path / "usage.json"
    u.write_text(json.dumps({"as_of": "2026-11-26", "renews": "2026-12-29", "period_used": 100, "plan_credits": 500,
                             "wallet_usd": 20.0, "usd_per_credit": 0.0092}))
    return AskNewsGuard(usage_path=u, state_path=tmp_path / "a.json", today=date(2026, 11, 26), init_ok=True)


@pytest.fixture
def offline_research(monkeypatch):
    for k in ("ASKNEWS_API_KEY", "ASKNEWS_CLIENT_ID", "ASKNEWS_SECRET", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    async def fake_gather(q, clock, cfg):
        return ResearchBundle(as_of=clock.now(), providers=["t"], items=[ResearchItem(source="t", text="news")], calls=[])

    writer_calls = []

    async def fake_apply(q, bundle, clock, cfg, llm, cache=None):  # the writer's brief, fetched once per question and cache
        out = research_v2.copy_bundle(bundle)
        if "writer" not in cache:
            writer_calls.append(q.question_id)
            cache["writer"] = LlmCall(purpose="research", provider="gemini", model="writer", params={}, prompt="", requested_at=NOW,
                                      cost_usd=0.02, extra={"grounding_fee_usd": 0.01, "search_queries": ["a"]})
        out.calls.append(cache["writer"])
        return out

    monkeypatch.setattr(run, "gather_research", fake_gather)
    monkeypatch.setattr(research_v2, "apply", fake_apply)
    return writer_calls


def test_plan_joins_on_the_same_baseline_and_assigns_owners():
    arms_, owners = experiments.plan(["EXP-017", "EXP-018"])
    assert arms_ == ("cheap", "cheap-old-prompt", "exp013-c-writer", "cheap-aa", "no-research", "effort-high", "writer-effort-high")
    assert owners["cheap"] == owners["exp013-c-writer"] == "EXP-017" and owners["effort-high"] == owners["writer-effort-high"] == "EXP-018"
    assert experiments.plan(["EXP-018"])[1]["cheap"] == "EXP-018"


def test_exp018_on_card():
    assert E18.contrast == (("effort-high", "cheap"), ("writer-effort-high", "exp013-c-writer"))
    assert E18.cap_usd == 25.0 and E18.look_n == 280 and E18.second_look_n == 560 and E18.level == pytest.approx(0.95)
    assert E17.cap_usd + E18.cap_usd <= 80


def test_joint_run_effort_reaches_only_b_and_d_and_the_writer_is_shared(tmp_path, offline_research):
    arms_, owners = experiments.plan(["EXP-017", "EXP-018"])
    llm = _Llm()
    spend = SpendGuard(state_path=tmp_path / "s.json", init_ok=True, experiment="EXP-017", experiment_cap_usd=45.0,
                       extra_caps={"EXP-018": 25.0})
    out = asyncio.run(run.run_questions(_qs(2), arms_, FixedClock(NOW), llm, spend, _news(tmp_path), root=tmp_path,
                                        max_run_usd=5.0, experiment="EXP-017", owners=owners))
    assert out["records"] == 14 and out["arms_capped"] == 0
    assert len(offline_research) == 2  # one writer brief per question for C and D together
    fc = [c for c in llm.calls if c["purpose"] == "forecast"]
    high = [c for c in fc if c["effort"] == "high"]
    assert len(high) == 2 * 2 * 3 and all(c["max_tokens"] == 32000 for c in high)  # 2 questions x 2 arms x 3 models
    assert all(c["effort"] == "medium" and c["max_tokens"] == 16000 for c in fc if c not in high)
    assert all(c["effort"] != "high" for c in llm.calls if c["purpose"] != "forecast")
    st = json.loads((tmp_path / "s.json").read_text())
    # EXP-018 pays only its two arms' forecaster and polarity calls: 2 questions x 2 arms x (3 + 3) calls x $0.01
    assert st["by_experiment"]["EXP-018"] == pytest.approx(0.24)
    assert st["ledger_usd"] == pytest.approx(st["by_experiment"]["EXP-017"] + st["by_experiment"]["EXP-018"])
    rows = [json.loads(l) for l in (tmp_path / "picks.jsonl").read_text().splitlines()]
    assert all(r["experiments"] == ["EXP-017", "EXP-018"] for r in rows)
    assert run.picked_for(E18, tmp_path) == 2 and run.picked_for(E17, tmp_path) == 2


def test_a_joined_experiment_at_its_cap_loses_only_its_own_arms(tmp_path, offline_research):
    (tmp_path / "s.json").write_text(json.dumps({"ledger_usd": 25.0, "by_experiment": {"EXP-018": 25.0}}))
    arms_, owners = experiments.plan(["EXP-017", "EXP-018"])
    spend = SpendGuard(state_path=tmp_path / "s.json", experiment="EXP-017", experiment_cap_usd=45.0, extra_caps={"EXP-018": 25.0})
    out = asyncio.run(run.run_questions(_qs(1), arms_, FixedClock(NOW), _Llm(), spend, _news(tmp_path), root=tmp_path,
                                        max_run_usd=5.0, experiment="EXP-017", owners=owners))
    assert out["records"] == 5 and out["arms_capped"] == 2
    assert not list((tmp_path / "forecasts").glob("*-fast-effort-high/*"))


def _write_2x2(tmp_path, n, gain):
    rng = random.Random(7)
    picks, outs = [], []
    for i in range(n):
        q = 1_000_000_000 + i
        p = rng.uniform(0.2, 0.8)
        o = int(rng.random() < p)
        picks.append({"key": f"pm:{i}", "source": "polymarket", "question_id": q, "closes": (NOW + timedelta(days=4 + i % 30)).isoformat(),
                      "experiments": ["EXP-017", "EXP-018"], "experiment": "EXP-017", "meta": {"tags": [f"x{i}"]}})
        outs.append({"key": f"pm:{i}", "question_id": q, "outcome": o, "void": False})
        for a in E18.arms:
            g = gain if a in ("effort-high", "writer-effort-high") else 0.0
            pa = min(0.97, max(0.03, p + rng.gauss(0, 0.04) + g * (1 if o else -1)))
            d = tmp_path / "forecasts" / f"20261201T000000Z-fast-{a}"
            d.mkdir(parents=True, exist_ok=True)
            fs = [{"model": m, "prediction": min(0.97, max(0.03, pa + rng.gauss(0, 0.02))), "excluded": None,
                   "call": {"tokens_out": 100 if g == 0 else 300, "latency_s": 2.0, "cost_usd": 0.003, "extra": {"finish_reason": "stop"}}}
                  for m in ("m1", "m2", "m3")]
            (d / f"q{q}.json").write_text(json.dumps({"status": "fastlane", "aggregate": pa, "flags": [], "forecasters": fs,
                                                      "question": {"question_id": q, "question_type": "binary"}}))
    (tmp_path / "picks.jsonl").write_text("".join(json.dumps(x) + "\n" for x in picks))
    (tmp_path / "outcomes.jsonl").write_text("".join(json.dumps(x) + "\n" for x in outs))


def test_contrast_is_locked_then_finds_a_real_gain(tmp_path):
    _write_2x2(tmp_path, 300, gain=0.08)
    text, ok = analyze.look(E18, tmp_path, datetime(2026, 12, 10, tzinfo=timezone.utc), reps=1000)
    assert ok and "Primary: mean +" in text and "CI above 0" in text
    assert "| m1 |" in text and "| effort-high |" in text and "interaction" in text
    locked, ok2 = analyze.look(E18, tmp_path / "missing", NOW, reps=100)
    assert not ok2 and "Locked" in locked


def test_contrast_without_a_gain_is_inconclusive(tmp_path):
    _write_2x2(tmp_path, 300, gain=0.0)
    text, _ = analyze.look(E18, tmp_path, NOW, reps=1000)
    assert "inconclusive" in text or "killed" in text
    assert "CI above 0" not in text


def test_contrast_arithmetic_on_a_tiny_set():
    scores = {"cheap": {1: 10.0, 2: 20.0}, "effort-high": {1: 14.0, 2: 20.0}, "exp013-c-writer": {1: 0.0, 2: 0.0},
              "writer-effort-high": {1: 6.0, 2: -2.0}}
    cl = {1: "a", 2: "b"}
    lines = analyze.contrast_section(E18, scores, [1, 2], cl, {1: "polymarket", 2: "series"}, set(), {a: {} for a in scores},
                                     {a: {} for a in scores}, reps=200)
    prim = [l for l in lines if l.startswith("| all | primary")][0]
    inter = [l for l in lines if l.startswith("| all | interaction")][0]
    assert "| +2.00 |" in prim  # q1: (4 + 6) / 2 = 5; q2: (0 − 2) / 2 = −1; mean 2
    assert "| +0.00 |" in inter  # q1: 6 − 4 = 2; q2: −2 − 0 = −2; mean 0


def test_pilot_table_pass_and_fail(tmp_path):
    def write(arm, n, parsed, secs=10.0):
        d = tmp_path / "forecasts" / f"20261201T000000Z-fast-{arm}"
        d.mkdir(parents=True, exist_ok=True)
        for i in range(n):
            fs = [{"model": m, "prediction": 0.3 if i < parsed else None,
                   "call": {"tokens_out": 500, "latency_s": secs, "cost_usd": 0.002, "extra": {"finish_reason": "stop"}}}
                  for m in ("m1", "m2")]
            (d / f"q{i}.json").write_text(json.dumps({"forecasters": fs}))
    write("cheap", 10, 10)
    write("effort-high", 10, 9)
    table, ok = run.pilot_table(tmp_path)
    assert ok and "**Pass**" in table and "| m1 | effort-high | 10 | 9 |" in table
    write("effort-high", 10, 8, secs=400)
    table, ok = run.pilot_table(tmp_path)
    assert not ok and "Not passed" in table


def test_pilot_and_run_refuse_without_go(monkeypatch, capsys):
    monkeypatch.delenv("FASTLANE_ON", raising=False)
    assert run.main(["--pilot-effort"]) == 2
    assert run.main(["--run", "--go", "--experiment", "EXP-017,EXP-018"]) == 2
    assert run.main(["--report", "--experiment", "EXP-017,EXP-017"]) == 2  # repeated
    assert "refusing to spend" in capsys.readouterr().out
