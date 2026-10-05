"""B-38: replay on frozen research and the outcome collector. Offline; synthetic records only."""
import asyncio
import json
from datetime import datetime, timezone

import pytest

from evals import outcomes as oc
from evals.replay import check_record, replay_record, snapshot_from_dict
from forecast_engine.clock import FixedClock
from forecast_engine.config import DEFAULT_CONFIG
from forecast_engine.core import forecast
from forecast_engine.schema import LlmCall, QuestionSnapshot, ResearchBundle, ResearchItem

AS_OF = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
Q = QuestionSnapshot(question_id=7, post_id=70, question_type="multiple_choice", question_text="Which?", options=("A", "B"),
                     resolution_criteria="Resolves to the winner.", tournaments=("minibench",),
                     open_time=datetime(2026, 10, 5, 9, tzinfo=timezone.utc))


class FakeLlm:
    def __init__(self, output="A: 60%\nB: 40%"):
        self.output, self.prompts = output, []

    async def complete(self, *, model, prompt, purpose, max_tokens, reasoning_effort=None):
        self.prompts.append(prompt)
        return LlmCall(purpose=purpose, provider="openrouter", model=model, params={}, prompt=prompt, requested_at=AS_OF,
                       output=self.output, cost_usd=0.001)


def _bundle():
    return ResearchBundle(as_of=AS_OF, providers=["asknews_latest"], calls=[],
                          items=[ResearchItem(source="asknews_latest", text="Synthetic news.", title="T",
                                              published_at=datetime(2026, 10, 4, tzinfo=timezone.utc))])


def _live_record() -> dict:
    rec = asyncio.run(forecast(Q, FixedClock(AS_OF), _bundle(), DEFAULT_CONFIG, FakeLlm()))
    return json.loads(json.dumps(rec.to_dict()))  # exactly what the record writer stores


def test_snapshot_round_trip():
    assert snapshot_from_dict(Q.to_dict()) == Q
    old = Q.to_dict()
    del old["tournaments"]  # records written before a field existed still load
    assert snapshot_from_dict(old).tournaments == ()


def test_live_record_reproduces_its_prompts_and_tampering_is_caught():
    rec = _live_record()
    assert check_record(rec)
    rec["research"]["items"][0]["text"] = "Edited later."
    assert not check_record(rec)


def test_replay_runs_on_frozen_inputs_as_of_the_record():
    rec = _live_record()
    llm = FakeLlm()
    replayed = asyncio.run(replay_record(rec, DEFAULT_CONFIG, llm))
    assert replayed.as_of == AS_OF and replayed.aggregate == pytest.approx(rec["aggregate"])
    assert llm.prompts[0] == rec["forecasters"][0]["call"]["prompt"]  # same inputs, same prompt


# --- outcome collector --------------------------------------------------------------------------------------------------

def test_find_question_in_plain_and_group_posts():
    plain = {"question": {"id": 5, "status": "resolved", "resolution": "yes"}}
    group = {"group_of_questions": {"questions": [{"id": 8, "resolution": "no"}, {"id": 9, "resolution": "-1.87"}]}}
    assert oc.find_question(plain, 5)["resolution"] == "yes"
    assert oc.find_question(group, 9)["resolution"] == "-1.87"
    assert oc.find_question(group, 99) is None


class FakeResp:
    def __init__(self, data, status=200):
        self._data, self.status_code, self.ok = data, status, status == 200

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, posts):
        self.posts, self.calls = posts, []

    def get(self, url, timeout=None):
        pid = int(url.rstrip("/").split("/")[-1])
        self.calls.append(pid)
        return FakeResp(self.posts[pid])


def test_update_records_changes_and_skips_settled(tmp_path, monkeypatch):
    monkeypatch.setattr(oc, "REQUEST_GAP_S", 0)
    runs = tmp_path / "runs" / "1" / "20261005T000000Z-tournament"
    runs.mkdir(parents=True)
    for qid, pid in ((1, 10), (2, 20)):
        (runs / f"q{qid}.json").write_text(json.dumps({"question": {"question_id": qid, "post_id": pid, "question_type": "binary"}}))
    out = tmp_path / "outcomes.jsonl"
    posts = {10: {"question": {"id": 1, "status": "resolved", "resolution": "yes"}},
             20: {"question": {"id": 2, "status": "open", "resolution": None}}}
    s = FakeSession(posts)
    clock = FixedClock(AS_OF)
    assert oc.update(s, clock, tmp_path / "runs", out)["newly_resolved"] == 1
    s.calls.clear()
    oc.update(s, clock, tmp_path / "runs", out)
    assert s.calls == [20]  # the settled question isn't fetched again
    assert oc.load_outcomes(out)[1]["resolution"] == "yes"
    assert len(out.read_text().splitlines()) == 2  # an unchanged open question isn't re-appended
