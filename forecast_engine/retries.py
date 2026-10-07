"""Retry cap for questions whose forecast keeps failing (B-61).

Every run forecasts each eligible question that the bot hasn't forecast yet, so a question whose forecast fails is retried
on every 10-minute run. On 2026-10-06 MiniBench 46123 failed 19 times in 3 hours ($0.14, ~15% of spend to date) before
anyone noticed. This ledger counts failures per question: after MAX_FAILURES the question is skipped for COOLDOWN, then
gets MAX_FAILURES fresh tries. A new CONFIG_VERSION (a fix) resets every count, and a success clears the question.

Only forecasting failures count (all members failed to parse, a forecast Metaculus can't take): they cost money and repeat
deterministically. Publishing errors, closed questions and the run cost cap don't count. A provider outage would also fail
every forecast; the cooldown bounds that to a few hours' pause instead of a permanent skip.

The ledger is a small JSON file. In GitHub Actions it survives between runs through actions/cache (restore the newest,
save under a new key), see .github/workflows/run_bot_on_tournament.yaml. A missing or unreadable file means no history.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_FAILURES = 3
COOLDOWN = timedelta(hours=12)
DEFAULT_PATH = Path("state/retries.json")


class RetryLedger:
    def __init__(self, config_version: str, path: Path | str = DEFAULT_PATH) -> None:
        self.config_version = config_version
        self.path = Path(path)
        self.entries: dict[str, dict] = {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("config_version") == config_version:
                self.entries = data.get("questions", {})
            else:
                logger.info(f"Retry ledger is for {data.get('config_version')}, now {config_version}: counts reset")
        except FileNotFoundError:
            pass
        except (OSError, ValueError, AttributeError) as e:
            logger.warning(f"Retry ledger unreadable ({e}); starting empty")

    def blocked(self, question_id: int, now: datetime) -> str | None:
        """Why the question is skipped this run, or None if it may be forecast."""
        e = self.entries.get(str(question_id))
        if not e or e["failures"] < MAX_FAILURES:
            return None
        until = datetime.fromisoformat(e["last_failure"]) + COOLDOWN
        if now >= until:
            return None
        return f"failed {e['failures']} times under this config; retry after {until:%Y-%m-%d %H:%M} UTC"

    def failed(self, question_id: int, now: datetime) -> None:
        e = self.entries.get(str(question_id))
        if e and e["failures"] >= MAX_FAILURES:  # cooldown over: a fresh set of tries
            e = None
        e = e or {"failures": 0}
        e["failures"] += 1
        e["last_failure"] = now.isoformat(timespec="seconds")
        self.entries[str(question_id)] = e

    def succeeded(self, question_id: int) -> None:
        self.entries.pop(str(question_id), None)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"config_version": self.config_version, "questions": self.entries}
        self.path.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
