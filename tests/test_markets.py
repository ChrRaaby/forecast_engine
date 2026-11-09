"""EXP-012 step 1a: market clients, as-of price rule, matcher, and leakage checks LK1-LK5 (all network-free)."""
from __future__ import annotations

import ast
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from evals import markets as mk
from tools import market_match as mm

ROOT = Path(__file__).resolve().parents[1]
AS_OF = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)


def ts(dt: datetime) -> int:
    return int(dt.timestamp())


class FakeHttp:
    """Stands in for mk.Http: `handler(url, params)` returns the canned JSON."""

    def __init__(self, handler):
        self.handler = handler
        self.calls: list[tuple[str, dict | None]] = []

    def get(self, url, params=None):
        self.calls.append((url, params))
        return self.handler(url, params)


def poly_market(mid="1", question="Will the Fed cut rates in December 2026?", created=AS_OF - timedelta(days=30), closed_time=None, **kw):
    m = {"id": mid, "question": question, "description": "Resolves Yes if the FOMC cuts at its December 2026 meeting.",
         "endDate": "2026-12-16T00:00:00Z", "createdAt": created.isoformat().replace("+00:00", "Z"),
         "outcomes": '["Yes", "No"]', "clobTokenIds": '["tok-yes", "tok-no"]', "closed": closed_time is not None}
    if closed_time:
        m["closedTime"] = closed_time
    m.update(kw)
    return m


# ---------- LK1: no price from after the forecast ----------

def test_lk1_pick_point_ignores_planted_future_point():
    pts = [(AS_OF - timedelta(hours=5), 0.30), (AS_OF - timedelta(minutes=1), 0.35), (AS_OF + timedelta(minutes=1), 0.99)]
    t, p = mk.pick_point(pts, AS_OF)
    assert p == 0.35 and t == AS_OF - timedelta(minutes=1)


def test_lk1_point_exactly_at_as_of_is_allowed_but_not_after():
    assert mk.pick_point([(AS_OF, 0.4), (AS_OF + timedelta(seconds=1), 0.99)], AS_OF) == (AS_OF, 0.4)


def test_price_older_than_7_days_is_dead():
    assert mk.pick_point([(AS_OF - timedelta(days=7, seconds=1), 0.4)], AS_OF) is None
    assert mk.pick_point([(AS_OF - timedelta(days=7), 0.4)], AS_OF) is not None
    assert mk.pick_point([], AS_OF) is None
    assert mk.pick_point([(AS_OF + timedelta(minutes=1), 0.99)], AS_OF) is None  # only a future point: no price


def _poly_market_obj():
    return mk.market_from_polymarket(poly_market())


def test_lk1_polymarket_history_with_planted_99_after_as_of():
    hist = [{"t": ts(AS_OF) - 3600, "p": 0.31}, {"t": ts(AS_OF) - 60, "p": 0.33}, {"t": ts(AS_OF) + 60, "p": 0.99}]
    seen = {}

    def handler(url, params):
        seen.update(params)
        return {"history": hist}

    pp = mk.price_as_of(_poly_market_obj(), AS_OF, FakeHttp(handler))
    assert pp.p == 0.33 and pp.age == timedelta(seconds=60) and pp.fidelity_min == 10
    assert seen["endTs"] == ts(AS_OF) and seen["market"] == "tok-yes"  # the request itself stops at as_of


def test_lk1_polymarket_falls_back_to_coarser_history_and_records_it():
    def handler(url, params):
        return {"history": []} if params["fidelity"] < 720 else {"history": [{"t": ts(AS_OF) - 7200, "p": 0.2}]}

    pp = mk.price_as_of(_poly_market_obj(), AS_OF, FakeHttp(handler))
    assert pp.fidelity_min == 720 and pp.p == 0.2 and pp.age == timedelta(hours=2)


def test_lk1_kalshi_trades_after_as_of_are_ignored():
    m = mk.market_from_kalshi({"ticker": "KXFED-26DEC-T3", "title": "Fed cut?", "created_time": "2026-09-01T00:00:00Z",
                              "close_time": "2026-12-16T00:00:00Z", "market_type": "binary"})
    trades = [{"created_time": (AS_OF + timedelta(minutes=1)).isoformat(), "yes_price_dollars": "0.9900"},
              {"created_time": (AS_OF - timedelta(minutes=30)).isoformat(), "yes_price_dollars": "0.4100"}]
    pp = mk.price_as_of(m, AS_OF, FakeHttp(lambda u, p: {"trades": trades}))
    assert pp.p == 0.41 and pp.age == timedelta(minutes=30)


def test_lk1_manifold_bet_after_as_of_is_ignored():
    m = mk.market_from_manifold({"id": "abc", "outcomeType": "BINARY", "question": "q", "createdTime": ts(AS_OF) * 1000 - 10**9})
    bets = [{"createdTime": ts(AS_OF) * 1000 + 60_000, "probAfter": 0.99}, {"createdTime": ts(AS_OF) * 1000 - 60_000, "probAfter": 0.55}]
    pp = mk.price_as_of(m, AS_OF, FakeHttp(lambda u, p: bets))
    assert pp.p == 0.55


def test_polarity_flip_and_clamp():
    assert mk.flip_and_clamp(0.30, "same") == 0.30
    assert mk.flip_and_clamp(0.30, "inverted") == pytest.approx(0.70)
    assert mk.flip_and_clamp(0.0, "same") == 0.01 and mk.flip_and_clamp(1.0, "same") == 0.99
    assert mk.flip_and_clamp(0.0, "inverted") == 0.99
    with pytest.raises(ValueError):
        mk.flip_and_clamp(0.5, "unknown")


def test_several_markets_are_averaged_on_the_log_odds_scale():
    assert mk.mean_logit_price([0.2, 0.8]) == pytest.approx(0.5)
    assert mk.mean_logit_price([0.7]) == pytest.approx(0.7)
    assert mk.mean_logit_price([0.9, 0.5]) == pytest.approx(0.75)  # logit 2.197/2 = 1.0986 -> 0.75


# ---------- LK2: no market created after the forecast ----------

def test_lk2_open_at_drops_late_and_already_closed_markets():
    ok = mk.market_from_polymarket(poly_market("ok"))
    late = mk.market_from_polymarket(poly_market("late", created=AS_OF + timedelta(minutes=1)))
    gone = mk.market_from_polymarket(poly_market("gone", closed_time="2026-10-09 00:00:00+00"))
    still = mk.market_from_polymarket(poly_market("still", closed_time="2026-11-01 00:00:00+00"))
    assert mk.open_at(ok, AS_OF) and mk.open_at(still, AS_OF)
    assert not mk.open_at(late, AS_OF)
    assert not mk.open_at(gone, AS_OF)


def test_lk2_candidates_drops_planted_late_market_on_every_venue():
    q = "Will the Fed cut rates in December 2026?"
    late = (AS_OF + timedelta(hours=1))

    def km(t, created, close):
        return {"ticker": t, "title": q, "created_time": created, "close_time": close, "market_type": "binary"}

    def handler(url, params):
        if "public-search" in url:
            return {"events": [{"title": "Fed", "markets": [poly_market("p-ok"), poly_market("p-late", created=late)]}]}
        if "search-markets" in url:
            base = {"outcomeType": "BINARY", "question": q}
            return [{"id": "m-ok", "createdTime": ts(AS_OF - timedelta(days=3)) * 1000, **base},
                    {"id": "m-late", "createdTime": ts(late) * 1000, **base}]
        if url.endswith("/events"):
            assert params["series_ticker"] == "KXFED"
            return {"events": [{"title": "Fed", "markets": [
                km("K-OK", "2026-09-01T00:00:00Z", "2026-12-16T00:00:00Z"),
                km("K-LATE", late.isoformat(), "2026-12-16T00:00:00Z"),
                km("K-GONE", "2026-09-01T00:00:00Z", "2026-10-09T00:00:00Z")]}], "cursor": ""}
        return {"textDescription": "rules"}  # manifold market detail

    series = [{"ticker": "KXFED", "title": "Fed rate cut December", "category": "Economics", "tags": []},
              {"ticker": "KXNBA", "title": "NBA champion", "category": "Sports", "tags": []}]
    got = mk.candidates(q, AS_OF, FakeHttp(handler), series, max_n=10)
    ids = {m.market_id for m in got}
    assert ids == {"p-ok", "m-ok", "K-OK"}
    assert all(m.created <= AS_OF for m in got)


def test_candidates_requires_aware_as_of():
    with pytest.raises(ValueError):
        mk.candidates("q", datetime(2026, 10, 10), FakeHttp(lambda u, p: None), [])


# ---------- LK3: the judge never sees results, prices or status ----------

PLANTED = ["PLANTED_RESOLVED_YES", "PLANTED_FINAL_PRICE", "PLANTED_CLOSED_STATUS", "PLANTED_RESOLUTION_NOTE", "PLANTED_VOLUME"]


def _fixture_candidates():
    poly = mk.market_from_polymarket(
        poly_market("p1", outcomePrices='["1", "0"]', umaResolutionStatus="PLANTED_RESOLVED_YES", lastTradePrice="PLANTED_FINAL_PRICE",
                    volume="PLANTED_VOLUME", resolutionSource="PLANTED_RESOLUTION_NOTE", closed_time="2026-11-01 00:00:00+00",
                    acceptingOrders="PLANTED_CLOSED_STATUS"))
    kal = mk.market_from_kalshi({
        "ticker": "K1", "event_ticker": "KXFED", "event_title": "Fed decision", "title": "Fed cut in Dec?", "yes_sub_title": "Cut",
        "rules_primary": "Resolves Yes if cut.", "created_time": "2026-09-01T00:00:00Z", "close_time": "2026-11-01T00:00:00Z",
        "latest_expiration_time": "2026-12-20T00:00:00Z", "market_type": "binary", "result": "PLANTED_RESOLVED_YES",
        "status": "PLANTED_CLOSED_STATUS", "settlement_value_dollars": "PLANTED_FINAL_PRICE", "volume_fp": "PLANTED_VOLUME",
        "last_price_dollars": "PLANTED_FINAL_PRICE"})
    man = mk.market_from_manifold({
        "id": "mm1", "outcomeType": "BINARY", "question": "Fed cut in Dec?", "createdTime": ts(AS_OF) * 1000 - 10**9,
        "closeTime": ts(AS_OF) * 1000 + 10**9, "isResolved": True, "resolution": "PLANTED_RESOLVED_YES", "probability": "PLANTED_FINAL_PRICE",
        "volume": "PLANTED_VOLUME", "textDescription": "Resolves YES if the Fed cuts."})
    return [poly, kal, man]


def test_lk3_judge_prompt_contains_none_of_the_planted_post_hoc_fields():
    q = {"question_text": "Will the Fed cut in December 2026?", "resolution_criteria": "Yes if cut.", "fine_print": "",
         "scheduled_resolution_time": "2026-12-16T00:00:00+00:00"}
    prompt = mm.judge_prompt(q, _fixture_candidates())
    for marker in PLANTED:
        assert marker not in prompt
    assert "Fed decision" in prompt and "Resolves Yes if cut." in prompt and "2026-12-20" in prompt  # the allowed fields are there
    assert "2026-11-01" not in prompt  # Kalshi's actual (early) close time never shown


def test_lk3_market_record_has_no_price_or_status_fields():
    fields = set(mk.Market.__dataclass_fields__)
    assert fields == {"venue", "market_id", "title", "rules", "end", "created", "closed_at", "ref", "url"}


def test_lk3_kalshi_index_keeps_no_result_or_price_fields():
    row = mk.kalshi_compact({"title": "Fed"}, {"ticker": "T", "result": "yes", "settlement_value_dollars": "1.0", "last_price_dollars": "1",
                                              "volume_fp": "9", "status": "finalized", "title": "m", "created_time": "x"})
    assert set(row) == {"ticker", "event_ticker", "title", "yes_sub_title", "rules_primary", "rules_secondary", "created_time", "close_time",
                        "latest_expiration_time", "expiration_time", "market_type", "event_title"}


def test_lk3_manifold_end_date_hidden_when_resolved_early():
    m = mk.market_from_manifold({"id": "x", "outcomeType": "BINARY", "question": "q", "createdTime": 1_700_000_000_000,
                                "closeTime": 1_726_682_510_000, "resolutionTime": 1_726_682_510_142, "isResolved": True})
    assert m.end is None


# ---------- LK4: the matcher never reads results; the scorer needs a committed hash ----------

def test_lk4_matcher_code_has_no_route_to_the_results_file_or_scoring():
    for rel in ("evals/markets.py", "tools/market_match.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        for bad in ("outcomes.jsonl", "evals.outcomes", "load_outcomes", "data/outcomes", "resolved_binary", "evals.scoring", "evals.report"):
            assert bad not in src, f"{rel} mentions {bad}"
        mods = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                mods |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                mods.add(node.module or "")
                mods |= {f"{node.module}.{a.name}" for a in node.names}
        assert not {m for m in mods if "outcomes" in m or m.endswith("scoring") or m.endswith("report")}, rel


def _git(cwd, *a):
    subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True)


def test_lk4_require_frozen_refuses_until_hash_committed(tmp_path):
    repo = tmp_path / "data"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")
    table = repo / "matches.jsonl"
    table.write_text('{"a": 1}\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="not frozen"):
        mk.require_frozen(table)
    freeze = repo / "matches.jsonl.sha256"
    freeze.write_text(mk.file_sha256(table) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="committed"):
        mk.require_frozen(table)  # hash written but not committed
    _git(repo, "add", "matches.jsonl", "matches.jsonl.sha256")
    _git(repo, "commit", "-q", "-m", "freeze")
    assert mk.require_frozen(table) == mk.file_sha256(table)
    table.write_text('{"a": 2}\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="changed after"):
        mk.require_frozen(table)


# ---------- LK5: real-data audit ----------

def test_lk5_audit_flags_price_or_market_from_after_as_of():
    good = {"record": "r1", "as_of": mk.iso(AS_OF), "candidates": [{"venue": "polymarket", "market_id": "1", "price_t": mk.iso(AS_OF - timedelta(hours=1)),
                                                                     "created": mk.iso(AS_OF - timedelta(days=2))}]}
    assert mk.audit_rows([good]) == []
    late_price = json.loads(json.dumps(good))
    late_price["candidates"][0]["price_t"] = mk.iso(AS_OF + timedelta(seconds=1))
    late_market = json.loads(json.dumps(good))
    late_market["candidates"][0]["created"] = mk.iso(AS_OF + timedelta(seconds=1))
    assert len(mk.audit_rows([late_price])) == 1 and len(mk.audit_rows([late_market])) >= 1


# ---------- parsing, query building, records ----------

def test_parse_time_formats():
    assert mk.parse_time("2026-01-29 00:18:06+00") == datetime(2026, 1, 29, 0, 18, 6, tzinfo=timezone.utc)
    assert mk.parse_time("2025-12-16T17:20:14.84176Z").year == 2025
    assert mk.parse_time(1_726_682_510_142) == datetime.fromtimestamp(1_726_682_510.142, tz=timezone.utc)
    assert mk.parse_time(1_766_188_821) == datetime.fromtimestamp(1_766_188_821, tz=timezone.utc)
    assert mk.parse_time(None) is None


def test_search_queries_are_short_and_distinct():
    qs = mk.search_queries("Will the Federal Reserve cut interest rates by at least 25 basis points at its December 2026 meeting?")
    assert qs[0].startswith("Will the Federal Reserve") and len(qs) == 3 and len(set(qs)) == 3
    assert "federal" in qs[1] and len(qs[2].split()) <= 4


def test_parse_judge_needs_every_candidate_and_valid_labels():
    ok = '{"matches": [{"n": 1, "grade": "A", "polarity": "inverted", "why": "x"}, {"n": 2, "grade": "none", "polarity": "same", "why": "y"}]}'
    assert [v["grade"] for v in mm.parse_judge("noise " + ok, 2)] == ["A", "none"]
    assert mm.parse_judge(ok, 3) is None  # candidate 3 missing
    assert mm.parse_judge('{"matches": [{"n": 1, "grade": "C", "polarity": "same"}]}', 1) is None
    assert mm.parse_judge("not json", 1) is None


def test_tournament_label():
    assert mm.tournament_of({"tournaments": ["minibench"]}) == "MiniBench"
    assert mm.tournament_of({"tournaments": ["fall-futureeval-2026"]}) == "FutureEval"
    assert mm.tournament_of({"tournaments": []}) == "other"


def _rec(qid, as_of, published=True, qtype="binary", agg=0.4):
    return {"question": {"question_id": qid, "post_id": qid, "question_type": qtype, "question_text": "q", "tournaments": ["minibench"]},
            "as_of": as_of, "aggregate": agg if qtype == "binary" else [0.1], "published": published, "config_version": "v"}


def test_eligible_records_filters(tmp_path):
    def put(run, name, rec):
        d = tmp_path / "1" / "data" / "forecasts" / run
        d.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(json.dumps(rec), encoding="utf-8")

    put("20261008T000000Z-tournament", "q1.json", _rec(1, "2026-10-08T00:00:00+00:00"))
    put("20261008T000000Z-tournament", "q2.json", _rec(2, "2026-10-08T00:00:00+00:00", qtype="numeric"))
    put("20261008T000000Z-tournament", "q3.json", _rec(3, "2026-10-08T00:00:00+00:00", published=False))
    put("20261008T000000Z-tournament-shadow-cheap", "q1.json", _rec(1, "2026-10-08T00:00:00+00:00"))
    put("20261001T000000Z-test_questions", "q4.json", _rec(4, "2026-10-08T00:00:00+00:00"))
    put("20260930T000000Z-tournament", "q5.json", _rec(5, "2026-09-30T23:00:00+00:00"))
    put("20261009T000000Z-wide", "q6.json", _rec(6, "2026-10-09T00:00:00+00:00"))
    got = mm.eligible_records(tmp_path)
    assert sorted(r["question"]["question_id"] for _, r in got) == [1, 6]


def test_report_and_audit_render_on_synthetic_rows():
    def cand(grade, price, venue="polymarket", real=True):
        return {"venue": venue, "market_id": "1", "real_money": real, "title": "t", "rules": "r", "end": None,
                "created": mk.iso(AS_OF - timedelta(days=1)), "closed_now": False, "url": "", "grade": grade, "polarity": "same", "why": "w",
                "price_raw": price, "price": price, "price_t": mk.iso(AS_OF - timedelta(hours=1)) if price else None,
                "price_age_h": 1.0 if price else None, "fidelity_min": 10 if price else None, "n_points": 5, "gap_min": 10.0}

    rows = []
    for i, (g, p) in enumerate([("A", 0.6), ("A", None), ("B", 0.3), ("none", None)]):
        r = {"record": f"r{i}", "question_id": i, "as_of": mk.iso(AS_OF), "tournament": "FutureEval" if i % 2 else "MiniBench",
             "config_version": "v", "bot_p": 0.5, "question_text": "q", "candidates": [cand(g, p)], "judge_error": None, "n_candidates": 1}
        mm.summarise_row(r)
        rows.append(r)
    rows.append({"record": "r9", "question_id": 9, "as_of": mk.iso(AS_OF), "tournament": "other", "config_version": "v", "bot_p": 0.5,
                 "question_text": "q", "candidates": [], "judge_error": None, "n_candidates": 0, "has_A_real": False, "has_A_real_priced": False,
                 "market_p": None})
    rep = mm.build_report(rows, {"model": "m", "cost": 0.01, "calls": 4, "cached": 0, "live_requests": 1, "cache_hits": 0})
    assert "Records scanned: **5**" in rep and "violations" in rep and "Futility rule triggers" in rep
    assert "0 violations" in rep
    assert "Verdict" in mm.build_audit(rows)
    assert rows[0]["market_p"] == 0.6 and rows[1]["market_p"] is None and rows[2]["has_A_real"] is False


def test_ledger_row_is_added_after_the_last_table_row(tmp_path):
    led = tmp_path / "ledger.md"
    led.write_text("# L\n\n| Date | E | Run | What | Calls | Cost | Cumulative | Left |\n|---|---|---|---|---|---|---|---|\n"
                   "| 2026-11-07 | EXP-002 | r | w | 5 | $1.000 | $4.000 | $176.00 |\n\n## Other\n\n| 2026-11-07 | x | $9 |\n", encoding="utf-8")
    mm.append_ledger("20261110T101500Z", 7, 0.25, "market matcher", ledger=led)
    lines = led.read_text(encoding="utf-8").splitlines()
    i = next(n for n, ln in enumerate(lines) if "EXP-012" in ln)
    assert lines[i - 1].startswith("| 2026-11-07 | EXP-002") and "$1.250" in lines[i] and "$0.250" in lines[i]
