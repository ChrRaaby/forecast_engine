"""B-75(2) (Christian, 2026-11-07): the config's prompt_version picks the forecast prompt, so a prompt experiment replays on frozen
research, or runs as an unpublished shadow arm, before it goes live. Offline; synthetic only."""
import asyncio
import importlib.util
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from evals import prompt_archive as pa
from evals import replay
from evals import validate_harness as vh
from forecast_engine import config as live_config
from forecast_engine import prompt_versions, provenance
from forecast_engine import prompts as live_prompts
from forecast_engine.clock import FixedClock
from forecast_engine.config import DEFAULT_CONFIG, TOURNAMENT_CONFIG
from forecast_engine.core import ForecastFailed, forecast
from forecast_engine.schema import ResearchBundle

VARIANT = "test-variant-yes"
EXTRA = "\nThe probability you give is the probability that the question resolves YES.\n"
CASES = pa.fixture_cases()
VARIANT_SRC = f'''from forecast_engine import prompts

EXTRA = {EXTRA!r}


def forecast_prompt(q, research, today):
    return prompts.forecast_prompt(q, research, today) + EXTRA
'''


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def variant(monkeypatch, tmp_path):
    """A registered experimental variant (live prompt + one line), with a correct frozen archive copy."""
    path = tmp_path / "test_variant_yes.py"
    path.write_text(VARIANT_SRC, encoding="utf-8")
    mod = _load(path, "test_variant_yes")
    monkeypatch.setitem(prompt_versions.VARIANTS, VARIANT, mod)
    frozen = SimpleNamespace(VERSION=VARIANT, __file__=str(path),
                             build=lambda q, items, as_of: pa.ARCHIVE[prompt_versions.LIVE].build(q, items, as_of) + EXTRA)
    monkeypatch.setitem(pa.ARCHIVE, VARIANT, pa.ArchivedTemplate(VARIANT, frozen, pa.file_sha256(frozen),
                                                                 pa.variant_fingerprint(VARIANT)))
    return mod


def _forecast(cfg, case=CASES[0]):
    _, q, items, as_of = case
    llm = vh.FakeLlm()
    bundle = ResearchBundle(as_of=as_of, providers=["synthetic"], items=list(items), calls=[])
    try:
        rec = asyncio.run(forecast(q, FixedClock(as_of), bundle, cfg, llm))
    except ForecastFailed as e:  # the fake answers don't parse for every question type; the prompts were still sent
        rec = e.record
    return rec, llm


def _shadow_arm(**kw):
    """The live config's settings with an experimental prompt: what a prompt experiment's arm looks like."""
    return replace(DEFAULT_CONFIG, prompt_version=VARIANT, **kw)


def _configs_with_shadow(arm):
    return SimpleNamespace(DEFAULT_CONFIG=live_config.DEFAULT_CONFIG, TOURNAMENT_CONFIG=live_config.TOURNAMENT_CONFIG,
                           WIDE_CONFIG=live_config.WIDE_CONFIG, CONFIG_VERSION=live_config.CONFIG_VERSION,
                           SHADOW_CONFIGS={**live_config.SHADOW_CONFIGS, "exp-prompt": arm}, EXP008_ARMS=live_config.EXP008_ARMS)


def test_live_configs_still_send_exactly_the_live_prompt():
    assert DEFAULT_CONFIG.prompt_version == TOURNAMENT_CONFIG.prompt_version == prompt_versions.LIVE
    for case in CASES:
        _, q, items, as_of = case
        _, llm = _forecast(TOURNAMENT_CONFIG, case)
        assert llm.prompts[0] == pa.ARCHIVE[prompt_versions.LIVE].build(q, items, as_of)  # byte-identical to the frozen template
    assert pa.drift_problems() == []


def test_a_variant_config_sends_the_variant_prompt(variant):
    rec, llm = _forecast(_shadow_arm())
    sent = [p for p in llm.prompts if "Your research assistant says" in p]
    assert sent and all(p.endswith(EXTRA) for p in sent)
    _, live_llm = _forecast(DEFAULT_CONFIG)
    assert [p for p in live_llm.prompts if "Your research assistant says" in p][0] + EXTRA == sent[0]
    assert pa.drift_problems() == []


def test_unknown_prompt_version_is_refused_before_any_model_call():
    llm = vh.FakeLlm()
    _, q, items, as_of = CASES[0]
    with pytest.raises(prompt_versions.UnknownPromptVersion, match="neither the live prompt"):
        asyncio.run(forecast(q, FixedClock(as_of), ResearchBundle(as_of=as_of, providers=[], items=list(items), calls=[]),
                             replace(DEFAULT_CONFIG, prompt_version="typo"), llm))
    assert llm.calls == 0


def test_variant_records_name_their_prompt_version(variant, tmp_path):
    rec, _ = _forecast(_shadow_arm())
    info = provenance.gather_run_info("run", "tournament", False, env={"GITHUB_SHA": "abc"})
    prov = provenance.record_provenance(info, _shadow_arm(), role="shadow", roster="exp-prompt")
    assert prov["prompts"]["forecast"] == VARIANT
    from forecast_engine.records import RecordWriter

    RecordWriter("r", root=tmp_path).write(rec, published=False, status="shadow", provenance=prov)
    full = json.loads(next((tmp_path / "r").glob("q*.json")).read_text(encoding="utf-8"))
    assert full["prompt_version"] == VARIANT
    assert replay.record_prompt_version(full) == VARIANT and replay.check_record(full)  # its own template checks it


def test_replay_runs_a_prompt_experiment_on_an_old_records_frozen_research(variant):
    old, _ = _forecast(DEFAULT_CONFIG)
    old = json.loads(json.dumps(old.to_dict()))  # an archived live record, made with the live prompt
    llm = vh.FakeLlm()
    new = asyncio.run(replay.replay_record(old, _shadow_arm(), llm))
    sent = [p for p in llm.prompts if "Your research assistant says" in p]
    assert sent and all(p.endswith(EXTRA) for p in sent)  # the experiment's prompt...
    assert new.research.items[0].text == old["research"]["items"][0]["text"]  # ...on the record's own frozen research


def test_drift_guard_refuses_a_variant_in_a_published_config(variant, monkeypatch):
    cfg = _configs_with_shadow(_shadow_arm())
    cfg.TOURNAMENT_CONFIG = replace(live_config.TOURNAMENT_CONFIG, prompt_version=VARIANT)
    monkeypatch.setattr(pa, "_config", cfg)
    assert [p for p in pa.drift_problems() if "a published config must use the live prompt" in p]


def test_drift_guard_accepts_a_variant_in_a_shadow_arm(variant, monkeypatch):
    monkeypatch.setattr(pa, "_config", _configs_with_shadow(_shadow_arm()))
    assert pa.drift_problems() == []


def test_drift_guard_refuses_an_unregistered_version_in_a_shadow_arm(monkeypatch):
    monkeypatch.setattr(pa, "_config", _configs_with_shadow(replace(DEFAULT_CONFIG, prompt_version="nope")))
    assert [p for p in pa.drift_problems() if "neither the live prompt nor a registered variant" in p]


def test_drift_guard_wants_an_archive_copy_of_every_variant(variant, monkeypatch):
    monkeypatch.delitem(pa.ARCHIVE, VARIANT)
    assert [p for p in pa.drift_problems() if "has no frozen template in the archive" in p]


def test_drift_guard_catches_an_edited_variant(variant, monkeypatch):
    path = variant.__file__
    with open(path, "a", encoding="utf-8") as f:
        f.write("# edited after archiving\n")
    assert [p for p in pa.drift_problems() if f"the code of variant {VARIANT!r}" in p]
    monkeypatch.setattr(variant, "forecast_prompt", lambda q, r, t: live_prompts.forecast_prompt(q, r, t) + "changed")
    assert [p for p in pa.drift_problems() if "doesn't render what the variant renders" in p]


def test_variant_modules_are_part_of_the_code_fingerprint():
    assert "forecast_engine/prompt_variants/*.py" in provenance.CODE_FILES
    assert any(p.parent.name == "prompt_variants" for p in provenance.code_paths())
