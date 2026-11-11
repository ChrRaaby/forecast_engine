"""EXP-013 arm C pilot tool (tools/exp013_armc_pilot.py): question picking, archive query counts, one call with a fake
Gemini response, brief checks and the summary. Offline: the Gemini call is replaced."""
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("exp013_armc_pilot", ROOT / "tools" / "exp013_armc_pilot.py")
pilot = importlib.util.module_from_spec(_spec)
sys.modules["exp013_armc_pilot"] = pilot  # dataclasses need the module registered
_spec.loader.exec_module(pilot)


def _record(qid, qtype, as_of, queries, criteria="", shadow=False):
    return {
        "as_of": as_of,
        "question": {"question_id": qid, "post_id": qid, "question_type": qtype, "question_text": f"Will {qid} happen?",
                     "resolution_criteria": criteria, "fine_print": ""},
        "research": {"calls": [{"provider": "gemini", "model": "gemini-3.5-flash-lite", "prompt": f"p{qid}",
                                "requested_at": as_of, "extra": {"search_queries": ["a"] * queries}}]},
    }, shadow


def _archive(tmp_path, recs):
    for i, (rec, shadow) in enumerate(recs):
        d = tmp_path / (f"run{i}-shadow-cheap" if shadow else f"run{i}")
        d.mkdir()
        (d / f"q{rec['question']['question_id']}.json").write_text(json.dumps(rec), encoding="utf-8")
    return tmp_path


def test_pick_mixes_types_and_prefers_linked_criteria(tmp_path):
    runs = _archive(tmp_path, [
        _record(1, "binary", "2026-11-10T10:00:00+00:00", 2),
        _record(2, "binary", "2026-11-10T11:00:00+00:00", 2, criteria="per https://example.org/data"),
        _record(3, "numeric", "2026-11-09T11:00:00+00:00", 1),
        _record(4, "multiple_choice", "2026-11-08T11:00:00+00:00", 1),
    ])
    qs = pilot.pick_questions(runs, 3)
    assert [q.question_id for q in qs] == [2, 4, 3]


def test_archive_counts_each_call_once_and_only_in_window(tmp_path):
    live, _ = _record(1, "binary", "2026-11-10T10:00:00+00:00", 3)
    shadow = (json.loads(json.dumps(live)), True)  # the shadow repeats the live research bundle
    old = _record(2, "binary", "2026-09-01T10:00:00+00:00", 5)
    runs = _archive(tmp_path, [(live, False), shadow, old])
    a = pilot.archive_query_counts(runs, datetime(2026, 11, 11, tzinfo=timezone.utc))
    assert a["total_queries"] == 3 and a["total_calls"] == 1 and a["live_questions"] == 1


def test_run_one_with_fake_response():
    q = pilot.snapshot_from_dict({"question_id": 7, "post_id": 7, "question_type": "binary", "question_text": "Will X?",
                                  "resolution_criteria": "per https://example.org", "fine_print": ""})
    text = ("Resolution source\nThe index stood at 4,512 (source: https://example.org/x, 2026-11-03).\n"
            "Turnout was 61% last time.\nNOT FOUND: latest poll")
    fake = {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP",
                            "groundingMetadata": {"webSearchQueries": ["x", "y", "z"],
                                                  "groundingChunks": [{"web": {"title": "t", "uri": "u"}}]}}],
            "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 1000, "thoughtsTokenCount": 1000},
            "modelVersion": "gemini-3.5-flash"}
    seen = {}

    def post(model, prompt, level):
        seen.update(model=model, prompt=prompt, level=level)
        return fake

    r = pilot.run_one(q, "gemini-3.5-flash", "2026-11-11", "low", post=post)
    assert r.ok and r.search_queries == 3 and r.grounding_chunks == 1
    assert abs(r.token_usd - (1000 * 1.5 + 2000 * 9.0) / 1e6) < 1e-12
    assert abs(r.grounding_usd_list - 0.042) < 1e-12
    assert "Today is 2026-11-11" in seen["prompt"] and "NOT FOUND" in seen["prompt"] and seen["level"] == "low"
    s = r.stats
    assert s["fact_lines"] == 2 and s["fact_lines_with_link"] == 1 and s["fact_lines_with_date"] == 1
    assert s["not_found"] == 1 and s["has_resolution_source_section"]
    rows = pilot.summarise([r], {"live_questions": 300, "days": 30})
    assert rows[0]["queries_per_call_mean"] == 3 and rows[0]["extra_queries_per_month_est"] == 900
    assert rows[0]["calls_over_cap_incl_grounding"] == 1 and rows[0]["calls_over_cap_tokens_only"] == 0
    lines = pilot.report_lines(rows, {"days": 30, "calls": {}, "queries": {}, "total_queries": 3600,
                                      "live_questions": 300}, [q], r.token_usd, r.total_usd_list)
    assert any("~4500 queries per 30 days (within 5000" in ln for ln in lines)


def test_failed_call_is_a_result_not_a_crash():
    q = pilot.snapshot_from_dict({"question_id": 8, "post_id": 8, "question_type": "binary", "question_text": "Will Y?"})

    def post(model, prompt, level):
        raise RuntimeError("Gemini HTTP 429: quota")

    r = pilot.run_one(q, "gemini-3.1-pro-preview", "2026-11-11", None, post=post)
    assert not r.ok and "429" in r.error
