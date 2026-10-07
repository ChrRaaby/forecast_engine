"""Season spend cap for the frontier tournament ensemble (ADR-0009, B-16).

Christian set $300 for Fall 2026 tournament forecasting. Every live tournament forecast made with the frontier config adds its full
cost (research, forecasters, polarity check; failed forecasts too) to a per-season total. Once the total reaches the cap, tournament
questions fall back to the cheap config instead of going silent. Shadow forecasts (B-62) don't count: they are the cheap config.

Like the retry ledger (retries.py), the total is a small JSON file in state/ that survives between GitHub Actions runs through
actions/cache. If the cache is lost the total restarts at 0, so the OpenRouter key limit stays the hard backstop.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path("state/season_spend.json")


class SeasonSpend:
    def __init__(self, season: str, cap_usd: float, path: Path | str = DEFAULT_PATH) -> None:
        self.season, self.cap_usd, self.path = season, cap_usd, Path(path)
        self.spent_usd = 0.0
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("season") == season:
                self.spent_usd = float(data.get("spent_usd", 0.0))
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, AttributeError) as e:
            logger.warning(f"Season spend file unreadable ({e}); starting at $0 (the OpenRouter key limit still applies)")

    @property
    def exhausted(self) -> bool:
        return self.spent_usd >= self.cap_usd

    def add(self, usd: float) -> None:
        self.spent_usd += max(0.0, usd)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"season": self.season, "cap_usd": self.cap_usd, "spent_usd": round(self.spent_usd, 6)}),
                             encoding="utf-8")
