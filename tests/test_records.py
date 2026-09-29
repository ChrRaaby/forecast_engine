import json
from datetime import datetime, timezone

from forecast_engine.records import RecordWriter
from forecast_engine.schema import ForecasterOutput, ForecastRecord, LlmCall, QuestionSnapshot, ResearchBundle

AS_OF = datetime(2026, 5, 1, tzinfo=timezone.utc)


def _record():
    call = LlmCall(purpose="forecast", provider="openrouter", model="m", params={}, prompt="p", requested_at=AS_OF,
                   output="Probability: 30%", tokens_in=1000, tokens_out=500, cost_usd=0.0021)
    research_call = LlmCall(purpose="research", provider="asknews", model="news", params={}, prompt="q",
                            requested_at=AS_OF, extra={"asknews_calls": 1})
    return ForecastRecord(
        config_version="v", config_hash="h",
        question=QuestionSnapshot(question_id=7, post_id=8, question_type="binary", question_text="Q?"),
        as_of=AS_OF, forecasters=[ForecasterOutput(model="m", call=call, prediction=0.3)], aggregate=0.3,
        aggregation="median",
        research=ResearchBundle(as_of=AS_OF, providers=["asknews_latest"], items=[], calls=[research_call]),
    )


def test_writer_appends_cost_log_and_full_record(tmp_path):
    w = RecordWriter("run1", root=tmp_path)
    w.write(_record(), published=False, status="ok")
    w.write(_record(), published=False, status="ok")
    lines = (tmp_path / "run1" / "forecasts.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    row = json.loads(lines[0])
    assert row["cost_usd"] == 0.0021 and row["tokens_in"] == [1000] and row["asknews_calls"] == 1
    assert row["config_version"] == "v" and row["published"] is False
    full = json.loads((tmp_path / "run1" / "q7.json").read_text(encoding="utf-8"))
    assert full["forecasters"][0]["call"]["prompt"] == "p"
