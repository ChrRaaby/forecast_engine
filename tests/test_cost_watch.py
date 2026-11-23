"""B-101 cost watch: what is counted, when it warns, whom it tells. Offline, made-up numbers."""
import json
from datetime import date, datetime, timedelta, timezone

import pytest

import tools.build_monitor as bm
import tools.cost_watch as cw

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
LIVE_HASH = "d7d19c6f8464"


def _fact(arm="live", days_ago=1.0, forecasts=0.17, steps=0.02, credits=7, queries=3, h=LIVE_HASH, gemini=0.0):
    return {"arm": arm, "at": (NOW - timedelta(days=days_ago)).isoformat()[:19], "qid": 1, "hash": h, "forecasts": forecasts,
            "openrouter_steps": steps, "gemini_steps": gemini, "credits": credits, "queries": queries}


def _lines(facts, season=(), keys=(), limit=250.0, reading=None, lane=0):
    return {x.key: x for x in cw.evaluate(facts, list(season), list(keys), limit, reading, lane, NOW)}


def _call(purpose, provider, cost=0.0, at="t1", **extra):
    return {"purpose": purpose, "provider": provider, "model": "m", "requested_at": at, "cost_usd": cost, "extra": extra}


def _record(calls, n=2, cost=0.05, h=LIVE_HASH):
    return {"as_of": "2026-11-22T10:00:00+00:00", "question": {"question_id": 5}, "config_hash": h,
            "forecasters": [{"call": {"cost_usd": cost}, "check": {"cost_usd": 0.01}} for _ in range(n)], "research": {"calls": calls}}


def test_google_fee_is_not_real_spend_and_shared_steps_count_once():
    calls = [_call("research", "gemini", cost=0.002), _call("exp013_brief_writer", "gemini", cost=0.06, token_cost_usd=0.011,
                                                           search_queries=["a", "b"]),
             _call("exp013_source_extract", "openrouter", cost=0.02), _call("exp013_news", "asknews", asknews_credits=5),
             _call("research", "asknews", at="t0", asknews_calls=1)]
    seen = set()
    live = cw.record_facts("20261122T100000Z-tournament", _record(calls), seen)
    arm = cw.record_facts("20261122T100000Z-tournament-shadow-exp013-b-source", _record(calls), seen)
    assert live["forecasts"] == pytest.approx(0.12) and live["openrouter_steps"] == pytest.approx(0.02)
    assert live["gemini_steps"] == pytest.approx(0.013) and (live["credits"], live["queries"]) == (6, 2)
    assert arm["arm"] == "exp013-b-source" and arm["forecasts"] == pytest.approx(0.12)
    assert (arm["openrouter_steps"], arm["gemini_steps"], arm["credits"], arm["queries"]) == (0, 0, 0, 0)  # the live one paid


def test_a_failed_asknews_call_is_not_counted_as_credits():
    c = _call("exp013_news", "asknews", asknews_credits=5) | {"error": "400"}
    assert cw.record_facts("20261122T100000Z-tournament", _record([c]), set())["credits"] == 0


def test_live_cost_per_question_warns_above_its_limit_and_only_with_enough_questions():
    assert _lines([_fact() for _ in range(10)])[f"live per question ({LIVE_HASH})"].status == "OK"
    assert _lines([_fact(forecasts=0.25) for _ in range(10)])[f"live per question ({LIVE_HASH})"].status == "WARN"
    assert _lines([_fact(forecasts=0.25) for _ in range(3)])[f"live per question ({LIVE_HASH})"].status == "INFO"
    old = [_fact(forecasts=0.25, days_ago=20) for _ in range(10)]  # outside the window
    assert f"live per question ({LIVE_HASH})" not in _lines(old)


def test_season_and_key_projections():
    slow, fast = [("2026-11-13T10:00", 31.0), ("2026-11-23T10:00", 41.0)], [("2026-11-13T10:00", 31.0), ("2026-11-23T10:00", 101.0)]
    assert _lines([], season=slow)["season cap"].status == "OK" and _lines([], season=fast)["season cap"].status == "WARN"
    key = [("2026-11-15T20:36", 54.70), ("2026-11-23T22:00", 82.32)]  # the real readings: $3.43 a day, limit reached in January
    x = _lines([], keys=key)["experiments key"]
    assert x.status == "WARN" and "2027-01-1" in x.text
    assert _lines([], keys=[("2026-11-15T20:36", 54.70), ("2026-11-23T22:00", 56.00)])["experiments key"].status == "OK"
    assert _lines([], keys=[("2026-11-23T22:00", 56.00)])["experiments key"].status == "INFO"  # one reading: no pace yet


def test_exp013_lines():
    rows = [_fact("exp013-b-source", 9, 0.01, 0.005)] + [_fact("exp013-f-frontier-full", 5, 0.21, 0) for _ in range(5)]
    x = _lines(rows)
    assert x["EXP-013 cheap arms"].status == "OK" and x["EXP-013 frontier arm per question"].status == "WARN"
    at_accepted = rows[:1] + [_fact("exp013-frontier-old", 5, 0.17, 0) for _ in range(5)]
    assert _lines(at_accepted)["EXP-013 frontier arm per question"].status == "OK"  # Christian accepted ~$0.17 (2026-11-23)
    assert _lines([_fact("exp013-c-writer", 9, 1.0, 0, gemini=12.0)])["EXP-013 cheap arms"].status == "WARN"
    both = [_fact("exp013-f-frontier-full", 9, 0.1, 0), _fact("exp013-frontier-old", 1, 0.1, 0), _fact("exp013-b-source", 9, 0, 0)]
    assert "(2 questions)" in _lines(both)["EXP-013 frontier arm per question"].text  # F and its successor are one line


def test_asknews_lines_name_the_driver():
    reading = {"as_of": "2026-11-23", "renews": "2026-11-29", "period_used": 891, "plan_credits": 500, "wallet_usd": 21.42,
               "usd_per_credit": 0.0092}
    x = _lines([_fact() for _ in range(40)], reading=reading)
    assert x["AskNews per question"].status == "OK" and x["AskNews burn"].status == "OK" and x["AskNews wallet"].status == "OK"
    x = _lines([_fact(credits=10) for _ in range(40)], reading=reading)
    assert x["AskNews per question"].status == "WARN" and x["AskNews burn"].status == "WARN"
    x = _lines([_fact() for _ in range(20)], reading=reading, lane=400)
    assert x["AskNews burn"].status == "WARN" and "mostly fast lane" in x["AskNews burn"].text and x["AskNews wallet"].status == "WARN"


def test_wallet_projection_by_hand():
    reading = {"as_of": "2026-11-23", "renews": "2026-11-29", "period_used": 891, "plan_credits": 500, "wallet_usd": 21.42,
               "usd_per_credit": 0.0092}
    # 6 days x 42 all over the plan; then two 30-day periods of 1,260 credits (760 over each); then 4 days inside the plan
    want = 21.42 - 0.0092 * (6 * 42 + 760 + 760)
    assert cw.wallet_at_horizon(reading, 42, date(2027, 1, 31), NOW.date()) == pytest.approx(want)


def test_the_newest_reading_wins(tmp_path):
    for n, d in (("a", "2026-11-20"), ("b", "2026-11-23")):
        (tmp_path / n).write_text(json.dumps({"as_of": d, "renews": "2026-11-29"}))
    (tmp_path / "broken").write_text("{oops")
    got = cw.asknews_reading([tmp_path / "a", tmp_path / "broken", tmp_path / "missing", tmp_path / "b"])
    assert got["as_of"] == "2026-11-23" and cw.asknews_reading([tmp_path / "missing"]) is None


def test_notified_once_then_reminded_and_again_after_recovery(tmp_path, monkeypatch):
    shown, state = [], {"status": "WARN"}
    monkeypatch.setattr(cw, "toast", lambda title, text: shown.append(text) or True)
    monkeypatch.setattr(cw, "openrouter_key", lambda: None)
    monkeypatch.setattr(cw, "evaluate", lambda *a, **k: [cw.Line("season cap", state["status"], "too much")])
    go = lambda d: cw.run(True, True, NOW + timedelta(days=d), tmp_path, tmp_path)  # noqa: E731
    go(0), go(1)
    assert len(shown) == 1  # told once, not every day
    go(8)
    assert len(shown) == 2  # reminder after a week
    state["status"] = "OK"
    go(9)
    state["status"] = "WARN"
    go(10)
    assert len(shown) == 3  # it held again, then failed again: told at once
    assert "do not hold" in (tmp_path / "latest.md").read_text(encoding="utf-8")


def test_a_notification_that_could_not_be_shown_is_tried_again(tmp_path, monkeypatch):
    tries = []
    monkeypatch.setattr(cw, "toast", lambda *a: tries.append(1) and False)
    monkeypatch.setattr(cw, "openrouter_key", lambda: None)
    monkeypatch.setattr(cw, "evaluate", lambda *a, **k: [cw.Line("season cap", "WARN", "too much")])
    for d in (0, 1):
        cw.run(True, True, NOW + timedelta(days=d), tmp_path, tmp_path)
    assert len(tries) == 2


def test_the_archive_task_gets_the_warnings_and_is_never_blocked(monkeypatch):
    monkeypatch.setattr(cw, "run", lambda **k: [cw.Line("a", "WARN", "too much"), cw.Line("b", "OK", "fine")])
    assert bm.cost_warnings() == ["COST too much"]

    def boom(**k):
        raise ValueError("synthetic")

    monkeypatch.setattr(cw, "run", boom)
    assert bm.cost_warnings() == ["cost watch failed: ValueError: synthetic"]
