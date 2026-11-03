"""Record provenance (design doc 2026-11-03, ADR-0011): every live and shadow record names the code, prompts, settings and libraries
that made it; an unregistered code change turns CI red and flags the run's records."""
import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest
from forecasting_tools import BinaryQuestion, MultipleChoiceQuestion, NumericQuestion

import main
from evals import replay
from forecast_engine import polarity, provenance, research
from forecast_engine.clock import FixedClock
from forecast_engine.config import DEFAULT_CONFIG, EXP008_ARMS, TOURNAMENT_CONFIG, WIDE_CONFIG
from forecast_engine.llm import OpenRouterClient
from forecast_engine.records import RecordWriter
from forecast_engine.schema import ResearchBundle
from tests.test_core import AS_OF, Q, _bundle
from tests.test_records import _record
from tests.test_tournament import _bot

# config_hash of every config the bot can run, as on main before this change: provenance must not alter one of them.
CONFIG_HASHES = {
    "default": ("c18ebdd3d1ef", DEFAULT_CONFIG),
    "tournament": ("11fb02f5ac67", TOURNAMENT_CONFIG),
    "wide": ("9785b336fd21", WIDE_CONFIG),
    "exp008-b-gaps": ("cb90669a252c", EXP008_ARMS["exp008-b-gaps"]),
    "exp008-c-against": ("98597cf32085", EXP008_ARMS["exp008-c-against"]),
    "exp008-d-control": ("e1a704e2c285", EXP008_ARMS["exp008-d-control"]),
}
# sha256 of polarity.PROMPT per POLARITY_PROMPT_VERSION. Append only; an edit to the prompt needs a new version line.
POLARITY_PROMPTS = {"polarity-2026-10-05": "47c159d42ce86248de2976cf2c0b7c57079944daa6f1afba7b34201072cbc653"}


def _run_info(registered=True, **kw):
    info = provenance.gather_run_info("run", "tournament", False, env={"GITHUB_SHA": "abc123", "GITHUB_RUN_ID": "99",
                                                                       "GITHUB_REPOSITORY": "o/r", "GITHUB_EVENT_NAME": "x"})
    return info if registered else provenance.RunInfo(**{**info.__dict__, "code_version": None, **kw})


@pytest.mark.parametrize("name", sorted(CONFIG_HASHES))
def test_config_hashes_unchanged(name):
    want, cfg = CONFIG_HASHES[name]
    assert cfg.config_hash() == want


def test_live_code_fingerprint_is_registered():
    fp = provenance.code_fingerprint()
    versions = provenance.code_versions()
    assert fp in versions, (
        f"forecasting code changed: fingerprint {fp} is not in forecast_engine/code_versions.json. Append "
        f'"{fp}": {{"label": "code-YYYY-MM-DD", "note": "<what changed>"}} (never edit an existing entry); if the change alters '
        "forecasts, also bump CONFIG_VERSION. See forecast_engine/provenance.py.")
    labels = [v["label"] for v in versions.values()]
    assert len(labels) == len(set(labels)) and all(v.get("note") for v in versions.values())


def test_polarity_prompt_pinned_to_its_version():
    got = hashlib.sha256(polarity.PROMPT.encode()).hexdigest()
    assert POLARITY_PROMPTS.get(polarity.POLARITY_PROMPT_VERSION) == got, (
        f"polarity.PROMPT changed (sha256 {got}): give it a new POLARITY_PROMPT_VERSION and add it to POLARITY_PROMPTS")


def test_fingerprint_ignores_line_endings_but_nothing_else(tmp_path, monkeypatch):
    monkeypatch.setattr(provenance, "CODE_FILES", ("pkg/*.py", "poetry.lock"))
    (tmp_path / "pkg").mkdir()
    src = tmp_path / "pkg" / "a.py"
    (tmp_path / "poetry.lock").write_text("lock\n", encoding="utf-8")
    src.write_text("x = 1\n", encoding="utf-8")
    base = provenance.code_fingerprint(tmp_path)
    src.write_bytes(b"x = 1\r\n")  # a Windows checkout
    assert provenance.code_fingerprint(tmp_path) == base
    src.write_text("x = 1  # why\n", encoding="utf-8")  # strict: a comment counts too
    assert provenance.code_fingerprint(tmp_path) != base
    src.write_text("x = 2\n", encoding="utf-8")
    assert provenance.code_fingerprint(tmp_path) != base
    src.write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "poetry.lock").write_text("lock2\n", encoding="utf-8")
    assert provenance.code_fingerprint(tmp_path) != base


def test_run_info_from_github_env():
    info = _run_info()
    assert info.git_commit == "abc123" and info.github_run == "https://github.com/o/r/actions/runs/99" and info.registered
    assert info.libraries["forecasting-tools"] and info.lockfile_sha and info.python.startswith("3.")


def test_version_id_is_deterministic_and_separates_configs():
    info = _run_info()
    a = provenance.record_provenance(info, TOURNAMENT_CONFIG, role="live", roster="frontier")
    assert a == provenance.record_provenance(info, TOURNAMENT_CONFIG, role="live", roster="frontier")
    b = provenance.record_provenance(info, DEFAULT_CONFIG, role="shadow", roster="cheap")
    assert a["version_id"] != b["version_id"] and a["version_id"].startswith("v-") and len(a["version_id"]) == 10
    assert a["settings"]["config_hash"] == TOURNAMENT_CONFIG.config_hash() and a["settings"]["full"] == TOURNAMENT_CONFIG.to_dict()
    assert a["prompts"] == {"forecast": TOURNAMENT_CONFIG.prompt_version, "polarity_check": polarity.POLARITY_PROMPT_VERSION}
    arm = provenance.record_provenance(info, EXP008_ARMS["exp008-b-gaps"], role="shadow", roster="exp008-b-gaps")
    assert arm["prompts"]["research_followup"] == EXP008_ARMS["exp008-b-gaps"].research.followup.prompt_version
    # the run id is not part of the version: the same code and settings in two runs give the same version_id
    other = provenance.RunInfo(**{**info.__dict__, "run_id": "other", "github_run": None})
    assert provenance.record_provenance(other, TOURNAMENT_CONFIG, role="live", roster="frontier")["version_id"] == a["version_id"]


def test_roster_names():
    assert [provenance.roster_name(c) for c in (TOURNAMENT_CONFIG, DEFAULT_CONFIG, WIDE_CONFIG)] == ["frontier", "cheap", "wide"]
    assert provenance.roster_name(EXP008_ARMS["exp008-b-gaps"]).startswith("custom-")


def test_writer_stores_provenance_prompt_version_and_submitted(tmp_path):
    prov = provenance.record_provenance(_run_info(), DEFAULT_CONFIG, role="live", roster="cheap")
    w = RecordWriter("run1", root=tmp_path)
    w.write(_record(), published=True, status="ok", provenance=prov, submitted={"type": "binary", "probability_yes": 0.3})
    full = json.loads((tmp_path / "run1" / "q7.json").read_text(encoding="utf-8"))
    assert full["provenance"] == json.loads(json.dumps(prov)) and full["prompt_version"] == DEFAULT_CONFIG.prompt_version
    assert full["submitted"]["probability_yes"] == 0.3
    row = json.loads((tmp_path / "run1" / "forecasts.jsonl").read_text(encoding="utf-8"))
    assert row["version_id"] == prov["version_id"] and row["role"] == "live" and row["git_commit"] == "abc123"


def test_old_records_without_provenance_still_write_the_same_keys(tmp_path):
    w = RecordWriter("run1", root=tmp_path)
    w.write(_record(), published=False, status="ok")
    full = json.loads((tmp_path / "run1" / "q7.json").read_text(encoding="utf-8"))
    assert not {"provenance", "prompt_version", "submitted"} & set(full)


def test_replay_prefers_the_stored_prompt_version():
    rec = {"config_version": "frontier-2026-10-08"}
    assert replay.record_prompt_version(rec) == "template-2026-09-26"
    assert replay.record_prompt_version({**rec, "prompt_version": "template-2026-09-26"}) == "template-2026-09-26"
    with pytest.raises(replay.UnknownPromptVersion):  # a stored version that isn't archived is refused, never guessed
        replay.record_prompt_version({**rec, "prompt_version": "nope"})


def test_shadow_and_live_records_carry_provenance(tmp_path, monkeypatch):
    bot = _bot(tmp_path, monkeypatch, None)
    bot.run_info = _run_info()
    asyncio.run(bot._run_shadows(Q, _bundle(), TOURNAMENT_CONFIG))
    rec = json.loads((tmp_path / "data" / "forecasts" / "run-shadow-cheap" / f"q{Q.question_id}.json").read_text(encoding="utf-8"))
    assert rec["provenance"]["role"] == "shadow" and rec["provenance"]["roster"] == "cheap"
    assert rec["provenance"]["settings"]["config_hash"] == DEFAULT_CONFIG.config_hash()
    assert provenance.UNREGISTERED_FLAG not in rec["flags"]


def test_unregistered_code_still_writes_but_flags_every_record(tmp_path, monkeypatch):
    bot = _bot(tmp_path, monkeypatch, None)
    bot.run_info = _run_info(registered=False)
    asyncio.run(bot._run_shadows(Q, _bundle(), TOURNAMENT_CONFIG))
    rec = json.loads((tmp_path / "data" / "forecasts" / "run-shadow-cheap" / f"q{Q.question_id}.json").read_text(encoding="utf-8"))
    assert rec["status"] == "shadow" and provenance.UNREGISTERED_FLAG in rec["flags"]
    assert rec["provenance"]["code"]["code_version"] is None


def test_manifest_written(tmp_path):
    path = _run_info().write(tmp_path / "r")
    m = json.loads(path.read_text(encoding="utf-8"))
    assert m["code"]["git_commit"] == "abc123" and m["code"]["code_fingerprint"] == provenance.code_fingerprint()


def test_openrouter_call_records_upstream_host_and_generation_id():
    client = OpenRouterClient(FixedClock(AS_OF), api_key="test")
    resp = SimpleNamespace(
        id="gen-123", model="qwen/qwen3.8-max-0902", model_extra={"provider": "Alibaba"},
        choices=[SimpleNamespace(message=SimpleNamespace(content="Probability: 30%"), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, model_extra={"cost": 0.001}),
    )

    async def create(**_):
        return resp

    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    call = asyncio.run(client.complete(model="qwen/qwen3.8-max-0902", prompt="p", purpose="forecast", max_tokens=10))
    assert call.extra["upstream_provider"] == "Alibaba" and call.extra["generation_id"] == "gen-123"
    resp.model_extra, resp.id = {}, None  # not returned -> not set, never guessed
    call = asyncio.run(client.complete(model="m", prompt="p", purpose="forecast", max_tokens=10))
    assert "upstream_provider" not in call.extra and "generation_id" not in call.extra


def test_gemini_call_records_model_version(monkeypatch):
    data = {"candidates": [{"content": {"parts": [{"text": "brief"}]}, "groundingMetadata": {}}],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
            "modelVersion": "gemini-3.5-flash-lite-001", "responseId": "r-1"}
    monkeypatch.setattr(research, "_post_gemini", lambda model, prompt: data)
    bundle = ResearchBundle(as_of=AS_OF, providers=[], items=[], calls=[])
    asyncio.run(research._gemini_grounded(Q, FixedClock(AS_OF), DEFAULT_CONFIG.research, bundle))
    assert bundle.calls[0].extra["model_version"] == "gemini-3.5-flash-lite-001" and bundle.calls[0].extra["response_id"] == "r-1"


def test_submitted_block_is_what_metaculus_gets():
    b = BinaryQuestion(question_text="x", id_of_question=1, id_of_post=2)
    assert main.submitted_block(b, main.to_prediction(b, 0.3)) == {"type": "binary", "probability_yes": 0.3}
    mc = MultipleChoiceQuestion(question_text="x", id_of_question=1, id_of_post=2, options=["a", "b"])
    sub = main.submitted_block(mc, main.to_prediction(mc, {"a": 0.25, "b": 0.75}))
    assert sub == {"type": "multiple_choice", "probabilities": {"a": 0.25, "b": 0.75}}
    n = NumericQuestion(question_text="x", id_of_question=1, id_of_post=2, lower_bound=0, upper_bound=100,
                        open_lower_bound=False, open_upper_bound=True, zero_point=None)
    pred = main.to_prediction(n, [(0.1, 10.0), (0.5, 40.0), (0.9, 80.0)])
    sub = main.submitted_block(n, pred)
    assert sub["type"] == "numeric" and len(sub["continuous_cdf"]) == 201 == sub["axis"]["cdf_size"]
    assert sub["continuous_cdf"] == [p.percentile for p in pred.get_cdf()]
    assert sub["axis"]["open_upper_bound"] is True and sub["axis"]["zero_point"] is None
