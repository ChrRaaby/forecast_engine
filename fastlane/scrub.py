"""Keep market odds out of what the forecasters read (design review point 1).

Polymarket titles go word for word into the research searches, so a news article or Gemini's own summary may quote the market's
odds. Odds in the brief would turn the screen into "does the bot copy the market". `scrub_bundle` removes, from a copy of the bundle:
  - whole items from prediction-market / betting hosts,
  - every sentence that names a market venue or quotes odds / implied probabilities.
It errs on the side of deleting too much ("odds of rain" goes too). What was removed is counted and returned for the record; the
removed text itself is not kept (AskNews text is licensed). It never raises.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace

from forecast_engine.schema import ResearchBundle, ResearchItem

HOST_RE = re.compile(r"(polymarket|kalshi|manifold\.markets|metaculus\.com|predictit|betfair|oddschecker|sportsbook|draftkings|fanduel|"
                     r"pinnacle|bet365|prediction-?market|polymarketanalytics|dune\.com/.*polymarket)", re.I)
SENTENCE_RE = re.compile(
    r"polymarket|kalshi|manifold|metaculus|predictit|prediction markets?|betting markets?|sportsbooks?|bookmakers?|"
    r"\bodds\b|implied probabilit|market[- ]implied|traders? (?:give|see|price|put|bet|expect|assign)|"
    r"\b(?:yes|no) (?:shares?|contracts?|tokens?)\b|\bpriced? (?:at|in) \d+\s?(?:%|¢|cents)|\d+\s?¢|\bcents? on the dollar\b|"
    r"\d+(?:\.\d+)?\s?% (?:chance|probability|likelihood) (?:on|according to|per|in) ",
    re.I,
)
_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass(frozen=True)
class ScrubReport:
    items_dropped: int = 0
    sentences_removed: int = 0

    @property
    def any(self) -> bool:
        return bool(self.items_dropped or self.sentences_removed)

    def flag(self) -> str:
        return f"fastlane: market odds scrubbed from research ({self.items_dropped} items dropped, {self.sentences_removed} sentences removed)"


def scrub_text(text: str) -> tuple[str, int]:
    kept, removed = [], 0
    for s in _SPLIT.split(text or ""):
        if not s:
            continue
        if SENTENCE_RE.search(s):
            removed += 1
        else:
            kept.append(s)
    return " ".join(kept), removed


def scrub_bundle(b: ResearchBundle) -> tuple[ResearchBundle, ScrubReport]:
    items: list[ResearchItem] = []
    dropped = removed = 0
    for it in b.items:
        if HOST_RE.search(it.url or "") or HOST_RE.search(it.title or ""):
            dropped += 1
            continue
        text, n = scrub_text(it.text)
        removed += n
        title = it.title
        if title and SENTENCE_RE.search(title):
            title, removed = None, removed + 1
        if not text.strip():
            if n:
                dropped += 1
            continue
        items.append(replace(it, text=text, title=title))
    return replace(b, items=items), ScrubReport(dropped, removed)
