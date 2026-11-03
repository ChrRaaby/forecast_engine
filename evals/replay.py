"""Replay (B-38): rerun forecast() on a stored record's frozen research, as of the record's own time, with any config.

Every live forecast stores its question snapshot, as-of time and research bundle. Replaying with a different config (prompt,
roster, aggregation) on the same inputs is a leakage-free paired comparison once the question resolves: the research and the
question text are exactly what was available when the live forecast was made (ADR-0005, protocol §7 research cache).

    poetry run python -m evals.replay --check     # offline: every live record's prompts are reproduced exactly from its inputs
    poetry run python -m evals.replay --check --runs-dir <path>/data/runs   # the same on records in another checkout

`replay_record(record, cfg, llm)` is the API the experiment runner (B-30) will call. Replaying needs the same research text, so it
works on records from the private data repo only. Before any model call it runs two guards (B-32): `assert_record_as_of` (T2)
refuses a record whose frozen research holds an item dated after the record's as_of, or a bundle made after it; `assert_record_intact`
refuses a record whose stored inputs no longer rebuild the prompt the live models received (`check_record`), so the research
replayed is provably what the live forecaster saw at as_of.

Two prompts, kept apart (F-13 replay trap):
- the INTEGRITY CHECK (`reproduced_prompt`, `check_record`, `assert_record_intact`) rebuilds the record's prompt with the frozen
  template that made it (`evals.prompt_archive`, chosen by the record's config_version), never with today's prompts.py, so old
  records stay checkable after a prompt change. A record whose version isn't archived is refused (`UnknownPromptVersion`).
- the NEW FORECAST (`replay_record` -> `forecast_engine.core.forecast`) is built by today's code for the config being tested.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forecast_engine.clock import FixedClock, today_str
from forecast_engine.config import BotConfig
from forecast_engine.core import forecast
from forecast_engine.llm import LlmClient
from forecast_engine.schema import ForecastRecord, QuestionSnapshot, ResearchBundle

from . import prompt_archive
from .asof import LeakageError
from .research_cache import bundle_from_dict

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "data" / "runs"

_SNAPSHOT_FIELDS = {f.name for f in fields(QuestionSnapshot)}
_DATETIME_FIELDS = {"open_time", "scheduled_resolution_time"}


def snapshot_from_dict(d: dict[str, Any]) -> QuestionSnapshot:
    """Inverse of QuestionSnapshot.to_dict(). Fields added after a record was written take their defaults."""
    kwargs: dict[str, Any] = {}
    for k, v in d.items():
        if k not in _SNAPSHOT_FIELDS:
            continue
        if k in _DATETIME_FIELDS and isinstance(v, str):
            v = datetime.fromisoformat(v)
        if k in ("options", "tournaments") and isinstance(v, list):
            v = tuple(v)
        kwargs[k] = v
    return QuestionSnapshot(**kwargs)


def inputs_from_record(record: dict[str, Any]) -> tuple[QuestionSnapshot, FixedClock, ResearchBundle]:
    q = snapshot_from_dict(record["question"])
    as_of = datetime.fromisoformat(record["as_of"])
    return q, FixedClock(as_of), bundle_from_dict(record["research"])


class TamperedRecord(LeakageError):
    """The record's stored inputs don't rebuild the prompt the live models received: its research or question changed later."""


class UnknownPromptVersion(TamperedRecord):
    """The record's prompt template isn't in `evals.prompt_archive`, so its integrity can't be checked; refused, never rebuilt
    with the current prompts.py."""


def record_prompt_version(record: dict[str, Any]) -> str:
    """INTEGRITY CHECK ONLY: the prompt version the live code built this record's prompt with. Records from 2026-11 on store it
    (`prompt_version`, B-75(1)); older ones don't, and it follows from their config_version (`evals.prompt_archive`). Raises
    UnknownPromptVersion."""
    try:
        stored = record.get("prompt_version")
        if stored is not None:
            return prompt_archive.template_for(stored).version
        return prompt_archive.prompt_version_for(record.get("config_version"))
    except KeyError as e:
        raise UnknownPromptVersion(f"{e.args[0]}; not checkable, so not replayable") from None


def reproduced_prompt(record: dict[str, Any]) -> str:
    """INTEGRITY CHECK ONLY: the prompt rebuilt from the record's stored question, as_of and research, with the frozen template
    that made the record. Never sent to a model; an experiment's new prompt comes from `replay_record` (today's code)."""
    template = prompt_archive.template_for(record_prompt_version(record))
    q, clock, bundle = inputs_from_record(record)
    return template.build(q, bundle.items, clock.now())


def check_record(record: dict[str, Any]) -> bool:
    """True if every forecaster's stored prompt equals the prompt rebuilt from the stored inputs (byte for byte), using the
    template of the record's own prompt version. Raises UnknownPromptVersion if that version isn't archived."""
    prompt = reproduced_prompt(record)
    stored = [f["call"]["prompt"] for f in record["forecasters"]]
    return bool(stored) and all(p == prompt for p in stored)


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def assert_record_as_of(record: dict[str, Any]) -> None:
    """Raise LeakageError if the record's frozen research could not all have existed at the record's as_of (T2; B-32).

    Refused: a research item dated after as_of, and a bundle whose own as_of is after the record's. Unlike the backtest guard
    (`evals.asof.assert_bundle_as_of`), undated items are allowed: live research was captured at as_of by construction (the
    record's as_of is stamped after research), and Gemini-grounded items carry no date at all. A date without a time zone is read
    as UTC. The error names counts and dates only, never research text."""
    as_of = datetime.fromisoformat(record["as_of"])
    if as_of.tzinfo is None:
        raise LeakageError("record as_of has no time zone; can't check its research")
    research = record["research"]
    if research.get("as_of") and _utc(datetime.fromisoformat(research["as_of"])) > as_of:
        raise LeakageError(f"research bundle is as of {research['as_of']}, after the record's as_of {record['as_of']}")
    late = sorted(
        i["published_at"] for i in research.get("items", [])
        if i.get("published_at") and _utc(datetime.fromisoformat(i["published_at"])) > as_of
    )
    if late:
        raise LeakageError(f"{len(late)} research item(s) dated after the record's as_of {record['as_of']}: {', '.join(late[:5])}")


def assert_record_intact(record: dict[str, Any]) -> None:
    """Raise TamperedRecord unless `check_record` passes (B-32, agreed with Christian 2026-10-26).

    This is the strict part of the replay guard. Live research was captured at as_of, so what has to be proven is that it is
    unchanged since: every forecaster's stored prompt must equal the prompt rebuilt from the stored question, as_of and research.
    Publication dates can't prove that (Gemini items have none, and an edit can keep a date). The rebuild uses the template the
    record was made with (`evals.prompt_archive`); a record whose template isn't archived raises UnknownPromptVersion."""
    try:
        intact = check_record(record)
    except (KeyError, TypeError, ValueError) as e:
        raise TamperedRecord(f"record can't be checked ({type(e).__name__}: {e})") from e
    if not intact:
        raise TamperedRecord("stored inputs don't rebuild the prompt the live models received (research or question changed "
                             "after as_of); not replayable")


async def replay_record(record: dict[str, Any], cfg: BotConfig, llm: LlmClient) -> ForecastRecord:
    """Re-forecast a stored question on its frozen research, as of its own time, with `cfg`. Refuses (LeakageError, no model
    call) a record whose research is dated after its as_of, or (TamperedRecord) whose inputs changed since the live forecast.

    The integrity check uses the record's own (archived) prompt template; the new forecast is built by today's code
    (`forecast_engine.core.forecast`), so a record made before a prompt change is re-forecast with the new prompt. Note that
    forecast() builds the prompt from prompts.py, whatever `cfg.prompt_version` says."""
    assert_record_as_of(record)
    assert_record_intact(record)
    q, clock, bundle = inputs_from_record(record)
    return await forecast(q, clock, bundle, cfg, llm)


def live_records(runs_dir: Path = RUNS) -> list[tuple[Path, dict[str, Any]]]:
    out = []
    for path in sorted(runs_dir.rglob("q*.json")):
        if "test_questions" in path.parent.name:
            continue
        out.append((path, json.loads(path.read_text(encoding="utf-8"))))
    return out


def check_archive(recs: list[tuple[Path, dict[str, Any]]], root: Path | None = None) -> tuple[list[str], int]:
    """`--check` on these records: (lines to print, number of problems). Counts, versions and file paths only, never text.
    Paths are shown relative to `root` when given."""

    def show(p: Path) -> str:
        try:
            return p.relative_to(root).as_posix() if root else str(p)
        except ValueError:
            return p.name

    by_version: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # config_version -> [intact, total]
    mismatch: list[Path] = []
    unknown: dict[str, list[dict[str, Any]]] = defaultdict(list)
    broken: list[tuple[Path, str]] = []
    for path, rec in recs:
        cv = str(rec.get("config_version"))
        try:
            ok = check_record(rec)
        except UnknownPromptVersion:
            unknown[cv].append(rec)
            continue
        except (KeyError, TypeError, ValueError) as e:
            broken.append((path, type(e).__name__))
            continue
        by_version[cv][1] += 1
        if ok:
            by_version[cv][0] += 1
        else:
            mismatch.append(path)
    n_ok = sum(v[0] for v in by_version.values())
    lines = [f"{n_ok}/{len(recs)} archived live records reproduce their prompts exactly (each rebuilt with the archived "
             "template of its own prompt version)"]
    for cv in sorted(by_version):
        ok, n = by_version[cv]
        lines.append(f"  {cv} -> {prompt_archive.PROMPT_VERSION_BY_CONFIG_VERSION[cv]}: {ok}/{n}")
    for p in mismatch[:20]:
        lines.append(f"  MISMATCH {show(p)}")
    for p, why in broken[:20]:
        lines.append(f"  CAN'T CHECK ({why}) {show(p)}")
    for cv, rs in sorted(unknown.items()):
        hits = {v: sum(_rebuilds_with(r, v) for r in rs) for v in prompt_archive.ARCHIVE}
        lines.append(f"  REFUSED {len(rs)} record(s) with config_version {cv!r}: no prompt version archived for it. Diagnosis only "
                     f"(not counted as intact): would rebuild exactly with {', '.join(f'{v} {k}/{len(rs)}' for v, k in hits.items())}")
    drift = prompt_archive.drift_problems()
    lines.append("archive drift guard: " + ("PASS" if not drift else f"FAIL ({len(drift)} problem(s))"))
    lines.extend(f"  {d}" for d in drift)
    return lines, len(mismatch) + len(broken) + sum(len(rs) for rs in unknown.values()) + len(drift)


def _rebuilds_with(record: dict[str, Any], version: str) -> bool:
    try:
        q, clock, bundle = inputs_from_record(record)
        prompt = prompt_archive.template_for(version).build(q, bundle.items, clock.now())
        stored = [f["call"]["prompt"] for f in record["forecasters"]]
    except (KeyError, TypeError, ValueError):
        return False
    return bool(stored) and all(p == prompt for p in stored)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="verify prompt reproduction on every live record (offline)")
    ap.add_argument("--runs-dir", default=str(RUNS), help="archived run records (default: data/runs in this checkout)")
    args = ap.parse_args(argv)
    if not args.check:
        ap.print_help()
        return 0
    runs = Path(args.runs_dir)
    recs = live_records(runs) if runs.exists() else []
    if not recs:
        print(f"NO LIVE RECORDS found under {runs}: nothing was checked (wrong --runs-dir?)")
        return 2
    lines, problems = check_archive(recs, runs)
    print("\n".join(lines))
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
