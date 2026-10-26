"""Replay (B-38): rerun forecast() on a stored record's frozen research, as of the record's own time, with any config.

Every live forecast stores its question snapshot, as-of time and research bundle. Replaying with a different config (prompt,
roster, aggregation) on the same inputs is a leakage-free paired comparison once the question resolves: the research and the
question text are exactly what was available when the live forecast was made (ADR-0005, protocol §7 research cache).

    poetry run python -m evals.replay --check     # offline: every live record's prompts are reproduced exactly from its inputs

`replay_record(record, cfg, llm)` is the API the experiment runner (B-30) will call. Replaying needs the same research text, so it
works on records from the private data repo only. Before any model call it runs `assert_record_as_of` (B-32, threat T2): a record
whose frozen research holds an item dated after the record's as_of, or a bundle made after it, is refused.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from forecast_engine.clock import FixedClock, today_str
from forecast_engine.config import BotConfig
from forecast_engine.core import forecast
from forecast_engine.llm import LlmClient
from forecast_engine.prompts import forecast_prompt
from forecast_engine.schema import ForecastRecord, QuestionSnapshot, ResearchBundle

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


def reproduced_prompt(record: dict[str, Any]) -> str:
    q, clock, bundle = inputs_from_record(record)
    return forecast_prompt(q, bundle.as_prompt_text(), today_str(clock))


def check_record(record: dict[str, Any]) -> bool:
    """True if every forecaster's stored prompt equals the prompt rebuilt from the stored inputs."""
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


async def replay_record(record: dict[str, Any], cfg: BotConfig, llm: LlmClient) -> ForecastRecord:
    """Re-forecast a stored question on its frozen research, as of its own time, with `cfg`. Refuses (LeakageError, no model
    call) a record whose research is dated after its as_of."""
    assert_record_as_of(record)
    q, clock, bundle = inputs_from_record(record)
    return await forecast(q, clock, bundle, cfg, llm)


def live_records(runs_dir: Path = RUNS) -> list[tuple[Path, dict[str, Any]]]:
    out = []
    for path in sorted(runs_dir.rglob("q*.json")):
        if "test_questions" in path.parent.name:
            continue
        out.append((path, json.loads(path.read_text(encoding="utf-8"))))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="verify prompt reproduction on every live record (offline)")
    args = ap.parse_args()
    if not args.check:
        ap.print_help()
        return 0
    bad = []
    recs = live_records()
    for path, rec in recs:
        if not check_record(rec):
            bad.append(path)
    print(f"{len(recs) - len(bad)}/{len(recs)} live records reproduce their prompts exactly")
    for p in bad[:20]:
        print("  MISMATCH", p)
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
