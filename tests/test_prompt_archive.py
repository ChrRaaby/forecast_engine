"""F-13 replay trap: the integrity check rebuilds each record's prompt with the template that made it (evals.prompt_archive), so
older records stay checkable after a prompt change, and the archive can't drift from the live code. Offline; synthetic only."""
import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone
from types import ModuleType, SimpleNamespace

import pytest

from evals import prompt_archive as pa
from evals import replay
from evals import validate_harness as vh
from evals.replay import TamperedRecord, UnknownPromptVersion, assert_record_intact, check_record, replay_record
from forecast_engine import config as live_config
from forecast_engine import core
from forecast_engine import prompts as live_prompts
from forecast_engine import research_followup as rf
from forecast_engine.clock import FixedClock
from forecast_engine.config import CONFIG_VERSION, DEFAULT_CONFIG
from forecast_engine.core import ForecastFailed, forecast
from forecast_engine.schema import ResearchBundle, ResearchItem

LIVE_VERSION = "template-2026-09-26"
# Tripwires, deliberately duplicated from evals/prompt_archive.py: the archive is append-only and archived templates are frozen.
PINNED_CONFIG_VERSIONS = {
    "m1-baseline-2026-09-30b": LIVE_VERSION, "m1-baseline-2026-09-30c": LIVE_VERSION, "m1-baseline-2026-10-01": LIVE_VERSION,
    "m1-baseline-2026-10-03": LIVE_VERSION, "m1-baseline-2026-10-03b": LIVE_VERSION, "m1-baseline-2026-10-05": LIVE_VERSION,
    "m1-baseline-2026-10-06": LIVE_VERSION, "frontier-2026-10-08": LIVE_VERSION,
}
PINNED_FROZEN_FILES = {LIVE_VERSION: "e79eb2be55c1a48772c5f4fd854eda498f43b6bad3981fb62cf16bd7e4443c9c"}

AS_OF = datetime(2025, 3, 14, 12, 0, tzinfo=timezone.utc)
NEW_LINE = "Your final answer is the probability that this question resolves YES.\n"  # a made-up EXP-002-style change


def _record(q, items, as_of=AS_OF) -> dict:
    """What the record writer stores, built offline (a failed parse still stores the prompts)."""
    bundle = ResearchBundle(as_of=as_of, providers=["synthetic"], items=list(items), calls=[])
    try:
        rec = asyncio.run(forecast(q, FixedClock(as_of), bundle, DEFAULT_CONFIG, vh.FakeLlm()))
    except ForecastFailed as e:
        rec = e.record
    return json.loads(json.dumps(rec.to_dict()))


def _tamper(rec: dict) -> dict:
    out = json.loads(json.dumps(rec))
    items = out["research"]["items"]
    if items:
        items[0]["text"] = "Edited after the fact."
    else:
        items.append({"source": "synthetic", "text": "Added after the fact.", "url": None, "title": None, "published_at": None})
    return out


CASES = pa.fixture_cases()


# ------------------------------------------------------------------ today's records and the live template
def test_drift_guard_is_clean_on_the_current_code():
    assert pa.drift_problems() == []
    assert DEFAULT_CONFIG.prompt_version == LIVE_VERSION and pa.PROMPT_VERSION_BY_CONFIG_VERSION[CONFIG_VERSION] == LIVE_VERSION


@pytest.mark.parametrize("name,q,items,as_of", CASES, ids=[c[0] for c in CASES])
def test_archived_template_equals_live_prompt_byte_for_byte(name, q, items, as_of):
    assert pa.ARCHIVE[LIVE_VERSION].build(q, items, as_of) == pa.live_prompt(q, items, as_of)


def test_fixtures_cover_every_question_type():
    assert {c[1].question_type for c in CASES} == {"binary", "multiple_choice", "numeric", "discrete"}


@pytest.mark.parametrize("name,q,items,as_of", CASES, ids=[c[0] for c in CASES])
def test_a_record_made_with_todays_prompts_passes_and_tampering_fails(name, q, items, as_of):
    rec = _record(q, items, as_of)
    assert rec["config_version"] == CONFIG_VERSION and replay.record_prompt_version(rec) == LIVE_VERSION
    assert check_record(rec)
    assert_record_intact(rec)
    bad = _tamper(rec)
    assert not check_record(bad)
    with pytest.raises(TamperedRecord) as e:
        assert_record_intact(bad)
    assert not isinstance(e.value, UnknownPromptVersion)


def test_a_record_with_a_follow_up_section_needs_no_follow_up_archive(monkeypatch):
    """B-17: the critic and search prompts aren't part of the forecast prompt; what they found is stored as research items."""
    q = CASES[0][1]
    items = [ResearchItem(source="asknews_latest", text="Made-up article.", title="Made-up"),
             ResearchItem(source=rf.SOURCE, title=rf.SECTION_TITLE, text=rf.SECTION_TEXT),
             ResearchItem(source=rf.SOURCE, title="Follow-up search 1: made-up query", text="Made-up finding.")]
    rec = _record(q, items)
    monkeypatch.setattr(rf, "critic_prompt", lambda *a, **k: "a different critic prompt")
    monkeypatch.setattr(rf, "search_prompt", lambda *a, **k: "a different search prompt")
    assert check_record(rec) and pa.drift_problems() == []
    rec["research"]["items"][-1]["text"] = "Edited later."
    assert not check_record(rec)


# ------------------------------------------------------------------ a simulated prompt change
def _new_template_module() -> ModuleType:
    m = ModuleType("frozen_prompts_test_v2")
    m.VERSION = "test-v2"
    m.__file__ = __file__  # not a real frozen file; only the drift-guard tests look at it
    m.build = lambda q, items, as_of: pa.ARCHIVE[LIVE_VERSION].build(q, items, as_of) + NEW_LINE
    return m


@pytest.fixture
def prompt_changed(monkeypatch):
    """The live prompt changes (new line at the end), with a bumped prompt version, a new CONFIG_VERSION and an archive entry."""
    old_builder = live_prompts.forecast_prompt
    new_builder = lambda q, research, today: old_builder(q, research, today) + NEW_LINE  # noqa: E731
    monkeypatch.setattr(core, "forecast_prompt", new_builder)
    monkeypatch.setattr(live_prompts, "forecast_prompt", new_builder)
    monkeypatch.setattr(core, "CONFIG_VERSION", "test-config-v2")
    monkeypatch.setitem(pa.ARCHIVE, "test-v2", pa.ArchivedTemplate("test-v2", _new_template_module(), "-", "-"))
    monkeypatch.setitem(pa.PROMPT_VERSION_BY_CONFIG_VERSION, "test-config-v2", "test-v2")


@pytest.mark.parametrize("name,q,items,as_of", CASES[:1] + CASES[3:5], ids=[c[0] for c in CASES[:1] + CASES[3:5]])
def test_old_and_new_records_both_pass_after_a_prompt_change(request, name, q, items, as_of):
    old = _record(q, items, as_of)  # made before the change
    request.getfixturevalue("prompt_changed")
    new = _record(q, items, as_of)
    assert new["config_version"] == "test-config-v2" and new["forecasters"][0]["call"]["prompt"].endswith(NEW_LINE)
    # the trap this fixes: today's code no longer rebuilds the old record's prompt...
    assert pa.live_prompt(q, items, as_of) != old["forecasters"][0]["call"]["prompt"]
    # ...but each record is checked against the template that made it
    assert replay.record_prompt_version(old) == LIVE_VERSION and replay.record_prompt_version(new) == "test-v2"
    for rec in (old, new):
        assert check_record(rec)
        assert_record_intact(rec)
        assert not check_record(_tamper(rec))
        with pytest.raises(TamperedRecord):
            assert_record_intact(_tamper(rec))


def test_replaying_an_old_record_uses_the_old_template_for_the_check_and_the_new_prompt_for_the_forecast(prompt_changed):
    q, items, as_of = CASES[0][1], CASES[0][2], CASES[0][3]
    with pytest.MonkeyPatch.context() as mp:  # make the old record with the old code
        mp.setattr(core, "forecast_prompt", pa.ARCHIVE[LIVE_VERSION].module.forecast_prompt)
        mp.setattr(core, "CONFIG_VERSION", CONFIG_VERSION)
        old = _record(q, items, as_of)
    assert not old["forecasters"][0]["call"]["prompt"].endswith(NEW_LINE)
    llm = vh.FakeLlm()
    replayed = asyncio.run(replay_record(old, DEFAULT_CONFIG, llm))
    sent = [p for p in llm.prompts if "Your research assistant says" in p]
    assert sent and all(p == pa.live_prompt(q, items, as_of) and p.endswith(NEW_LINE) for p in sent)
    assert replayed.config_version == "test-config-v2"
    tampered = _tamper(old)
    llm = vh.FakeLlm()
    with pytest.raises(TamperedRecord):
        asyncio.run(replay_record(tampered, DEFAULT_CONFIG, llm))
    assert llm.calls == 0


# ------------------------------------------------------------------ unknown versions are refused, never rebuilt with today's code
@pytest.mark.parametrize("config_version", ["never-existed-2026-01-01", None, "missing"])
def test_unknown_config_version_is_refused_even_if_todays_code_would_match(config_version):
    rec = _record(CASES[0][1], CASES[0][2])
    if config_version == "missing":
        del rec["config_version"]
    else:
        rec["config_version"] = config_version
    with pytest.raises(UnknownPromptVersion, match="no prompt version"):
        check_record(rec)
    with pytest.raises(UnknownPromptVersion):
        assert_record_intact(rec)
    llm = vh.FakeLlm()
    with pytest.raises(UnknownPromptVersion):
        asyncio.run(replay_record(rec, DEFAULT_CONFIG, llm))
    assert llm.calls == 0


def test_a_mapped_version_without_a_frozen_template_is_refused(monkeypatch):
    monkeypatch.setitem(pa.PROMPT_VERSION_BY_CONFIG_VERSION, "cv-without-template", "template-that-was-never-archived")
    rec = _record(CASES[0][1], CASES[0][2])
    rec["config_version"] = "cv-without-template"
    with pytest.raises(UnknownPromptVersion, match="no frozen template"):
        assert_record_intact(rec)


# ------------------------------------------------------------------ the drift guard fires
def _problems_containing(text: str) -> list[str]:
    return [p for p in pa.drift_problems() if text in p]


def test_drift_guard_fires_when_prompts_py_changes_without_a_version_bump(monkeypatch, tmp_path):
    src = live_prompts.__file__
    edited = tmp_path / "prompts.py"
    edited.write_text(open(src, encoding="utf-8").read().replace('"Probability: ZZ%"', '"Probability (YES): ZZ%"'),
                      encoding="utf-8")
    real = live_prompts.forecast_prompt
    fake = SimpleNamespace(__file__=str(edited),
                           forecast_prompt=lambda q, r, t: real(q, r, t).replace('"Probability: ZZ%"', '"Probability (YES): ZZ%"'))
    monkeypatch.setattr(pa, "_prompts", fake)
    assert _problems_containing("changed but prompt_version is still")
    assert _problems_containing("doesn't render what the live code renders")


def test_drift_guard_fires_on_a_source_change_alone(monkeypatch, tmp_path):
    """A change no fixture exercises (here: only a comment) is still caught by the fingerprint."""
    edited = tmp_path / "prompts.py"
    edited.write_text(open(live_prompts.__file__, encoding="utf-8").read() + "\n# a new branch nobody tested\n", encoding="utf-8")
    monkeypatch.setattr(pa, "_prompts", SimpleNamespace(__file__=str(edited), forecast_prompt=live_prompts.forecast_prompt))
    assert _problems_containing("changed but prompt_version is still")


def test_drift_guard_fires_on_an_output_change_alone(monkeypatch):
    real = live_prompts.forecast_prompt
    monkeypatch.setattr(live_prompts, "forecast_prompt", lambda q, r, t: real(q, r, t).replace("Today is", "Today's date is"))
    assert len(_problems_containing("doesn't render what the live code renders")) == len(CASES)


def test_drift_guard_fires_when_research_rendering_changes(monkeypatch):
    real = ResearchBundle.as_prompt_text
    monkeypatch.setattr(ResearchBundle, "as_prompt_text", lambda self: real(self).replace("\n\n", "\n---\n"))
    assert _problems_containing("changed but prompt_version is still")
    assert _problems_containing("doesn't render what the live code renders")


def _config_with_prompt_version(version: str, config_version: str = CONFIG_VERSION) -> SimpleNamespace:
    def bump(c):
        return replace(c, prompt_version=version)

    return SimpleNamespace(
        DEFAULT_CONFIG=bump(live_config.DEFAULT_CONFIG), TOURNAMENT_CONFIG=bump(live_config.TOURNAMENT_CONFIG),
        WIDE_CONFIG=bump(live_config.WIDE_CONFIG), CONFIG_VERSION=config_version,
        SHADOW_CONFIGS={k: bump(v) for k, v in live_config.SHADOW_CONFIGS.items()},
        EXP008_ARMS={k: bump(v) for k, v in live_config.EXP008_ARMS.items()},
    )


def test_drift_guard_fires_when_a_new_version_has_no_archive_entry(monkeypatch):
    monkeypatch.setattr(pa, "_config", _config_with_prompt_version("template-2026-11-15", "frontier-2026-11-15"))
    assert _problems_containing("has no frozen template in the archive")
    assert _problems_containing("maps to None")


def test_drift_guard_fires_when_the_version_is_bumped_without_a_new_config_version(monkeypatch):
    monkeypatch.setattr(pa, "_config", _config_with_prompt_version("test-v2"))
    monkeypatch.setitem(pa.ARCHIVE, "test-v2", pa.ArchivedTemplate("test-v2", _new_template_module(), "-", "-"))
    assert _problems_containing("a prompt change needs a new CONFIG_VERSION")


def test_drift_guard_fires_when_configs_disagree_on_the_prompt_version(monkeypatch):
    cfg = _config_with_prompt_version(LIVE_VERSION)
    cfg.SHADOW_CONFIGS = {"cheap": replace(live_config.DEFAULT_CONFIG, prompt_version="other")}
    monkeypatch.setattr(pa, "_config", cfg)
    assert _problems_containing("SHADOW_CONFIGS['cheap'].prompt_version is 'other'")


def test_drift_guard_fires_when_an_archived_template_is_edited(monkeypatch):
    t = pa.ARCHIVE[LIVE_VERSION]
    monkeypatch.setitem(pa.ARCHIVE, LIVE_VERSION, replace(t, file_sha256="0" * 64))
    assert _problems_containing("was edited")


def test_archive_is_append_only_and_frozen_files_are_pinned():
    for cv, version in PINNED_CONFIG_VERSIONS.items():
        assert pa.PROMPT_VERSION_BY_CONFIG_VERSION.get(cv) == version, f"{cv} was remapped or removed; older records need it"
    for version, sha in PINNED_FROZEN_FILES.items():
        t = pa.ARCHIVE[version]
        assert t.file_sha256 == sha == pa.file_sha256(t.module), f"the frozen template {version} was edited"


def test_live_configs_and_hashes_unchanged():
    """This PR changes no live code: CONFIG_VERSION and the live config hashes are what they were on main."""
    assert CONFIG_VERSION == "frontier-2026-10-08"
    assert (live_config.DEFAULT_CONFIG.config_hash(), live_config.TOURNAMENT_CONFIG.config_hash(),
            live_config.WIDE_CONFIG.config_hash()) == ("c18ebdd3d1ef", "11fb02f5ac67", "9785b336fd21")


# ------------------------------------------------------------------ `evals.replay --check`
def _write(runs, name, rec):
    d = runs / "1" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / f"q{rec['question']['question_id']}.json").write_text(json.dumps(rec), encoding="utf-8")


def test_check_cli_reports_per_version_and_refuses_unknown_versions(tmp_path, capsys):
    runs = tmp_path / "runs"
    for _, q, items, as_of in CASES:
        _write(runs, "20250314T120000Z-tournament", _record(q, items, as_of))
    assert replay.main(["--check", "--runs-dir", str(runs)]) == 0
    out = capsys.readouterr().out
    assert f"{len(CASES)}/{len(CASES)} archived live records reproduce" in out and f"{CONFIG_VERSION} -> {LIVE_VERSION}" in out
    assert "archive drift guard: PASS" in out

    unknown = _record(CASES[0][1], CASES[0][2])
    unknown["config_version"] = "never-existed"
    unknown["question"]["question_id"] = 77
    _write(runs, "20250315T120000Z-tournament", unknown)
    tampered = _tamper(_record(CASES[1][1], CASES[1][2], CASES[1][3]))
    tampered["question"]["question_id"] = 78
    _write(runs, "20250315T120000Z-tournament", tampered)
    assert replay.main(["--check", "--runs-dir", str(runs)]) == 1
    out = capsys.readouterr().out
    assert f"{len(CASES)}/{len(CASES) + 2} archived live records reproduce" in out
    assert "MISMATCH 1/20250315T120000Z-tournament/q78.json" in out
    assert "REFUSED 1 record(s) with config_version 'never-existed'" in out and f"{LIVE_VERSION} 1/1" in out
    assert "Made-up" not in out and "after the fact" not in out  # never prints research text


def test_harness_archive_check_fails_on_drift(monkeypatch, tmp_path):
    real = live_prompts.forecast_prompt
    monkeypatch.setattr(live_prompts, "forecast_prompt", lambda q, r, t: real(q, r, t) + "x")
    res = vh.check_reproducibility_offline(tmp_path)
    assert any("prompt archive" in r.headline for r in res if r.status == vh.FAIL)


def test_check_cli_says_so_when_there_are_no_records(tmp_path, capsys):
    assert replay.main(["--check", "--runs-dir", str(tmp_path / "missing")]) == 2
    assert "NO LIVE RECORDS" in capsys.readouterr().out
