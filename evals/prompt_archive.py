"""Prompt archive (F-13 replay trap): a frozen copy of every forecast-prompt template a live record can carry, and which one made
which record.

Two different prompts in a replay, never mixed up:

- INTEGRITY CHECK (`evals.replay.check_record` / `assert_record_intact`): to prove a stored record's question and research are
  unchanged since its as_of, rebuild the prompt with the template that was live when the record was made (this archive) and
  compare it byte for byte with what the models received. Before this archive the check rebuilt with today's
  `forecast_engine/prompts.py`, so the first prompt change would have made every older record unreplayable.
- NEW FORECAST (`evals.replay.replay_record` -> `forecast_engine.core.forecast`): an experiment re-forecasts the stored question
  with today's code. Nothing here is ever sent to a model.

How a record's prompt version is found. Records don't store `BotConfig.prompt_version`; they store `config_version` (the
`CONFIG_VERSION` the code had) and `config_hash`. `forecast()` builds every config's prompt from the one `prompts.py`, whatever
`cfg.prompt_version` says, so the template is fixed by the code version, and `config.py` already requires a `CONFIG_VERSION`
bump for any change that alters forecasts. `PROMPT_VERSION_BY_CONFIG_VERSION` maps one to the other. A config version or prompt
version not in the tables is refused (never rebuilt with the current code, never guessed from a date).

The drift guard (`drift_problems()`, run by the tests, `evals.replay --check` and `evals.validate_harness`) keeps the archive
honest: the archived template for the live prompt version must render exactly what the live code renders; the live prompt code
(prompts.py, `ResearchBundle.as_prompt_text`, `today_str`) must hash to the fingerprint stored for the live version, so any edit
needs a new `prompt_version`; a new version needs an archive entry and a new `CONFIG_VERSION`; archived copies can't be edited.

Changing the live prompt (e.g. EXP-002 going live), in one commit:
  1. edit forecast_engine/prompts.py; bump `BotConfig.prompt_version` and `CONFIG_VERSION` in config.py;
  2. copy the new builder to a new frozen file `evals/frozen_prompts_<version>.py` (same shape as the existing one);
  3. add it to ARCHIVE below with its file hash and `live_fingerprint()`, and add the new CONFIG_VERSION to
     PROMPT_VERSION_BY_CONFIG_VERSION. Never change or remove an existing line: older records still need it.
`poetry run pytest tests/test_prompt_archive.py` prints the hashes to paste when they don't match.

Follow-up research (B-17, `FollowupConfig.prompt_version`) needs no entry: the critic and search prompts are not part of the
forecast prompt; what they found is stored in the record as research items, and the check rebuilds from those stored items.
"""
from __future__ import annotations

import hashlib
import inspect
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

from forecast_engine import clock as _clock
from forecast_engine import config as _config
from forecast_engine import prompts as _prompts
from forecast_engine.schema import QuestionSnapshot, ResearchBundle, ResearchItem

from . import frozen_prompts_template_2026_09_26 as _t2026_09_26


@dataclass(frozen=True)
class ArchivedTemplate:
    version: str  # BotConfig.prompt_version
    module: ModuleType  # the frozen copy; build(q, items, as_of) -> prompt
    file_sha256: str  # hash of the frozen file (LF line endings), pinned so an archived template can't be edited
    live_sha256: str  # live_fingerprint() while this version was the live prompt

    def build(self, q: QuestionSnapshot, items: Sequence[Any], as_of: datetime) -> str:
        return self.module.build(q, items, as_of)


ARCHIVE: dict[str, ArchivedTemplate] = {t.version: t for t in (
    ArchivedTemplate(
        "template-2026-09-26", _t2026_09_26,
        file_sha256="e79eb2be55c1a48772c5f4fd854eda498f43b6bad3981fb62cf16bd7e4443c9c",
        live_sha256="52f6832b910b6662bc4fff9d7eeca5a077630d1eed478e3eb92226bc58e7325e",
    ),
)}

# config_version stored in a record -> the prompt version its prompts were built with. Append only.
PROMPT_VERSION_BY_CONFIG_VERSION: dict[str, str] = {
    # Before the repo's git history (it starts 2026-10-04). Named in docs/log, research/03, research/09 and the playbook tracker.
    # Evidence that they used this template: the B-32 PC check on 2026-10-26 rebuilt the prompt of every archived live record
    # (401/401) with prompts.py as it is today, and prompts.py has not changed since. `evals.replay --check` re-proves it per
    # version on the real data: a wrong line here can only refuse records, never let a changed one through.
    "m1-baseline-2026-09-30b": "template-2026-09-26",
    "m1-baseline-2026-09-30c": "template-2026-09-26",
    "m1-baseline-2026-10-01": "template-2026-09-26",
    "m1-baseline-2026-10-03": "template-2026-09-26",
    # In git history: every commit of config.py has prompt_version "template-2026-09-26", and prompts.py has one version.
    "m1-baseline-2026-10-03b": "template-2026-09-26",
    "m1-baseline-2026-10-05": "template-2026-09-26",
    "m1-baseline-2026-10-06": "template-2026-09-26",
    "frontier-2026-10-08": "template-2026-09-26",
}


def prompt_version_for(config_version: str | None) -> str:
    """The prompt version records with this config_version were built with. KeyError (with the reason) if it isn't archived."""
    if config_version not in PROMPT_VERSION_BY_CONFIG_VERSION:
        raise KeyError(f"config_version {config_version!r} has no prompt version in evals/prompt_archive.py")
    version = PROMPT_VERSION_BY_CONFIG_VERSION[config_version]
    if version not in ARCHIVE:
        raise KeyError(f"prompt version {version!r} (config_version {config_version!r}) has no frozen template in the archive")
    return version


def template_for(version: str) -> ArchivedTemplate:
    if version not in ARCHIVE:
        raise KeyError(f"prompt version {version!r} has no frozen template in the archive")
    return ARCHIVE[version]


# --------------------------------------------------------------------------- drift guard
def _lf(text: str) -> str:
    return text.replace("\r\n", "\n")


def _sha(text: str) -> str:
    return hashlib.sha256(_lf(text).encode("utf-8")).hexdigest()


def file_sha256(module: ModuleType) -> str:
    return _sha(Path(module.__file__).read_text(encoding="utf-8"))


def live_prompt_sources() -> list[str]:
    """The live code that shapes the forecast prompt's text: all of prompts.py, plus how research and "today" are rendered."""
    return [
        Path(_prompts.__file__).read_text(encoding="utf-8"),
        inspect.getsource(ResearchBundle.as_prompt_text),
        inspect.getsource(ResearchBundle.is_empty.fget),
        inspect.getsource(_clock.today_str),
    ]


def live_fingerprint() -> str:
    return _sha("\n\0\n".join(_lf(s) for s in live_prompt_sources()))


def live_prompt(q: QuestionSnapshot, items: Sequence[ResearchItem], as_of: datetime) -> str:
    """What the live code sends for these inputs (the same calls as forecast_engine.core.forecast)."""
    bundle = ResearchBundle(as_of=as_of, providers=[], items=list(items), calls=[])
    return _prompts.forecast_prompt(q, bundle.as_prompt_text(), _clock.today_str(_clock.FixedClock(as_of)))


def live_configs() -> dict[str, _config.BotConfig]:
    out = {"DEFAULT_CONFIG": _config.DEFAULT_CONFIG, "TOURNAMENT_CONFIG": _config.TOURNAMENT_CONFIG,
           "WIDE_CONFIG": _config.WIDE_CONFIG}
    out.update({f"SHADOW_CONFIGS[{k!r}]": v for k, v in _config.SHADOW_CONFIGS.items()})
    out.update({f"EXP008_ARMS[{k!r}]": v for k, v in _config.EXP008_ARMS.items()})
    return out


def fixture_cases() -> list[tuple[str, QuestionSnapshot, list[ResearchItem], datetime]]:
    """Synthetic inputs covering every branch of the prompt code (question types, bounds, empty research, titles, time zones)."""
    utc = timezone.utc
    west = timezone(timedelta(hours=-5))  # 23:30 local is already the next day in UTC: "today" must follow the as_of's own zone
    items = [
        ResearchItem(source="asknews_latest", text="  Made-up article about the topic.\n", title="Made-up title",
                     published_at=datetime(2025, 3, 13, tzinfo=utc)),
        ResearchItem(source="gemini_grounded", text="Made-up briefing.\n\nSecond paragraph.", title=None),
        ResearchItem(source="research_followup", text="Made-up follow-up section.", title="Follow-up search 1: made-up query"),
    ]
    blank = [ResearchItem(source="asknews_latest", text="   \n", title="Empty")]
    as_of = datetime(2025, 3, 14, 12, 0, tzinfo=utc)
    late = datetime(2025, 3, 14, 23, 30, tzinfo=west)
    base = dict(post_id=1, background_info="Made-up background.", resolution_criteria="Made-up criteria.",
                fine_print="Made-up fine print.")
    return [
        ("binary", QuestionSnapshot(question_id=1, question_type="binary", question_text="Will the made-up event happen?",
                                    **base), items, as_of),
        ("binary, no research", QuestionSnapshot(question_id=2, post_id=None, question_type="binary", question_text="Will X happen?"),
         [], late),
        ("binary, blank research", QuestionSnapshot(question_id=3, question_type="binary", question_text="Will Y happen?",
                                                    **base), blank, as_of),
        ("multiple choice", QuestionSnapshot(question_id=4, question_type="multiple_choice", question_text="Which option?",
                                             options=("Option A", "Option 'B'", "C"), **base), items, as_of),
        ("numeric, open bounds", QuestionSnapshot(question_id=5, question_type="numeric", question_text="How many?",
                                                  unit_of_measure="widgets", lower_bound=0.0, upper_bound=1000.0,
                                                  open_lower_bound=True, open_upper_bound=True, **base), items, late),
        ("numeric, nominal bounds, no unit", QuestionSnapshot(
            question_id=6, question_type="numeric", question_text="What value?", lower_bound=-10.0, upper_bound=10.0,
            nominal_lower_bound=-5.0, nominal_upper_bound=5.0, **base), items[:1], as_of),
        ("discrete", QuestionSnapshot(question_id=7, question_type="discrete", question_text="How many times?",
                                      unit_of_measure="events", lower_bound=0, upper_bound=20, open_upper_bound=True,
                                      **base), items[1:], as_of),
    ]


def drift_problems() -> list[str]:
    """Everything that would let the archive and the live prompt drift apart; empty when all is well. Never prints prompts."""
    problems: list[str] = []
    current = _config.DEFAULT_CONFIG.prompt_version
    for name, cfg in live_configs().items():
        if cfg.prompt_version != current:
            problems.append(
                f"{name}.prompt_version is {cfg.prompt_version!r}, DEFAULT_CONFIG's is {current!r}; forecast() builds every "
                "config's prompt from the one prompts.py, so records can't tell them apart (record the prompt version first)")
    for config_version, version in PROMPT_VERSION_BY_CONFIG_VERSION.items():
        if version not in ARCHIVE:
            problems.append(f"config_version {config_version!r} maps to {version!r}, which has no frozen template")
    for t in ARCHIVE.values():
        got = file_sha256(t.module)
        if got != t.file_sha256:
            problems.append(f"frozen template {t.version!r} ({Path(t.module.__file__).name}) was edited: hash {got}, archived "
                            f"{t.file_sha256}. Archived templates must never change; put a new version in a new file")
        if getattr(t.module, "VERSION", None) != t.version:
            problems.append(f"{Path(t.module.__file__).name} says VERSION {getattr(t.module, 'VERSION', None)!r}, "
                            f"archived as {t.version!r}")
    mapped = PROMPT_VERSION_BY_CONFIG_VERSION.get(_config.CONFIG_VERSION)
    if mapped != current:
        problems.append(f"CONFIG_VERSION {_config.CONFIG_VERSION!r} maps to {mapped!r} in PROMPT_VERSION_BY_CONFIG_VERSION, "
                        f"but the live prompt_version is {current!r}: a prompt change needs a new CONFIG_VERSION, added there")
    if current not in ARCHIVE:
        problems.append(f"live prompt_version {current!r} has no frozen template in the archive (add one; see this module's "
                        f"docstring). live_fingerprint() = {live_fingerprint()}")
        return problems
    t = ARCHIVE[current]
    fp = live_fingerprint()
    if fp != t.live_sha256:
        problems.append(f"the live prompt code (prompts.py, ResearchBundle.as_prompt_text, today_str) changed but prompt_version "
                        f"is still {current!r}: bump prompt_version and CONFIG_VERSION and archive the new template "
                        f"(fingerprint now {fp}, archived {t.live_sha256})")
    for name, q, items, as_of in fixture_cases():
        try:
            same = t.build(q, items, as_of) == live_prompt(q, items, as_of)
        except Exception as e:  # noqa: BLE001 - a crash is drift too
            problems.append(f"{name}: crashed ({type(e).__name__}: {e})")
            continue
        if not same:
            problems.append(f"{name}: the archived template {current!r} doesn't render what the live code renders")
    return problems
