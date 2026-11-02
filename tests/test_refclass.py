"""B-56: reference-class library (evals/refclass.py). Synthetic questions only: made-up titles, ids, dates and outcomes.

The leakage guards (protocol T1/T6) come first: nothing resolved at or after as_of, nothing from the question's own post, the
actual resolve time not the scheduled one, and no time-zone-less dates.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from evals import refclass as rc
from evals import validate_harness as vh
from forecast_engine.schema import QuestionSnapshot

ROOT = Path(__file__).resolve().parents[1]
AS_OF = datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc)
SEC = timedelta(seconds=1)


def row(qid, resolved_at, outcome=1, *, post=None, title="Will the synthetic gadget ship?", tournaments=("synth-cup",),
        categories=(), source=rc.SOURCE_LIVE):
    return rc.RefQuestion(question_id=qid, post_id=post if post is not None else qid + 10_000, title=title, source=source,
                          tournaments=tuple(tournaments), categories=tuple(categories), tags=(), open_time=None,
                          resolved_at=resolved_at, outcome=outcome)


def target(qid=1, post=99, text="Will the synthetic gadget ship?"):
    return QuestionSnapshot(question_id=qid, post_id=post, question_type="binary", question_text=text)


def ids(similar_list):
    return [s.question.question_id for s in similar_list]


# ---------------------------------------------------------------- leakage guards
def test_resolved_one_second_after_as_of_is_never_returned():
    rows = [row(10, AS_OF - SEC), row(11, AS_OF + SEC)]
    assert ids(rc.similar(rows, target(), AS_OF)) == [10]
    r = rc.yes_rate(rows, AS_OF)
    assert (r.n, r.yes) == (1, 1)
    assert [q.question_id for q in rc.available(rows, AS_OF)] == [10]


def test_resolved_exactly_at_as_of_is_excluded():
    rows = [row(10, AS_OF - SEC, 0), row(11, AS_OF, 1)]
    assert ids(rc.similar(rows, target(), AS_OF)) == [10]
    assert rc.yes_rate(rows, AS_OF).n == 1
    assert rc.yes_rate(rows, AS_OF + SEC).n == 2  # one second later it is known


def test_the_question_itself_and_its_sibling_sub_questions_are_excluded():
    rows = [
        row(1, AS_OF - timedelta(days=3), post=99),  # the target question itself (should never happen, but must not leak)
        row(2, AS_OF - timedelta(days=2), post=99, title="Will the synthetic gadget ship? (Option B)"),  # sibling sub-question
        row(3, AS_OF - timedelta(days=1), post=55, title="Will the synthetic gizmo ship?"),
    ]
    assert ids(rc.similar(rows, target(qid=1, post=99), AS_OF)) == [3]
    assert rc.yes_rate(rows, AS_OF, exclude=target(qid=1, post=99)).n == 1
    assert rc.yes_rate(rows, AS_OF).n == 3  # without `exclude`, past siblings count in the plain Yes-rate


def test_question_without_post_id_excludes_only_itself():
    rows = [row(1, AS_OF - SEC), row(2, AS_OF - SEC)]
    assert ids(rc.similar(rows, target(qid=1, post=None), AS_OF)) == [2]


def _write_live(base: Path, questions: dict, *, snapshots: dict | None = None):
    """questions: qid -> outcomes.jsonl row fields (status, resolution, actual/scheduled times). Writes one record per question."""
    runs = base / "runs" / "2025-run"
    runs.mkdir(parents=True, exist_ok=True)
    lines = []
    for qid, out in questions.items():
        snap = dict(question_id=qid, post_id=qid + 500, question_type="binary", question_text=f"Will synthetic event {qid} occur?",
                    tournaments=["synth-cup"], open_time=(AS_OF - timedelta(days=60)).isoformat())
        snap.update((snapshots or {}).get(qid, {}))
        (runs / f"q{qid}.json").write_text(json.dumps({"question": snap, "as_of": "2025-01-01T00:00:00+00:00"}), encoding="utf-8")
        if out is not None:
            lines.append(json.dumps({"question_id": qid, "post_id": qid + 500, "type": snap["question_type"], **out}))
    (base / "outcomes").mkdir(exist_ok=True)
    (base / "outcomes" / "outcomes.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_filter_uses_actual_resolve_time_even_when_scheduled_close_is_earlier(tmp_path):
    early = (AS_OF - timedelta(days=30)).isoformat()
    _write_live(tmp_path, {
        # scheduled to resolve a month before as_of, actually resolved a day after it: unknown at as_of
        21: {"status": "resolved", "resolution": "yes", "actual_resolve_time": (AS_OF + timedelta(days=1)).isoformat(),
             "scheduled_resolve_time": early, "scheduled_close_time": early},
        # scheduled after as_of, actually resolved before it: known at as_of
        22: {"status": "resolved", "resolution": "no", "actual_resolve_time": (AS_OF - timedelta(days=1)).isoformat(),
             "scheduled_resolve_time": (AS_OF + timedelta(days=30)).isoformat()},
    })
    table = rc.load_table(tmp_path)
    assert sorted(r.question_id for r in table.rows) == [21, 22]
    assert [q.question_id for q in rc.available(table.rows, AS_OF)] == [22]
    assert ids(rc.similar(table, target(text="Will synthetic event occur?"), AS_OF)) == [22]


def test_benchmark_filter_uses_actual_resolve_time_too(tmp_path):
    early = (AS_OF - timedelta(days=30)).isoformat()
    post = {"id": 700, "title": "Will synthetic thing 31 happen?", "projects": {},
            "question": {"id": 31, "type": "binary", "status": "resolved", "resolution": "yes", "title": "Will synthetic thing 31 happen?",
                         "actual_resolve_time": (AS_OF + SEC).isoformat(), "scheduled_close_time": early,
                         "scheduled_resolve_time": early}}
    (tmp_path / "benchmark").mkdir()
    (tmp_path / "benchmark" / "posts_1_20250101T0000Z.jsonl").write_text(json.dumps(post) + "\n", encoding="utf-8")
    table = rc.load_table(tmp_path)
    assert [r.question_id for r in table.rows] == [31]
    assert rc.yes_rate(table, AS_OF).n == 0


@pytest.mark.parametrize("call", [
    lambda rows, t: rc.yes_rate(rows, t),
    lambda rows, t: rc.similar(rows, target(), t),
    lambda rows, t: rc.available(rows, t),
])
def test_time_zone_less_as_of_is_refused(call):
    rows = [row(10, AS_OF - timedelta(days=1))]
    with pytest.raises(ValueError, match="timezone"):
        call(rows, AS_OF.replace(tzinfo=None))


def test_time_zone_less_resolve_time_in_the_data_is_dropped_not_guessed(tmp_path):
    _write_live(tmp_path, {
        41: {"status": "resolved", "resolution": "yes", "actual_resolve_time": "2025-05-01T10:00:00"},  # no zone
        42: {"status": "resolved", "resolution": "yes", "actual_resolve_time": "2025-05-01"},  # a bare date
        43: {"status": "resolved", "resolution": "no", "actual_resolve_time": "2025-05-01T10:00:00Z"},
    })
    table = rc.load_table(tmp_path)
    assert [r.question_id for r in table.rows] == [43]
    assert table.drops[(rc.SOURCE_LIVE, rc.DROP_NAIVE_TIME)] == 2


def test_hand_built_row_without_time_zone_is_refused():
    with pytest.raises(ValueError, match="time zone"):
        rc.available([row(10, (AS_OF - timedelta(days=1)).replace(tzinfo=None))], AS_OF)


def test_later_questions_do_not_change_similarity_scores():
    """IDF is computed on the as-of pool only: adding questions resolved later changes nothing, not even scores."""
    past = [row(10, AS_OF - timedelta(days=2), title="Will synthetic rainfall exceed the record?"),
            row(11, AS_OF - timedelta(days=3), title="Will synthetic snowfall exceed the record?")]
    future = [row(20 + i, AS_OF + timedelta(days=i), title=f"Will synthetic rainfall {i} exceed?") for i in range(5)]
    q = target(text="Will synthetic rainfall exceed the record this year?")
    a, b = rc.similar(past, q, AS_OF), rc.similar(past + future, q, AS_OF)
    assert [(s.question.question_id, s.score) for s in a] == [(s.question.question_id, s.score) for s in b]


def test_live_bot_never_imports_refclass():
    """Not used by the live bot in this PR (B-56 is a tool for experiments)."""
    files = [ROOT / "main.py", ROOT / "bot_helpers.py", *(ROOT / "forecast_engine").glob("*.py")]
    for p in files:
        assert "refclass" not in p.read_text(encoding="utf-8"), p.name


# ---------------------------------------------------------------- Yes-rate and Wilson interval
def test_wilson_known_values():
    """Reference values: the roots of (p̂ − p)² = z²·p(1 − p)/n, solved separately (the definition of the Wilson interval)."""
    lo, hi = rc.wilson(7, 10)
    assert lo == pytest.approx(0.396778, abs=1e-6) and hi == pytest.approx(0.892209, abs=1e-6)
    lo, hi = rc.wilson(0, 10)
    assert lo == 0.0 and hi == pytest.approx(0.277533, abs=1e-6)
    lo, hi = rc.wilson(50, 100)
    assert lo == pytest.approx(0.403832, abs=1e-6) and hi == pytest.approx(0.596168, abs=1e-6)
    with pytest.raises(ValueError):
        rc.wilson(1, 0)


def test_yes_rate_known_inputs_with_tournament_and_category_filters():
    t = AS_OF - timedelta(days=1)
    rows = ([row(i, t, 1, tournaments=("cup-a",), categories=("synthetic-economy",)) for i in range(7)]
            + [row(10 + i, t, 0, tournaments=("cup-a",), categories=("synthetic-sport",)) for i in range(3)]
            + [row(20 + i, t, 0, tournaments=("cup-b",), categories=("synthetic-economy",)) for i in range(4)])
    a = rc.yes_rate(rows, AS_OF, tournament="cup-a")
    assert (a.n, a.yes, a.rate) == (10, 7, 0.7)
    assert (a.lo, a.hi) == pytest.approx(rc.wilson(7, 10))
    assert a.small  # n < 30 is flagged
    assert rc.yes_rate(rows, AS_OF, category="synthetic-economy").n == 11
    both = rc.yes_rate(rows, AS_OF, tournament="cup-a", category="synthetic-economy")
    assert (both.n, both.yes) == (7, 7)
    allr = rc.yes_rate(rows, AS_OF)
    assert (allr.n, allr.yes) == (14, 7)


def test_yes_rate_with_no_questions_reports_n_zero():
    r = rc.yes_rate([row(1, AS_OF + SEC)], AS_OF, tournament="synth-cup")
    assert (r.n, r.rate, r.lo, r.hi) == (0, None, None, None)
    assert "no resolved questions yet" in rc.prompt_block([r], [])


# ---------------------------------------------------------------- similar
def test_similar_returns_the_planted_near_duplicate_first():
    t = AS_OF - timedelta(days=10)
    rows = [
        row(1, t, title="Will the synthetic river flood the town of Exampleton?"),
        row(2, t, title="Will the Zorblax index close above 500 by the end of the quarter?"),  # planted near-duplicate
        row(3, t, title="Will a synthetic election be held in Fakeland?"),
        row(4, t, title="Will the index of synthetic prices rise?"),
        row(5, t, title="Will the Zorblax company hire a new chief?"),
    ]
    got = rc.similar(rows, target(text="Will the Zorblax index close above 600 by the end of the quarter?"), AS_OF, k=3)
    assert ids(got)[0] == 2
    assert len(got) <= 3 and all(a.score >= b.score for a, b in zip(got, got[1:]))


def test_similar_caps_at_k_and_skips_unrelated_questions():
    t = AS_OF - timedelta(days=1)
    rows = [row(i, t, title=f"Will synthetic metric {i} beat synthetic target?") for i in range(8)]
    rows.append(row(99, t, title="Qwerty plimsoll?"))
    got = rc.similar(rows, target(text="Will synthetic metric beat target?"), AS_OF)
    assert len(got) == 5 and 99 not in ids(got)
    assert rc.similar(rows, target(text="Xyzzy?"), AS_OF) == []  # no shared word: nothing, rather than random questions


def test_scorer_is_swappable():
    t = AS_OF - timedelta(days=1)
    rows = [row(1, t, title="aaa"), row(2, t, title="bbb"), row(3, AS_OF + SEC, title="ccc")]
    seen = {}

    def prefers_bbb(query, docs):
        seen["docs"] = list(docs)
        return np.array([1.0 if d == "bbb" else 0.5 for d in docs])

    got = rc.similar(rows, target(qid=100, text="anything"), AS_OF, scorer=prefers_bbb)
    assert ids(got) == [2, 1]
    assert seen["docs"] == ["aaa", "bbb"]  # the scorer only ever sees questions that passed the as-of filter


def test_similar_ties_go_to_later_resolved_then_lower_id():
    rows = [row(3, AS_OF - timedelta(days=5)), row(2, AS_OF - timedelta(days=1)), row(1, AS_OF - timedelta(days=5))]
    assert ids(rc.similar(rows, target(qid=100), AS_OF)) == [2, 1, 3]


# ---------------------------------------------------------------- loader: drops counted, no-data path
def test_loader_counts_drops_by_reason(tmp_path):
    ok = {"status": "resolved", "actual_resolve_time": "2025-04-01T00:00:00Z"}
    _write_live(tmp_path, {
        1: {**ok, "resolution": "yes"},
        2: {**ok, "resolution": "no"},
        3: {**ok, "resolution": "annulled"},
        4: {**ok, "resolution": "ambiguous"},
        5: {"status": "open", "resolution": None, "actual_resolve_time": None},
        6: None,  # no outcome row
        7: {"status": "resolved", "resolution": "yes", "actual_resolve_time": None},
        8: {**ok, "resolution": "42.0"},  # numeric question
        9: {**ok, "resolution": "yes"},  # also in the benchmark below: kept once
    }, snapshots={8: {"question_type": "numeric"}})
    # a second record of question 1 (shadow) is merged, not counted as a drop
    shadow = tmp_path / "runs" / "2025-run-shadow-cheap"
    shadow.mkdir()
    rec = json.loads((tmp_path / "runs" / "2025-run" / "q1.json").read_text(encoding="utf-8"))
    (shadow / "q1.json").write_text(json.dumps(rec), encoding="utf-8")
    (shadow / "q_broken.json").write_text("{not json", encoding="utf-8")
    test_run = tmp_path / "runs" / "2025-run-test_questions"
    test_run.mkdir()
    (test_run / "q77.json").write_text(json.dumps({"question": {"question_id": 77, "question_type": "binary"}}), encoding="utf-8")

    bench = tmp_path / "benchmark"
    bench.mkdir()
    q = lambda qid, **kw: {"id": qid, "type": "binary", "status": "resolved", "resolution": "no",  # noqa: E731
                           "actual_resolve_time": "2025-03-01T00:00:00Z", "title": f"Will synthetic {qid} happen?", **kw}
    posts = [
        {"id": 900, "title": "Will synthetic 9 happen?", "question": q(9),
         "projects": {"tournament": [{"slug": "synth-bench"}], "category": [{"slug": "synthetic-science"}]}},
        {"id": 901, "title": "Synthetic group", "group_of_questions": {"questions": [q(51, label="Option A"), q(52, type="numeric")]},
         "projects": {"tournament": [{"slug": "synth-bench"}], "tag": [{"slug": "synthetic-tag"}]}},
        {"id": 902, "title": "Synthetic conditional", "conditional": {"question_yes": q(53), "question_no": q(54)}},
        {"id": 903, "title": "Synthetic open", "question": q(55, status="open", resolution=None, actual_resolve_time=None)},
    ]
    (bench / "posts_1_20250101T0000Z.jsonl").write_text("[]\n", encoding="utf-8")  # older file, ignored
    (bench / "posts_1_20250201T0000Z.jsonl").write_text("\n".join(json.dumps(p) for p in posts) + "\nnot json\n", encoding="utf-8")

    table = rc.load_table(tmp_path)
    assert sorted((r.source, r.question_id) for r in table.rows) == [
        ("benchmark", 51), ("live", 1), ("live", 2), ("live", 9)]
    d = table.drops
    L, B = rc.SOURCE_LIVE, rc.SOURCE_BENCHMARK
    assert d[(L, rc.DROP_ANNULLED)] == 2
    assert d[(L, rc.DROP_UNRESOLVED)] == 1
    assert d[(L, rc.DROP_NO_OUTCOME)] == 1
    assert d[(L, rc.DROP_NO_TIME)] == 1
    assert d[(L, rc.DROP_NOT_BINARY)] == 1
    assert d[(L, rc.DROP_UNREADABLE)] == 1
    assert d[(L, rc.DROP_TEST)] == 1
    assert d[(B, rc.DROP_DUPLICATE)] == 1
    assert d[(B, rc.DROP_NOT_BINARY)] == 1
    assert d[(B, rc.DROP_CONDITIONAL)] == 1
    assert d[(B, rc.DROP_UNRESOLVED)] == 1
    assert d[(B, rc.DROP_UNREADABLE)] == 1
    assert sum(d.values()) == 13
    sub = next(r for r in table.rows if r.question_id == 51)
    assert sub.post_id == 901 and sub.title.endswith("(Option A)") and sub.tags == ("synthetic-tag",)
    assert sub.tournaments == ("synth-bench",)
    assert "1 older file(s) ignored" in table.found[B]


def test_no_data_gives_a_clear_message_not_a_crash(tmp_path, capsys):
    table = rc.load_table(tmp_path / "missing")
    assert table.no_data and table.rows == []
    assert "no runs folder" in table.found[rc.SOURCE_LIVE] and "no posts_" in table.found[rc.SOURCE_BENCHMARK]
    assert rc.main(["--summary", "--data-dir", str(tmp_path / "missing")]) == 0
    assert "no data" in capsys.readouterr().out


def test_fe_data_environment_variable_points_the_loader(tmp_path, monkeypatch):
    _write_live(tmp_path, {1: {"status": "resolved", "resolution": "yes", "actual_resolve_time": "2025-04-01T00:00:00Z"}})
    monkeypatch.setenv(rc.DATA_ENV, str(tmp_path))
    assert rc.data_dir() == tmp_path
    assert rc.data_dir(str(tmp_path / "x")) == tmp_path / "x"  # the flag wins
    assert [r.question_id for r in rc.load_table().rows] == [1]


# ---------------------------------------------------------------- CLI and prompt block
def test_summary_prints_counts_and_no_titles(tmp_path, capsys):
    ok = {"status": "resolved", "actual_resolve_time": "2025-04-01T00:00:00Z"}
    _write_live(tmp_path, {1: {**ok, "resolution": "yes"}, 2: {**ok, "resolution": "no"}, 3: {**ok, "resolution": "yes"}})
    assert rc.main(["--summary", "--data-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "synthetic event" not in out.lower()  # titles stay out
    assert re.search(r"\b3\s+live\b", out) and re.search(r"\b3\s+synth-cup\b", out)
    assert "overall Yes-rate: 2/3" in out and "small sample" in out
    assert rc.main(["--summary", "--data-dir", str(tmp_path), "--as-of", "2025-04-01T00:00:00Z"]) == 0
    assert "0 of 3 questions resolved strictly before it" in capsys.readouterr().out
    assert rc.main(["--summary", "--data-dir", str(tmp_path), "--as-of", "2025-04-02"]) == 2  # no time zone: refused


def test_prompt_block_format():
    t = AS_OF - timedelta(days=3)
    rows = [row(i, t, 1 if i < 4 else 0, title=f"Will synthetic launch {i} succeed?") for i in range(10)]
    rate = rc.yes_rate(rows, AS_OF, tournament="synth-cup")
    block = rc.prompt_block([rate], rc.similar(rows, target(text="Will synthetic launch succeed?"), AS_OF, k=3))
    assert 'Yes-rate, tournament "synth-cup": 40% of 10 resolved Yes (95% interval 17%–69%) (small sample' in block
    assert block.count("resolved YES") + block.count("resolved NO") == 3
    assert "2025-05-29" in block and "2025-06-01" not in block


# ---------------------------------------------------------------- harness line
def test_harness_refclass_check_passes():
    with __import__("tempfile").TemporaryDirectory() as d:
        res = vh.check_refclass(Path(d))
    assert [r.status for r in res] == [vh.PASS]


def test_harness_refclass_check_fails_when_the_filter_is_not_strict(monkeypatch, tmp_path):
    real = rc.available

    def leaky(rows, as_of, exclude=None):
        return real(rows, as_of + timedelta(seconds=2), exclude)

    monkeypatch.setattr(rc, "available", leaky)
    assert [r.status for r in vh.check_refclass(tmp_path)] == [vh.FAIL]


def test_harness_refclass_check_fails_when_sub_questions_leak(monkeypatch, tmp_path):
    real = rc.available
    monkeypatch.setattr(rc, "available", lambda rows, as_of, exclude=None: real(rows, as_of))
    assert [r.status for r in vh.check_refclass(tmp_path)] == [vh.FAIL]
