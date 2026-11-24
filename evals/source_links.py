"""Resolution-source links of a question, classified by what a plain download gives (free: no model, no AskNews, no Google).

Moved here from the EXP-013 thread's throw-away survey of 2026-11-15 (234 tournament questions; it produced the data repo's
experiments/EXP-013/researcher-test-cases-2026-11-15.csv), with two corrections found while reusing it for the researcher pilot:
  - a JSON / CSV answer is data however short it is (the survey called a short JSON API answer "JavaScript-only");
  - round brackets no longer count as program code (ordinary Wikipedia text was called "JavaScript-only").
The fetch is EXP-013's own (`research_v2._http_get`), so "usable" means usable to the source block as merged.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable

from forecast_engine import research_v2 as rv
from forecast_engine.config import SourceBlockConfig
from forecast_engine.schema import QuestionSnapshot

USABLE, PDF, INDEX, JS, BLOCKED = "usable", "pdf", "index", "javascript", "blocked"
RANK = (USABLE, PDF, INDEX, JS, BLOCKED)  # a question counts as its best link
NO_LINK = "no_link"
METACULUS_ONLY = "metaculus_only"

GROUPS = ("javascript", "index", "no_usable_link")

_NUM = re.compile(r"\b\d[\d,.]*\b")
_CODE = "{};="
MIN_CHARS, MAX_CODE_SHARE, MIN_NUMBERS = 600, 0.03, 25


def classify_fetch(res: dict, cfg: SourceBlockConfig) -> str:
    """Kind of link from what `research_v2._http_get` returned."""
    err = res.get("error") or ""
    if err:
        return PDF if "application/pdf" in err else BLOCKED
    ctype = res.get("content_type", "")
    if "json" in ctype or "csv" in ctype:
        return USABLE
    text = rv.page_text(res["body"], ctype, cfg.max_text_chars)
    if len(text) < MIN_CHARS or sum(text.count(c) for c in _CODE) / len(text) > MAX_CODE_SHARE:
        return JS
    return USABLE if len(_NUM.findall(text)) >= MIN_NUMBERS else INDEX


def classify_link(url: str, cfg: SourceBlockConfig = SourceBlockConfig(), get: Callable | None = None) -> str:
    try:
        return classify_fetch((get or rv._http_get)(url, cfg), cfg)
    except Exception:  # noqa: BLE001 - timeouts, TLS and connection errors are what "blocked" means here
        return BLOCKED


def question_kind(q: QuestionSnapshot, kinds: dict[str, str], cfg: SourceBlockConfig = SourceBlockConfig()) -> str:
    links = rv.resolution_links(q, cfg.max_links)
    if not links:
        has_any = rv.URL_RE.search(f"{q.resolution_criteria}\n{q.fine_print}")
        return METACULUS_ONLY if has_any else NO_LINK
    return min((kinds[u] for u in links), key=RANK.index)


def group_of(kind: str) -> str | None:
    """The pilot's group, or None for questions whose source a plain download can already read (or a direct PDF)."""
    if kind == JS:
        return "javascript"
    if kind == INDEX:
        return "index"
    return "no_usable_link" if kind in (BLOCKED, METACULUS_ONLY, NO_LINK) else None


def classify_questions(qs: Iterable[QuestionSnapshot], cfg: SourceBlockConfig = SourceBlockConfig(),
                       get: Callable | None = None, workers: int = 8) -> tuple[dict[int, str], dict[str, str]]:
    """(question id -> kind, link -> kind). Each distinct link is fetched once."""
    qs = list(qs)
    urls = sorted({u for q in qs for u in rv.resolution_links(q, cfg.max_links)})
    with ThreadPoolExecutor(max_workers=workers) as ex:
        kinds = dict(zip(urls, ex.map(lambda u: classify_link(u, cfg, get), urls)))
    return {q.question_id: question_kind(q, kinds, cfg) for q in qs}, kinds
