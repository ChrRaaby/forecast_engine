"""B-28: as-of research layer. Synthetic fixtures only (no AskNews text in the repo); no network (conftest blocks it)."""
import asyncio
import json
import re
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from evals import asknews_archive
from evals.asknews_archive import ArchiveConfig, archive_search, search_params
from evals.asknews_budget import AskNewsBudget, BudgetConfig, BudgetExceeded, billing_period, live_credits_since
from evals.asof import LeakageError, assert_bundle_as_of, check_item, filter_items
from evals.asof_research import gather_research_as_of, screen_cached
from evals.leakage_screen import ScreenConfig, parse_verdict, screen_bundle, screen_prompt, summarize
from evals.research_cache import CacheError, ResearchCache, cache_key
from forecast_engine.clock import FixedClock, SystemClock
from forecast_engine.schema import LlmCall, QuestionSnapshot, ResearchBundle, ResearchItem

AS_OF = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)  # wall clock for the budget
Q = QuestionSnapshot(question_id=101, post_id=202, question_type="binary", question_text="Will the synthetic event happen?",
                     resolution_criteria="Resolves Yes if the synthetic event happens.")


def item(days_before: float | None, url: str = "https://news.example/a", title: str = "Synthetic headline") -> ResearchItem:
    published = None if days_before is None else AS_OF - timedelta(days=days_before)
    return ResearchItem(source="asknews_archive", text="Synthetic article text.", url=url, title=title, published_at=published)


def article(published: datetime | None, url: str = "https://news.example/a", title: str = "Synthetic headline"):
    return SimpleNamespace(summary="Synthetic summary.", article_url=url, eng_title=title, pub_date=published)


class FakeArchive:
    def __init__(self, articles=None, error: Exception | None = None):
        self.articles, self.error, self.calls = articles or [], error, []

    async def search_news(self, **params):
        self.calls.append(params)
        if self.error:
            raise self.error
        return SimpleNamespace(as_dicts=self.articles)


class FakeLlm:
    def __init__(self, output: str = '{"leak": false, "items": [], "reason": "nothing after the reference date"}',
                 error: str | None = None):
        self.output, self.error, self.prompts = output, error, []

    async def complete(self, *, model, prompt, purpose, max_tokens, reasoning_effort=None):
        self.prompts.append(prompt)
        return LlmCall(purpose=purpose, provider="openrouter", model=model, params={}, prompt=prompt, requested_at=AS_OF,
                       output=self.output, cost_usd=0.0001, error=self.error)


def budget(tmp_path, now=NOW, runs_dir=None, **cfg) -> AskNewsBudget:
    return AskNewsBudget(FixedClock(now), BudgetConfig(**cfg), ledger_path=tmp_path / "ledger.jsonl", runs_dir=runs_dir)


@pytest.fixture(autouse=True)
def _no_spacing(monkeypatch):
    monkeypatch.setattr(asknews_archive, "_last_call", [float("-inf")])


CFG = ArchiveConfig(min_interval_s=0)


def run(coro):
    return asyncio.run(coro)


# --- date guard (T2, T7) -------------------------------------------------------------------------------------------------

def test_guard_rejects_future_undated_naive_and_aggregators():
    naive = ResearchItem(source="s", text="t", published_at=datetime(2026, 1, 1))
    assert check_item(item(1), AS_OF) is None
    assert check_item(item(0), AS_OF) is None  # exactly at as_of is allowed
    assert check_item(item(-0.001), AS_OF) == "after_as_of"
    assert check_item(item(None), AS_OF) == "undated"
    assert check_item(naive, AS_OF) == "naive_date"
    assert check_item(item(1, url="https://www.metaculus.com/questions/1/"), AS_OF) == "forecast_aggregator"
    assert check_item(item(1, url="https://polymarket.com/event/x"), AS_OF) == "forecast_aggregator"
    assert check_item(item(1, url="https://notpolymarket.com.example/x"), AS_OF) is None


def test_filter_reports_reasons_without_text():
    kept, rejected = filter_items([item(1), item(-2), item(None)], AS_OF)
    assert len(kept) == 1
    assert [r.reason for r in rejected] == ["after_as_of", "undated"]
    assert all(not hasattr(r, "text") for r in rejected)


def test_leakage_injection_planted_future_document_is_blocked():
    """Protocol §7 harness validation: a planted future document must never pass."""
    bundle = ResearchBundle(as_of=AS_OF, providers=["x"], items=[item(3), item(-1, title="PLANTED")], calls=[])
    with pytest.raises(LeakageError):
        assert_bundle_as_of(bundle)


def test_guard_needs_aware_as_of():
    with pytest.raises(ValueError):
        check_item(item(1), datetime(2026, 1, 15))


# --- archive search ---------------------------------------------------------------------------------------------------

def test_archive_search_sends_hard_date_bounds(tmp_path):
    fake = FakeArchive([article(AS_OF - timedelta(days=2))])
    run(archive_search(Q, FixedClock(AS_OF), CFG, fake, budget(tmp_path)))
    p = fake.calls[0]
    assert p["historical"] is True
    assert p["end_timestamp"] == int(AS_OF.timestamp())
    assert p["start_timestamp"] == int(AS_OF.timestamp()) - 30 * 86400
    assert p["time_filter"] == "pub_date"
    assert p["hours_back"] is None


def test_end_timestamp_floors_fractional_seconds():
    as_of = AS_OF + timedelta(microseconds=900_000)
    assert search_params(Q, int(as_of.timestamp() // 1), CFG)["end_timestamp"] <= as_of.timestamp()


def test_archive_search_drops_future_and_undated_items(tmp_path):
    fake = FakeArchive([
        article(AS_OF - timedelta(days=5)),
        article(AS_OF + timedelta(hours=1), title="FUTURE"),
        article(None, title="UNDATED"),
        article(AS_OF - timedelta(days=1), url="https://manifold.markets/x"),
    ])
    bundle = run(archive_search(Q, FixedClock(AS_OF), CFG, fake, budget(tmp_path)))
    assert [i.title for i in bundle.items] == ["Synthetic headline"]
    extra = bundle.calls[0].extra
    assert extra["n_articles_returned"] == 4 and extra["n_kept"] == 1
    assert extra["rejected"] == {"after_as_of": 1, "undated": 1, "forecast_aggregator": 1}
    assert any("after as_of" in e for e in bundle.errors)  # the server ignoring end_timestamp is surfaced
    assert "Synthetic summary." not in json.dumps(extra["rejected_items"])
    assert_bundle_as_of(bundle)


def test_archive_search_refuses_system_clock(tmp_path):
    with pytest.raises(TypeError):
        run(archive_search(Q, SystemClock(), CFG, FakeArchive(), budget(tmp_path)))


def test_failed_search_is_recorded_and_still_charged(tmp_path):
    b = budget(tmp_path)
    bundle = run(archive_search(Q, FixedClock(AS_OF), CFG, FakeArchive(error=RuntimeError("boom")), b))
    assert bundle.calls[0].error == "RuntimeError: boom"
    assert bundle.items == [] and bundle.errors
    assert b.status().archive_used == 5


def test_config_hash_ignores_spacing_but_not_content():
    assert ArchiveConfig(min_interval_s=1).config_hash() == ArchiveConfig(min_interval_s=99).config_hash()
    assert ArchiveConfig(lookback_days=7).config_hash() != ArchiveConfig().config_hash()


# --- budget guard -----------------------------------------------------------------------------------------------------

def test_billing_period_anchored_on_the_30th():
    assert billing_period(date(2026, 10, 3), 30) == (date(2026, 9, 30), date(2026, 10, 30))
    assert billing_period(date(2026, 10, 29), 30) == (date(2026, 9, 30), date(2026, 10, 30))
    assert billing_period(date(2026, 10, 30), 30) == (date(2026, 10, 30), date(2026, 11, 30))
    assert billing_period(date(2027, 2, 28), 30) == (date(2027, 2, 28), date(2027, 3, 30))  # clamped to month end
    assert billing_period(date(2027, 1, 5), 30) == (date(2026, 12, 30), date(2027, 1, 30))
    assert billing_period(date(2026, 10, 3), 1) == (date(2026, 10, 1), date(2026, 11, 1))


def test_budget_default_leaves_300_credits_for_archive(tmp_path):
    st = budget(tmp_path).status()
    assert (st.limit, st.live_hold, st.remaining) == (500, 200, 300)


def test_budget_refuses_beyond_allowance_and_counts_reservations(tmp_path):
    b = budget(tmp_path, plan_credits=20, live_budget=5)
    for _ in range(3):
        b.reserve(5, question_id=1)
    assert b.status().remaining == 0
    with pytest.raises(BudgetExceeded):
        b.reserve(5)
    with pytest.raises(BudgetExceeded):
        b.check(1)


def test_budget_batch_precheck(tmp_path):
    b = budget(tmp_path)
    b.check(300)
    with pytest.raises(BudgetExceeded):
        b.check(305)


def test_overage_only_when_allowed(tmp_path):
    assert budget(tmp_path, allow_overage=True).status().remaining == 550


def test_budget_resets_with_the_period_but_counts_the_boundary_day(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    entries = [
        {"at": "2026-09-15T10:00:00+00:00", "credits": 100},  # previous period: not counted
        {"at": "2026-09-29T23:00:00+00:00", "credits": 10},  # boundary day: counted (reset time zone unknown)
        {"at": "2026-10-02T10:00:00+00:00", "credits": 5},
    ]
    ledger.write_text("".join(json.dumps(e) + "\n" for e in entries))
    assert budget(tmp_path).status().archive_used == 15
    later = budget(tmp_path, now=datetime(2026, 10, 31, tzinfo=timezone.utc)).status()
    assert later.period_start == date(2026, 10, 30) and later.archive_used == 0


def test_live_usage_from_run_records_shrinks_archive_budget(tmp_path):
    runs = tmp_path / "runs"

    def record(run, qid, at, n):
        path = runs / run / "data" / "forecasts" / f"{run}-tournament" / "forecasts.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps({"question_id": qid, "as_of": at, "asknews_calls": n}) + "\n")

    record("r1", 1, "2026-09-10T10:00:00+00:00", 1)  # previous period
    for i in range(250):
        record("r2", 100 + i, "2026-10-01T10:00:00+00:00", 1)
    record("r3", 9, "2026-10-02T10:00:00+00:00", 2)  # a rate-limited retry counts twice
    since = datetime(2026, 9, 29, tzinfo=timezone.utc)
    assert live_credits_since(runs, since) == 252
    st = budget(tmp_path, runs_dir=runs).status()
    assert st.live_used == 252 and st.live_hold == 0 and st.remaining == 500 - 252


def test_observed_usage_overrides_stale_counts(tmp_path):
    b = AskNewsBudget(FixedClock(NOW), BudgetConfig(), ledger_path=tmp_path / "l.jsonl", runs_dir=None, observed_used=250)
    assert b.status().remaining == 500 - 250 - 200


def test_corrupt_ledger_stops_the_batch(tmp_path):
    (tmp_path / "ledger.jsonl").write_text("{not json\n")
    with pytest.raises(ValueError):
        budget(tmp_path).status()


def test_search_is_refused_before_the_call_when_budget_is_spent(tmp_path):
    fake = FakeArchive([article(AS_OF - timedelta(days=1))])
    b = budget(tmp_path, plan_credits=200, live_budget=200)
    with pytest.raises(BudgetExceeded):
        run(archive_search(Q, FixedClock(AS_OF), CFG, fake, b))
    assert fake.calls == []


# --- research cache ---------------------------------------------------------------------------------------------------

def _bundle(*items: ResearchItem) -> ResearchBundle:
    call = LlmCall(purpose="research", provider="asknews", model="m", params={"n": 1}, prompt="p", requested_at=AS_OF,
                   extra={"rejected": {"after_as_of": 1}})
    return ResearchBundle(as_of=AS_OF, providers=["asknews_archive"], items=list(items) or [item(2)], calls=[call])


def test_cache_key_is_stable_and_sensitive():
    k = cache_key(1, AS_OF, "abc", "Q?")
    assert k == cache_key(1, AS_OF.astimezone(timezone(timedelta(hours=2))), "abc", "Q?")  # same instant, other zone
    assert len({k, cache_key(2, AS_OF, "abc", "Q?"), cache_key(1, AS_OF + timedelta(seconds=1), "abc", "Q?"),
                cache_key(1, AS_OF, "abd", "Q?"), cache_key(1, AS_OF, "abc", "Q? (edited later)")}) == 5


def test_cache_round_trip_and_write_once(tmp_path):
    cache = ResearchCache(tmp_path)
    b = _bundle()
    assert cache.get(1, AS_OF, "h", "Q?") is None
    key = cache.put(1, AS_OF, "h", "Q?", b)
    assert cache.get(1, AS_OF, "h", "Q?").to_dict() == b.to_dict()
    assert cache.put(1, AS_OF, "h", "Q?", b) == key  # same content: no-op
    with pytest.raises(CacheError):
        cache.put(1, AS_OF, "h", "Q?", _bundle(item(3, title="different")))


def test_cache_detects_tampering_and_rechecks_dates(tmp_path):
    cache = ResearchCache(tmp_path)
    key = cache.put(1, AS_OF, "h", "Q?", _bundle())
    path = cache.path(key)
    entry = json.loads(path.read_text())
    entry["bundle"]["items"][0]["text"] = "edited"
    path.write_text(json.dumps(entry))
    with pytest.raises(CacheError, match="content hash"):
        cache.get(1, AS_OF, "h", "Q?")
    # A future item with a matching hash (e.g. written by buggy code) is still caught on read.
    from evals.research_cache import content_hash
    entry["bundle"]["items"][0]["published_at"] = (AS_OF + timedelta(days=1)).isoformat()
    entry["content_sha256"] = content_hash(entry["bundle"])
    path.write_text(json.dumps(entry))
    with pytest.raises(LeakageError):
        cache.get(1, AS_OF, "h", "Q?")


def test_cache_refuses_leaky_or_failed_bundles(tmp_path):
    cache = ResearchCache(tmp_path)
    with pytest.raises(LeakageError):
        cache.put(1, AS_OF, "h", "Q?", _bundle(item(-1)))
    failed = _bundle()
    failed.calls[0].error = "boom"
    with pytest.raises(CacheError):
        cache.put(1, AS_OF, "h", "Q?", failed)


def test_gather_pays_once_then_hits_cache(tmp_path):
    fake = FakeArchive([article(AS_OF - timedelta(days=1))])
    b, cache = budget(tmp_path), ResearchCache(tmp_path / "cache")
    first = run(gather_research_as_of(Q, FixedClock(AS_OF), CFG, client=fake, budget=b, cache=cache))
    second = run(gather_research_as_of(Q, FixedClock(AS_OF), CFG, client=fake, budget=b, cache=cache))
    assert len(fake.calls) == 1 and b.status().archive_used == 5
    assert first.to_dict() == second.to_dict()


def test_gather_does_not_cache_failures(tmp_path):
    b, cache = budget(tmp_path), ResearchCache(tmp_path / "cache")
    run(gather_research_as_of(Q, FixedClock(AS_OF), CFG, client=FakeArchive(error=RuntimeError("x")), budget=b, cache=cache))
    assert cache.get(Q.question_id, AS_OF, CFG.config_hash(), Q.question_text) is None


# --- leakage screen (T3) ----------------------------------------------------------------------------------------------

def test_screen_model_must_predate_the_cutoff():
    ScreenConfig()  # default is allowed
    with pytest.raises(ValueError, match="unknown"):  # release dates come from a table, never from the caller (B-45 review)
        ScreenConfig(model="vendor/new-model")
    with pytest.raises(ValueError, match="cutoff"):
        ScreenConfig(model="google/gemini-3.5-flash-lite")


def test_screen_model_must_predate_the_bundle(tmp_path):
    early = ResearchBundle(as_of=datetime(2025, 6, 1, tzinfo=timezone.utc), providers=[], items=[], calls=[])
    with pytest.raises(ValueError):
        run(screen_bundle(Q, early, FakeLlm(), ScreenConfig()))


def test_screen_prompt_has_no_dates_after_as_of_and_no_resolution():
    bundle = _bundle(item(3), item(10))
    prompt = screen_prompt(Q, bundle)
    dates = re.findall(r"\b(20\d\d)-(\d\d)-(\d\d)\b", prompt)
    assert dates and all(date(*map(int, d)) <= AS_OF.date() for d in dates)
    assert "2026-01-15" in prompt
    assert not re.search(r"resolved (yes|no)|resolution value|community prediction", prompt, re.IGNORECASE)


def test_screen_verdicts():
    bundle = _bundle(item(1), item(2))
    clean = run(screen_bundle(Q, bundle, FakeLlm(), ScreenConfig()))
    assert clean.status == "clean" and not clean.excluded
    leak = run(screen_bundle(Q, bundle, FakeLlm('Sure: {"leak": true, "items": [1], "reason": "went on to win"}'),
                             ScreenConfig()))
    assert leak.status == "leak" and leak.flagged_items == [1] and leak.excluded


@pytest.mark.parametrize("output", ["no json here", '{"leak": "maybe"}', '{"leak": true, "items": [7]}',
                                    '{"leak": false, "items": [0]}', '{"leak": true, "items": [0]'])
def test_screen_fails_closed_on_bad_output(output):
    v = run(screen_bundle(Q, _bundle(item(1)), FakeLlm(output), ScreenConfig()))
    assert v.status == "screen_failed" and v.excluded


def test_screen_fails_closed_on_api_error():
    v = run(screen_bundle(Q, _bundle(), FakeLlm(output="", error="HTTP 500"), ScreenConfig()))
    assert v.status == "screen_failed"


def test_parse_verdict_accepts_wellformed():
    assert parse_verdict('{"leak": false, "items": [], "reason": "ok"}', 3) == ("clean", [], "ok")


def test_screen_verdict_is_cached_per_screen_config(tmp_path):
    cache, b = ResearchCache(tmp_path), _bundle()
    cache.put(Q.question_id, AS_OF, "h", Q.question_text, b)
    llm = FakeLlm()
    v1 = run(screen_cached(Q, b, "h", llm=llm, screen_cfg=ScreenConfig(), cache=cache))
    v2 = run(screen_cached(Q, b, "h", llm=llm, screen_cfg=ScreenConfig(), cache=cache))
    assert len(llm.prompts) == 1 and v1.status == v2.status == "clean"
    run(screen_cached(Q, b, "h", llm=llm, screen_cfg=ScreenConfig(prompt_version="v2"), cache=cache))
    assert len(llm.prompts) == 2


def test_failed_screens_are_not_cached(tmp_path):
    cache, b = ResearchCache(tmp_path), _bundle()
    cache.put(Q.question_id, AS_OF, "h", Q.question_text, b)
    run(screen_cached(Q, b, "h", llm=FakeLlm(error="x", output=""), screen_cfg=ScreenConfig(), cache=cache))
    llm = FakeLlm()
    assert run(screen_cached(Q, b, "h", llm=llm, screen_cfg=ScreenConfig(), cache=cache)).status == "clean"


def test_leak_rate_warning_above_ten_percent():
    assert summarize(["clean"] * 9 + ["leak"])["warning"] is None
    s = summarize(["clean"] * 8 + ["leak", "screen_failed"])
    assert s["excluded_rate"] == pytest.approx(0.2) and s["warning"]
    assert summarize([])["n"] == 0


def test_screen_prompt_v2_says_publication_dates_are_prechecked():
    """Regression for the 2026-10-03 false positive: the screen must judge the described event's date, not the article's."""
    from datetime import datetime, timezone

    from evals.leakage_screen import ScreenConfig, screen_prompt
    from forecast_engine.schema import QuestionSnapshot, ResearchBundle, ResearchItem

    as_of = datetime(2025, 11, 1, tzinfo=timezone.utc)
    q = QuestionSnapshot(question_id=1, post_id=None, question_type="binary", question_text="Will X?")
    b = ResearchBundle(as_of=as_of, providers=["asknews_archive"], calls=[],
                       items=[ResearchItem(source="t", text="Something happened.", published_at=as_of)])
    p = screen_prompt(q, b)
    assert "a publication date is never a reason to flag" in p
    assert "first find the date of the event" in p
    assert ScreenConfig().prompt_version == "leak-screen-v2"


def test_screen_verdict_is_never_stored_for_an_uncached_bundle(tmp_path):
    """B-45 review: a 'clean' verdict for an empty, failed search must not be reused later for the real articles."""
    cache = ResearchCache(tmp_path)
    empty = ResearchBundle(as_of=AS_OF, providers=[], items=[], calls=[])
    assert run(screen_cached(Q, empty, "h", llm=FakeLlm(), screen_cfg=ScreenConfig(), cache=cache)).status == "clean"
    real = _bundle(item(1))
    cache.put(Q.question_id, AS_OF, "h", Q.question_text, real)
    llm = FakeLlm('{"leak": true, "items": [0], "reason": "went on to"}')
    assert run(screen_cached(Q, real, "h", llm=llm, screen_cfg=ScreenConfig(), cache=cache)).status == "leak"
    assert len(llm.prompts) == 1  # the real bundle was actually screened


def test_stored_verdict_must_match_the_bundle(tmp_path):
    cache, b = ResearchCache(tmp_path), _bundle()
    key = cache.put(Q.question_id, AS_OF, "h", Q.question_text, b)
    with pytest.raises(CacheError):
        cache.put_screen(key, "s", "not-the-bundle-hash", {"status": "clean", "flagged_items": [], "reason": ""})
    from evals.research_cache import content_hash
    digest = content_hash(b.to_dict())
    cache.put_screen(key, "s", digest, {"status": "clean", "flagged_items": [], "reason": ""})
    with pytest.raises(CacheError):
        cache.get_screen(key, "s", "other-hash")
    path = cache.screen_path(key, "s")
    entry = json.loads(path.read_text())
    entry["verdict"]["status"] = "maybe"
    path.write_text(json.dumps(entry))
    with pytest.raises(CacheError, match="invalid stored status"):
        cache.get_screen(key, "s", digest)


def test_post_as_of_rejections_keep_no_title_or_url(tmp_path):
    """B-45 review: headlines of articles dated after as_of must not be stored inside an 'as of' bundle."""
    late = article(AS_OF + timedelta(days=3), title="Fed cuts rates again on Dec 10")
    fake = FakeArchive([article(AS_OF - timedelta(days=1)), late])
    bundle = run(archive_search(Q, FixedClock(AS_OF), CFG, fake, budget(tmp_path)))
    blob = json.dumps(bundle.to_dict())
    assert "Dec 10" not in blob and "Fed cuts rates again" not in blob
    assert bundle.calls[0].extra["rejected"]["after_as_of"] == 1


def test_first_day_of_a_new_period_also_respects_the_old_period(tmp_path):
    """B-45 review: if AskNews resets later than UTC midnight, spend on the 30th may still be billed to the old period."""
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps({"at": "2026-10-20T10:00:00+00:00", "credits": 300}) + "\n")
    early = budget(tmp_path, now=datetime(2026, 10, 30, 2, 0, tzinfo=timezone.utc)).status()
    assert early.remaining == 0 and early.period_start == date(2026, 9, 30)
    later = budget(tmp_path, now=datetime(2026, 10, 31, 2, 0, tzinfo=timezone.utc)).status()
    assert later.period_start == date(2026, 10, 30) and later.remaining == 300


def test_reserve_takes_a_process_lock_and_breaks_stale_ones(tmp_path):
    import os as _os
    import time as _t

    b = budget(tmp_path)
    lock = tmp_path / "ledger.jsonl.lock"
    lock.write_text("")
    old = _t.time() - 600
    _os.utime(lock, (old, old))  # a lock left by a crashed process
    b.reserve(5)
    assert not lock.exists() and b.status().archive_used == 5
