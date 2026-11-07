"""B-51a outcome screen: synthetic records only."""
from datetime import datetime, timezone

from tools import research_flag_outcomes as rfo

AS_OF = datetime(2026, 11, 7, tzinfo=timezone.utc).isoformat()


def _rec(qid, text, as_of=AS_OF, qtype="binary"):
    return {"question": {"question_id": qid, "question_type": qtype}, "as_of": as_of,
            "research": {"as_of": AS_OF, "providers": [], "calls": [], "items": [{"source": "asknews_latest", "text": text}]}}


BAD, OK = "Friday, October 4, 2026", "Sunday, October 4, 2026"


def test_rows_use_last_outcome_row_earliest_record_and_skip_unscored():
    recs = [_rec(1, BAD), _rec(1, OK, as_of="2026-11-08T00:00:00+00:00"), _rec(2, OK), _rec(3, OK)]
    outcomes = {1: {"scores": {"peer_score": -10.0}}, 2: {"scores": {"peer_score": 4.0}}, 3: {"scores": None}}
    rows = rfo.rows_from_records(recs, outcomes)
    assert [(r["qid"], r["flagged"], r["peer"]) for r in rows] == [(1, True, -10.0), (2, False, 4.0)]


def test_summary_and_gate():
    rows = [{"qid": i, "type": "binary", "flagged": i < 5, "peer": -20.0 if i < 5 else 5.0} for i in range(40)]
    s = rfo.summarise(rows)
    assert s["n_flagged"] == 5 and s["diff"] == -25.0 and s["ci"][1] < 0
    assert "not reached" in rfo.gate(s)
    s["n"] = 150
    assert "propose B-51b" in rfo.gate(s)
    s["ci"] = (-5.0, 3.0)
    assert "close B-51b" in rfo.gate(s)


def test_report_without_flagged_rows():
    assert "gate not reached" in rfo.report([{"qid": 1, "type": "binary", "flagged": False, "peer": 1.0}])
