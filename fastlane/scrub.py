"""Keep market odds out of what the forecasters read (design review point 1).

Polymarket titles go word for word into the research searches, so a news article or Gemini's own summary may quote the market's
odds. Odds in the brief would turn the screen into "does the bot copy the market". `scrub_bundle` removes, from a copy of the bundle:
  - whole items from prediction-market / betting hosts,
  - every line that names a market venue or betting, and every other sentence that quotes odds / implied probabilities.
It errs on the side of deleting too much ("odds of rain" goes too), but it is pattern-based: unusual wording can get through.
It cleans what the forecasters read. The raw provider output stays in the record's `research.calls` (audit trail, private archive
only), so a stored record can still contain odds; never feed `research.calls` to a model.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace

from forecast_engine.schema import ResearchBundle, ResearchItem

HOST_RE = re.compile(r"(polymarket|kalshi|manifold\.markets|metaculus\.com|predictit|betfair|oddschecker|sportsbook|draftkings|fanduel|"
                     r"pinnacle|bet365|prediction-?market|polymarketanalytics|dune\.com/.*polymarket)", re.I)
# a named venue or betting: the whole paragraph goes, because the figure often sits in a neighbouring sentence
VENUE_RE = re.compile(r"polymarket|kalshi|manifold|metaculus|predictit|prediction markets?|betting markets?|sportsbooks?|bookmakers?|"
                      r"\bbett(?:ors?|ing)\b|\bwager(?:s|ed|ing)?\b|\bgamblers?\b", re.I)
SENTENCE_RE = re.compile(
    r"polymarket|kalshi|manifold|metaculus|predictit|prediction markets?|betting markets?|sportsbooks?|bookmakers?|"
    r"\bbett(?:ors?|ing)\b|\bwager(?:s|ed|ing)?\b|\bgamblers?\b|\btrad(?:es|ing|ed) at\b|"
    r"\bodds\b|implied probabilit|market[- ]implied|traders? (?:give|see|price|put|bet|expect|assign)|"
    r"\b(?:yes|no) (?:shares?|contracts?|tokens?)\b|\bpriced? (?:at|in) \d+\s?(?:%|¢|cents)|\d+\s?¢|\bcents? on the dollar\b|"
    r"\d+(?:\.\d+)?\s?% (?:chance|probability|likelihood) (?:on|according to|per|in) ",
    re.I,
)
_SPLIT = re.compile(r"(?<=[.!?])\s+")
_ABBREV = re.compile(r"\b(U\.S|U\.K|U\.N|E\.U|D\.C|Mr|Mrs|Ms|Dr|St|Inc|Corp|Co|Ltd|vs|No|Jr|Sr|Gov|Sen|Rep|Gen|approx|est)\.(?=\s)")
_DOT = "․"  # stands in for the full stop of an abbreviation while we split sentences


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
    """Drop every paragraph that names a venue or betting, then every sentence that quotes odds. Returns (clean text, removed count)."""
    kept_paras, removed = [], 0
    for para in re.split(r"\n+", text or ""):  # a line is a paragraph here: feeds put one paragraph per line
        if not para.strip():
            continue
        if VENUE_RE.search(para):
            removed += 1
            continue
        protected = _ABBREV.sub(lambda m: m.group(0)[:-1] + _DOT, para)
        kept = []
        for sent in _SPLIT.split(protected):
            if SENTENCE_RE.search(sent):
                removed += 1
            elif sent.strip():
                kept.append(sent.replace(_DOT, "."))
        if kept:
            kept_paras.append(" ".join(kept))
    return "\n".join(kept_paras), removed


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
