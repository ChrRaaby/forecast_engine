"""FAST lane (EXP-017): picker, series, scrubber, guards, resolver and runner, all offline."""
import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from fastlane import picker, resolve, run, series
from fastlane.guard import AskNewsGuard, GuardStop, SpendGuard
from fastlane.scrub import scrub_bundle, scrub_text
from forecast_engine.clock import FixedClock
from forecast_engine.schema import LlmCall, ResearchBundle, ResearchItem

NOW = datetime(2026, 11, 16, 21, 0, tzinfo=timezone.utc)
AFTER = datetime(2026, 11, 25, 3, 0, tzinfo=timezone.utc)


def _market(i, end_days=5, liq=20000, closed=False, title=None, price=0.37):
    end = (NOW + timedelta(days=end_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": str(i), "question": title or f"Will thing {i} happen?", "outcomes": '["Yes", "No"]',
            "outcomePrices": json.dumps([str(price), str(round(1 - price, 4))]),
            "clobTokenIds": '["t1", "t2"]', "createdAt": "2026-11-01T00:00:00Z", "endDate": end, "liquidityNum": liq,
            "closed": closed, "active": True, "description": "Resolves Yes if it happens."}


def _event(eid, markets, tags=("politics",)):
    return {"id": str(eid), "slug": f"ev-{eid}", "title": f"Event {eid}", "tags": [{"slug": t} for t in tags], "markets": markets}


# ---- picker ----

def test_picker_filters_and_caps():
    events = [
        _event(1, [_market(11), _market(12)]),                       # two markets, one event: only one may be picked
        _event(2, [_market(21, end_days=1)]),                        # closes too soon
        _event(3, [_market(31, end_days=9)]),                        # too far
        _event(4, [_market(41, liq=100)]),                           # illiquid
        _event(5, [_market(51, closed=True)]),                       # closed
        _event(6, [_market(61)], tags=("crypto", "bitcoin")),        # crypto: not source A
        _event(7, [_market(71)]),
    ]
    ids = sorted(q.snapshot.question_id - 1_000_000_000 for q in picker.pick(events, NOW))
    assert not {21, 31, 41, 51, 61} & set(ids)
    assert len([i for i in ids if i in (11, 12)]) == 1 and 71 in ids


def test_picker_price_band_and_price_never_reaches_the_question():
    events = [_event(1, [_market(1, price=0.04)]), _event(2, [_market(2, price=0.96)]), _event(3, [_market(3, price=0.1)]),
              _event(4, [_market(4, price=0.9)]), _event(5, [_market(5, price=0.4321)])]
    got = {q.snapshot.question_id - 1_000_000_000: q for q in picker.pick(events, NOW)}
    assert set(got) == {3, 4, 5}  # under 10% and over 90% are dropped, the edges stay
    q = got[5]
    assert q.price_at_pick == pytest.approx(0.4321)
    blob = json.dumps(q.snapshot.to_dict()) + json.dumps(q.meta) + json.dumps(q.to_pick(NOW))
    assert "0.4321" not in blob and "4321" not in blob and "outcomePrices" not in blob


def test_picker_ids_cannot_collide_with_metaculus_or_series():
    q = picker.pick([_event(1, [_market(46858)])], NOW)[0]
    assert q.snapshot.question_id == 1_000_046_858


def test_picker_respects_max_picks_already_picked_tag_caps_and_is_deterministic():
    events = [_event(i, [_market(100 + i)]) for i in range(20)]
    assert len(picker.pick(events, NOW, cfg=picker.PickerConfig(max_picks=5))) == 5
    first = picker.pick(events, NOW, cfg=picker.PickerConfig(max_picks=20))
    again = picker.pick(events, NOW, {q.key for q in first[:3]}, picker.PickerConfig(max_picks=20))
    assert not {q.key for q in first[:3]} & {q.key for q in again}
    tweets = [_event(i, [_market(200 + i)], tags=("tweets-markets",)) for i in range(6)]
    assert len(picker.pick(tweets, NOW)) == 2
    a = [q.key for q in picker.pick(events, NOW)]
    assert a == [q.key for q in picker.pick(list(reversed(events)), NOW)]


# ---- odds scrubber ----

def test_scrub_removes_odds_and_market_mentions_and_keeps_news():
    text = ("The senator announced the bill on Monday. Polymarket traders give it a 62% chance. Analysts expect a vote next week. "
            "The odds are 3-1 against.")
    out, n = scrub_text(text)
    assert n == 2 and "Polymarket" not in out and "odds" not in out and "announced the bill" in out and "vote next week" in out


def test_scrub_bundle_drops_market_hosts_and_reports_without_keeping_text():
    b = ResearchBundle(as_of=NOW, providers=["x"], calls=[], items=[
        ResearchItem(source="asknews_latest", text="Market says 70%.", url="https://polymarket.com/event/x", title="Will X?"),
        ResearchItem(source="asknews_latest", text="Prediction markets price it at 55%. Rain fell on Sunday.", url="https://news.example/a"),
        ResearchItem(source="gemini", text="Only news here."),
        ResearchItem(source="asknews_latest", text="Kalshi odds are 30%."),
    ])
    out, rep = scrub_bundle(b)
    assert rep.items_dropped == 2 and rep.sentences_removed == 2
    assert [i.text for i in out.items] == ["Rain fell on Sunday.", "Only news here."]
    assert len(b.items) == 4  # the original is untouched
    assert "scrubbed" in rep.flag()


# ---- series ----

def _feed(url, params):
    if "/ticker" in url:
        return {"price": "70000.0"}
    if url.endswith("/latest"):
        return {"rates": {params["symbols"]: 0.88}}
    raise AssertionError(url)


def test_series_are_number_questions_in_several_independent_families():
    qs = series.make_questions(_feed, NOW, n=12)
    assert len(qs) == 12
    fams = {q.meta["family"] for q in qs}
    assert {"weather", "quakes"} <= fams
    assert sum(q.meta["family"] == "crypto" for q in qs) <= 3  # crypto moves together: capped
    for q in qs:
        s = q.snapshot
        assert s.question_type in ("numeric", "discrete") and s.question_id >= 2_000_000_000
        assert s.lower_bound is not None and s.upper_bound > s.lower_bound
        assert 3 <= (q.closes - NOW).days <= 9
        assert "polymarket" not in json.dumps(s.to_dict()).lower()
    assert len({q.key for q in qs}) == 12


def test_series_resolution_waits_and_handles_holidays():
    day = date(2026, 11, 19)
    start = int(datetime(2026, 11, 19, tzinfo=timezone.utc).timestamp())

    def get(url, params):
        if "candles" in url:
            return [[start, 1, 2, 3, 71000.0, 5]]
        if "frankfurter" in url:
            # 19 Nov was a holiday: the service answers with the rate of the 18th
            return {"date": "2026-11-18" if day.isoformat() in url else "2026-11-19", "rates": {"EUR": 0.89}}
        if "open-meteo" in url:
            return {"daily": {"temperature_2m_max": [7.5]}}
        if "usgs" in url:
            return {"count": 9}

    crypto = {"family": "crypto", "symbol": "BTC-USD", "day": day.isoformat()}
    assert series.resolve(get, crypto, datetime(2026, 11, 19, 12, tzinfo=timezone.utc)) is None  # day not over
    assert series.resolve(get, crypto, AFTER) == 71000.0
    fx = {"family": "fx", "symbol": "EUR", "day": day.isoformat()}
    assert series.resolve(get, fx, datetime(2026, 11, 20, 12, tzinfo=timezone.utc)) is None  # ECB publication not in yet
    assert series.resolve(get, fx, AFTER) == 0.89  # holiday: latest earlier rate counts
    assert series.resolve(get, {"family": "weather", "lat": 1, "lon": 2, "day": day.isoformat()}, AFTER) == 7.5
    assert series.resolve(get, {"family": "quakes", "first": "2026-11-17", "last": "2026-11-19"}, AFTER) == 9.0
    assert series.resolve(get, {"family": "quakes", "first": "2026-11-17", "last": "2026-11-19"}, datetime(2026, 11, 20, 1, tzinfo=timezone.utc)) is None


# ---- resolver ----

def test_polymarket_outcome():
    mk = lambda closed, p: (lambda u, params: {"closed": closed, "outcomes": '["Yes","No"]', "outcomePrices": p})
    assert [resolve.polymarket_outcome(mk(*a), "1") for a in
            ((True, '["1","0"]'), (True, '["0","1"]'), (False, '["0.5","0.5"]'), (True, '["0.5","0.5"]'))] == [1, 0, None, None]


def _pick(key, source, closes, **meta):
    return {"key": key, "source": source, "question_id": 7, "closes": closes.isoformat(), "meta": meta, "type": "binary"}


def test_update_resolves_voids_and_counts(tmp_path):
    (tmp_path / "picks.jsonl").write_text("\n".join(json.dumps(p) for p in (
        _pick("pm:1", "polymarket", NOW - timedelta(days=2), market_id="1"),     # resolves
        _pick("pm:2", "polymarket", NOW - timedelta(days=40), market_id="2"),    # never resolves: void
        _pick("pm:3", "polymarket", NOW - timedelta(days=2), market_id="3"),     # stuck but not yet void
        _pick("pm:4", "polymarket", NOW + timedelta(days=2), market_id="4"),     # still open
    )) + "\n", encoding="utf-8")

    def get(url, params):
        if url.endswith("/1"):
            return {"closed": True, "outcomes": '["Yes","No"]', "outcomePrices": '["1","0"]'}
        return {"closed": False, "outcomes": '["Yes","No"]', "outcomePrices": '["0.5","0.5"]'}

    assert resolve.update(get, NOW, tmp_path) == {"resolved": 1, "voided": 1, "open": 2, "resolved_total": 2}
    assert resolve.update(get, NOW, tmp_path)["resolved"] == 0  # idempotent
    c = resolve.counts(tmp_path)
    assert c["by_source"]["polymarket"] == {"picked": 4, "resolved": 1, "voided": 1, "open": 2}
    assert not any("brier" in json.dumps(c).lower() for _ in [0])  # counts only


# ---- guards ----

def test_spend_guard_ledger_cap_google_fee_and_live_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_LIVE_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    path = tmp_path / "s.json"
    g = SpendGuard(cap_usd=1.0, state_path=path, init_ok=True)
    g.start(date(2026, 11, 20))
    g.add(0.5, google_queries=10)  # 0.5 + 10 * 0.014 = 0.64
    assert g.spent() == pytest.approx(0.64)
    with pytest.raises(GuardStop):
        g.add(0.4)
    g.save()
    assert json.loads(path.read_text())["google_queries"] == 10
    with pytest.raises(GuardStop):
        SpendGuard(cap_usd=1.0, state_path=path, init_ok=False).start(date(2026, 11, 20))  # cap reached, persisted
    with pytest.raises(GuardStop):
        SpendGuard(state_path=tmp_path / "t.json", init_ok=True).start(date(2027, 2, 1))
    monkeypatch.setenv("OPENROUTER_LIVE_KEY", "k")
    with pytest.raises(GuardStop):
        SpendGuard(state_path=tmp_path / "u.json", init_ok=True).start(date(2026, 11, 20))


def test_guards_fail_closed_on_missing_or_corrupt_state(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_LIVE_KEY", raising=False)
    with pytest.raises(GuardStop):
        SpendGuard(state_path=tmp_path / "none.json", init_ok=False).start(date(2026, 11, 20))
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(GuardStop):
        SpendGuard(state_path=bad, init_ok=True).start(date(2026, 11, 20))
    a = AskNewsGuard(base_used=0, state_path=tmp_path / "none2.json", today=date(2026, 11, 20), init_ok=False)
    assert not a.allow(1)
    assert not AskNewsGuard(base_used=0, state_path=bad, today=date(2026, 11, 20), init_ok=True).allow(1)


def test_asknews_guard_keeps_the_live_bots_credits(tmp_path):
    day = date(2026, 11, 20)  # period 30 Oct - 29 Nov: 10 days left, 60 credits held for the live bot
    mk = lambda used, n: AskNewsGuard(base_used=used, state_path=tmp_path / f"{n}.json", today=day, init_ok=True)
    assert mk(597, "a").allow(1)
    assert mk(1689, "b").allow(1) and not mk(1690, "c").allow(1)  # 1,750 - 60
    assert not mk(10000, "d").allow(1)  # unknown usage: no AskNews


def test_asknews_guard_caps_and_counts_skips(tmp_path):
    g = AskNewsGuard(base_used=0, state_path=tmp_path / "a.json", today=date(2026, 11, 20), daily_cap=3, total_cap=5, init_ok=True)
    for _ in range(3):
        assert g.allow(1)
        g.take(1)
    assert not g.allow(1)
    g.skip(1)
    g.today = date(2026, 11, 21)
    assert g.allow(1) and g.allow(2) and not g.allow(3)  # total cap 5, 3 used
    assert json.loads((tmp_path / "a.json").read_text())["skipped"] == 1 and g.skipped_run == 1


# ---- runner ----

def test_run_refuses_without_go_and_switch(monkeypatch, capsys):
    monkeypatch.delenv("FASTLANE_ON", raising=False)
    assert run.main(["--run"]) == 2
    monkeypatch.setenv("FASTLANE_ON", "1")
    assert run.main(["--run"]) == 2  # still no --go
    assert "refusing to spend" in capsys.readouterr().out


class _Llm:
    def __init__(self):
        self.prompts = []

    async def complete(self, *, model, prompt, purpose, max_tokens, reasoning_effort=None):
        self.prompts.append(prompt)
        out = '{"refers_to": "YES", "why": "ok"}' if purpose != "forecast" else "Probability: 30%"
        return LlmCall(purpose=purpose, provider="openrouter", model=model, params={}, prompt=prompt, requested_at=NOW,
                       output=out, tokens_in=10, tokens_out=5, cost_usd=0.01)


def _no_keys(monkeypatch):
    for k in ("ASKNEWS_API_KEY", "ASKNEWS_CLIENT_ID", "ASKNEWS_SECRET", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)


def test_run_questions_records_flags_private_prices_and_counts_asknews_skips(tmp_path, monkeypatch):
    _no_keys(monkeypatch)
    qs = picker.pick([_event(i, [_market(400 + i, price=0.3 + i / 100)]) for i in range(3)], NOW)
    spend = SpendGuard(cap_usd=80.0, state_path=tmp_path / "s.json", init_ok=True)
    news = AskNewsGuard(base_used=10000, state_path=tmp_path / "a.json", today=date(2026, 11, 16), init_ok=True)
    llm = _Llm()
    out = asyncio.run(run.run_questions(qs, ("cheap",), FixedClock(NOW), llm, spend, news, root=tmp_path))
    assert out["questions"] == 3 and out["records"] == 3 and out["asknews_credits"] == 0 and out["asknews_skipped"] == 3
    recs = sorted((tmp_path / "forecasts").glob("*-fast-cheap/q*.json"))
    rec = json.loads(recs[0].read_text(encoding="utf-8"))
    assert rec["status"] == "fastlane" and rec["published"] is False and rec["provenance"]["role"] == "fastlane"
    assert any("AskNews skipped" in f for f in rec["flags"])
    # the market price lives only in the private file: not in any record, pick row or prompt
    prices = [json.loads(l) for l in (tmp_path / "market_prices.jsonl").read_text().splitlines()]
    assert len(prices) == 3 and all(0.3 <= p["price_at_pick"] <= 0.33 for p in prices)
    for r in recs:
        assert "price_at_pick" not in r.read_text(encoding="utf-8")
    assert "price_at_pick" not in (tmp_path / "picks.jsonl").read_text()
    assert not any("0.31" in p or "0.32" in p for p in llm.prompts)
    assert json.loads((tmp_path / "s.json").read_text())["ledger_usd"] > 0  # saved after the questions
    assert json.loads((tmp_path / "a.json").read_text())["skipped"] == 3


def test_run_questions_numeric_series_question(tmp_path, monkeypatch):
    _no_keys(monkeypatch)

    class NumLlm(_Llm):
        async def complete(self, **kw):
            call = await super().complete(**kw)
            if kw["purpose"] == "forecast":
                call.output = "Percentile 10: 1\nPercentile 20: 2\nPercentile 40: 3\nPercentile 60: 4\nPercentile 80: 5\nPercentile 90: 6"
            return call

    qs = [q for q in series.make_questions(_feed, NOW, n=12) if q.meta["family"] == "weather"][:1]
    out = asyncio.run(run.run_questions(qs, ("cheap",), FixedClock(NOW), NumLlm(),
                                        SpendGuard(state_path=tmp_path / "s.json", init_ok=True),
                                        AskNewsGuard(base_used=10000, state_path=tmp_path / "a.json", init_ok=True), root=tmp_path))
    assert out["records"] == 1 and out["failed"] == 0


def test_run_questions_stops_at_run_cost_cap(tmp_path, monkeypatch):
    _no_keys(monkeypatch)
    qs = picker.pick([_event(i, [_market(500 + i)]) for i in range(5)], NOW)
    out = asyncio.run(run.run_questions(qs, ("cheap",), FixedClock(NOW), _Llm(), SpendGuard(state_path=tmp_path / "s.json", init_ok=True),
                                        AskNewsGuard(base_used=10000, state_path=tmp_path / "a.json", init_ok=True), root=tmp_path,
                                        max_run_usd=0.02))
    assert out["stopped"] and out["questions"] < 5
