"""Season spend cap for the frontier tournament ensemble (ADR-0009, B-16; B-85).

Christian set $300 for Fall 2026 tournament forecasting. Every live tournament forecast made with the frontier config adds its full
cost (research, forecasters, polarity check; failed forecasts too) to a per-season total. Once the total reaches the cap, tournament
questions fall back to the cheap config instead of going silent. Shadow forecasts (B-62) don't count.

B-85 (Christian, 2026-11-14, option 2): the published forecasts run on their own OpenRouter key ("live", OPENROUTER_LIVE_KEY);
every shadow and experiment stays on the old key. At start-up the season total is read from OpenRouter itself:
    spent = spend before the live key existed (config) + the live key's usage
so it can't be lost the way the cache-held counter was (reset 30->31 Oct). During the run it is updated as before. The JSON file in
state/ (kept through actions/cache) is now only the fallback when OpenRouter can't be asked; if neither is available, the run
treats the budget as used up (cheap config for that run: the safe side for money). The key's own limit stays the hard backstop.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable

import requests

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path("state/season_spend.json")
KEY_URL = "https://openrouter.ai/api/v1/key"


def openrouter_key_usage(api_key: str, timeout_s: float = 15.0) -> float:
    """Total credit usage (USD) of the key making the request (OpenRouter GET /api/v1/key, field data.usage)."""
    resp = requests.get(KEY_URL, headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout_s)
    resp.raise_for_status()
    usage = resp.json()["data"]["usage"]
    return float(usage)


class SeasonSpend:
    def __init__(self, season: str, cap_usd: float, path: Path | str = DEFAULT_PATH) -> None:
        self.season, self.cap_usd, self.path = season, cap_usd, Path(path)
        self.spent_usd = 0.0
        self.source = "cache file"
        self._cached = self._read_file()
        if self._cached is not None:
            self.spent_usd = self._cached

    def _read_file(self) -> float | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("season") == self.season:
                return float(data.get("spent_usd", 0.0))
            return None
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError, AttributeError) as e:
            logger.warning(f"Season spend file unreadable ({e})")
            return None

    @classmethod
    def from_live_key(cls, season: str, cap_usd: float, api_key: str, before_key_usd: float, path: Path | str = DEFAULT_PATH,
                      usage: Callable[[str], float] = openrouter_key_usage) -> "SeasonSpend":
        """The season total from the live key's usage at OpenRouter (B-85). Falls back to the cache file, then to 'used up'."""
        s = cls(season, cap_usd, path)
        try:
            s.spent_usd = before_key_usd + usage(api_key)
            s.source = "OpenRouter live key"
        except Exception as e:  # network, auth, a changed response: never stop the run over the counter
            if s._cached is not None:
                s.source = "cache file (OpenRouter key usage unavailable)"
                logger.warning(f"OpenRouter key usage unavailable ({type(e).__name__}: {e}); using the cached season total "
                               f"${s.spent_usd:.2f}")
            else:
                s.spent_usd = cap_usd
                s.source = "unknown: cheap config for this run"
                logger.warning(f"OpenRouter key usage unavailable ({type(e).__name__}: {e}) and no cached season total: "
                               "treating the frontier budget as used up for this run (cheap config)")
        return s

    @property
    def exhausted(self) -> bool:
        return self.spent_usd >= self.cap_usd

    def add(self, usd: float) -> None:
        self.spent_usd += max(0.0, usd)

    def save(self) -> None:
        if self.source.startswith("unknown"):
            return  # a run that didn't know the total must not write a made-up one over a good cache
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"season": self.season, "cap_usd": self.cap_usd, "spent_usd": round(self.spent_usd, 6)}),
                             encoding="utf-8")
