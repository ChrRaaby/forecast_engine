"""B-51a research date flags: synthetic text only; flags are stored, the prompt is untouched, the checker never raises."""
import asyncio
from datetime import datetime, timezone

from evals.research_cache import bundle_from_dict
from forecast_engine.clock import FixedClock
from forecast_engine.config import DEFAULT_CONFIG
from forecast_engine.core import forecast
from forecast_engine.research_checks import check_bundle
from forecast_engine.schema import ResearchBundle, ResearchItem
from forecast_engine.core import ForecastFailed
from tests.test_core import FakeLlm, Q, _bundle
from tests.test_core import AS_OF as CORE_AS_OF

AS_OF = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


def _b(*texts, published_at=None, as_of=AS_OF):
    items = [ResearchItem(source="asknews_latest", text=t, published_at=published_at) for t in texts]
    return ResearchBundle(as_of=as_of, providers=[], items=items, calls=[])


def _kinds(bundle):
    return [f["flag"] for f in check_bundle(bundle)]


def test_wrong_weekday_is_flagged():
    flags = check_bundle(_b("The vote is on Friday, October 4, 2026 in Geneva."))  # 2026-10-04 is a Sunday
    assert [f["flag"] for f in flags] == ["weekday_mismatch"]
    assert flags[0]["detail"] == "Friday, October 4, 2026" and flags[0]["item_index"] == 0 and flags[0]["source"] == "asknews_latest"


def test_correct_weekday_is_not_flagged():
    assert _kinds(_b("It happened on Sunday, October 4, 2026.", "Sun, Oct 4, 2026", "Sunday 4 October 2026")) == []


def test_missing_year_uses_as_of_year():
    assert _kinds(_b("Fri, Oct 4 was the deadline.")) == ["weekday_mismatch"]  # 2026-10-04 is a Sunday
    assert _kinds(_b("Sun, Oct 4 was the deadline.")) == []


def test_missing_year_far_ahead_rolls_back_a_year():
    # Dec 2026 is 6 weeks after as_of, so it stays 2026; Dec 2025 would be wrong for "Thu, Dec 25"
    assert _kinds(_b("Fri, Dec 25 is a holiday.")) == ["future_weekday_date"]  # Friday, 2026-12-25: right weekday, after as_of
    assert _kinds(_b("Thu, Dec 25 was a holiday.", as_of=datetime(2026, 3, 1, tzinfo=timezone.utc))) == []  # rolls back to 2025: a Thursday


def test_future_weekday_date_flagged_plain_future_date_not():
    assert _kinds(_b("The match is on Saturday, November 14, 2026.")) == ["future_weekday_date"]
    assert _kinds(_b("The match is on November 14, 2026.")) == []


def test_published_after_as_of():
    assert _kinds(_b("x", published_at=datetime(2026, 11, 8, tzinfo=timezone.utc))) == ["published_after_as_of"]
    assert _kinds(_b("x", published_at=datetime(2026, 11, 8))) == ["published_after_as_of"]  # naive = UTC
    assert _kinds(_b("x", published_at=datetime(2026, 11, 6, tzinfo=timezone.utc))) == []


def test_stale_season_year():
    for t in ("2024/25 marketing year", "MY 2024/25", "crop year 2024-25", "FY2024 revenue", "in fiscal year 2024"):
        assert _kinds(_b(t)) == ["stale_season_year"], t
    for t in ("2025/26 marketing year", "MY 2026/27", "FY2026 guidance", "fiscal year 2025", "the 2024/25 season", "the 2024/25 film"):
        assert _kinds(_b(t)) == [], t


def test_flags_ignore_source_and_index_items():
    b = ResearchBundle(as_of=AS_OF, providers=[], calls=[], items=[
        ResearchItem(source="gemini_grounded", text="fine"),
        ResearchItem(source="followup", text="Friday, October 4, 2026")])
    f = check_bundle(b)
    assert [(x["item_index"], x["source"]) for x in f] == [(1, "followup")]


def test_never_raises_on_garbage():
    huge = "Friday, October 4, 2026 " * 200_000
    junk = ResearchBundle(as_of=AS_OF, providers=[], calls=[], items=[
        ResearchItem(source="x", text=None), ResearchItem(source=None, text=123), ResearchItem(source="x", text="☃\x00💩 Fri, Feb 31"),
        ResearchItem(source="x", text="Friday, October 99, 2026 Mon, Foo 4"), ResearchItem(source="x", text=huge)])
    assert isinstance(check_bundle(junk), list)
    assert [f["flag"] for f in check_bundle(ResearchBundle(as_of=None, providers=[], calls=[], items=None))] == ["check_error"]
    assert check_bundle(None)[0]["flag"] == "check_error"


def test_flags_do_not_change_prompt_and_reach_the_record():
    def run(text):
        b = ResearchBundle(as_of=CORE_AS_OF, providers=[], calls=[], items=[ResearchItem(source="asknews_latest", text=text)])
        llm = FakeLlm({})
        try:
            rec = asyncio.run(forecast(Q, FixedClock(CORE_AS_OF), b, DEFAULT_CONFIG, llm))
        except ForecastFailed as e:
            rec = e.record
        return b, llm.prompts, rec

    bad = "Friday, October 4, 2026"
    b, prompts, rec = run(bad)
    assert b.quality_flags and rec.to_dict()["research"]["quality_flags"] == b.quality_flags
    # same text through as_prompt_text with the flags cleared: identical to what the models were given
    cleared = ResearchBundle(as_of=b.as_of, providers=[], calls=[], items=b.items)
    assert b.as_prompt_text() == cleared.as_prompt_text() and all(bad in p for p in prompts)
    assert "quality_flags" not in b.as_prompt_text() and "weekday_mismatch" not in "".join(prompts)


def test_old_records_without_the_field_load():
    d = _bundle().to_dict()
    d.pop("quality_flags")
    assert bundle_from_dict(d).quality_flags == []
