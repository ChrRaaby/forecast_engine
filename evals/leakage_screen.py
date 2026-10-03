"""LLM leakage screen on assembled research bundles (protocol T3; ADR-0008).

The date guard can't see text that was edited or summarised after as_of. This screen reads the question and the bundle and
flags any sign of information from after as_of. A flagged question is excluded from scoring and counted; it is never "repaired"
by dropping items, because that would let the screen shape what the forecaster sees.

The screen model must be released on or before the evaluation-model cutoff (Christian, 2026-10-03). A model that knows what
happened could flag on its own knowledge of the outcome, which makes the excluded set correlate with outcomes (a selection
leak). A pre-cutoff model can only judge from evidence in the text. The check is in code: ScreenConfig refuses a later model,
and `screen_bundle` refuses a model released after the bundle's as_of.

Fail closed: an API error or an unparseable answer counts as excluded ("screen_failed"), reported separately from "leak".
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Literal

from forecast_engine.llm import LlmClient
from forecast_engine.schema import LlmCall, QuestionSnapshot, ResearchBundle

EVAL_MODEL_CUTOFF = date(2025, 12, 1)  # evaluation models released on or before this date (Christian, 2026-10-03; research/05)
LEAK_RATE_WARN = 0.10  # protocol T3


@dataclass(frozen=True)
class ScreenConfig:
    # OpenRouter id. Verify with tools/check_models.py on the PC before the first paid run.
    model: str = "google/gemini-2.5-flash"
    # Google's GA announcement for Gemini 2.5 Flash, 2025-06-17 (research/05 lists the same OpenRouter date). Verify.
    model_release_date: date = date(2025, 6, 17)
    max_tokens: int = 8000  # room for the model's thinking tokens plus a short JSON answer
    reasoning_effort: str | None = None
    prompt_version: str = "leak-screen-v1"

    def __post_init__(self) -> None:
        if self.model_release_date > EVAL_MODEL_CUTOFF:
            raise ValueError(
                f"screen model {self.model} was released {self.model_release_date}, after the evaluation-model cutoff "
                f"{EVAL_MODEL_CUTOFF}; it could flag on its own knowledge of outcomes"
            )

    def config_hash(self) -> str:
        blob = json.dumps(asdict(self), sort_keys=True, default=str).encode()
        return hashlib.sha256(blob).hexdigest()[:16]


Status = Literal["clean", "leak", "screen_failed"]


@dataclass
class ScreenVerdict:
    status: Status
    flagged_items: list[int] = field(default_factory=list)
    reason: str = ""
    call: LlmCall | None = None

    @property
    def excluded(self) -> bool:
        return self.status != "clean"

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "flagged_items": self.flagged_items, "reason": self.reason,
                "call": self.call.to_dict() if self.call else None}


def screen_prompt(q: QuestionSnapshot, bundle: ResearchBundle) -> str:
    """No date other than as_of and the items' own publication dates (all ≤ as_of) appears here (T5); no resolution fields (T4)."""
    ref = bundle.as_of.strftime("%Y-%m-%d")
    items = []
    for i, item in enumerate(bundle.items):
        published = item.published_at.strftime("%Y-%m-%d") if item.published_at else "undated"
        items.append(f"[{i}] (published {published}) {item.title or ''}\n{item.text.strip()}")
    research = "\n\n".join(items) if items else "(no items)"
    criteria = f"\nResolution criteria:\n{q.resolution_criteria}\n" if q.resolution_criteria else ""
    return f"""You are auditing research material for a forecasting backtest. A forecaster will be asked the question below as
if the current date were {ref} (the reference date). The research must contain only information that was available on or
before the reference date. Your job is to find leaks, not to forecast.

Question:
{q.question_text}
{criteria}
Research items:
{research}

Judge only from the text above. Flag an item if it:
- describes events, data or announcements dated after the reference date;
- reports or implies how the question resolved, or uses hindsight ("went on to", "ultimately", "in the end", "it later emerged");
- quotes a probability or price from a forecasting platform or prediction market (Metaculus, Polymarket, Manifold, Kalshi, ...).
Background published before the reference date, and predictions or plans made before it, are fine.

Answer with one JSON object and nothing else:
{{"leak": true or false, "items": [indices of flagged items], "reason": "one sentence quoting the decisive text"}}
"""


_JSON = re.compile(r"\{.*\}", re.DOTALL)


def parse_verdict(output: str, n_items: int) -> tuple[Status, list[int], str]:
    """Strict: anything but a well-formed verdict is a failure (fail closed)."""
    m = _JSON.search(output or "")
    if not m:
        raise ValueError("no JSON object in the screen output")
    data = json.loads(m.group(0))
    leak = data.get("leak")
    items = data.get("items", [])
    if not isinstance(leak, bool):
        raise ValueError(f"'leak' must be true or false, got {leak!r}")
    if not isinstance(items, list) or not all(isinstance(i, int) and 0 <= i < n_items for i in items):
        raise ValueError(f"'items' must be indices of research items, got {items!r}")
    if items and not leak:
        raise ValueError("items flagged but 'leak' is false")
    return ("leak" if leak else "clean"), items, str(data.get("reason", ""))


async def screen_bundle(q: QuestionSnapshot, bundle: ResearchBundle, llm: LlmClient, cfg: ScreenConfig) -> ScreenVerdict:
    if cfg.model_release_date >= bundle.as_of.date():
        raise ValueError(f"screen model {cfg.model} (released {cfg.model_release_date}) is not older than as_of "
                         f"{bundle.as_of.date()}")
    if bundle.is_empty:
        return ScreenVerdict(status="clean", reason="no research items")
    call = await llm.complete(model=cfg.model, prompt=screen_prompt(q, bundle), purpose="leakage_screen",
                              max_tokens=cfg.max_tokens, reasoning_effort=cfg.reasoning_effort)
    if call.error:
        return ScreenVerdict(status="screen_failed", reason=call.error, call=call)
    try:
        status, items, reason = parse_verdict(call.output, len(bundle.items))
    except (ValueError, json.JSONDecodeError) as e:
        return ScreenVerdict(status="screen_failed", reason=f"unparseable verdict: {e}", call=call)
    return ScreenVerdict(status=status, flagged_items=items, reason=reason, call=call)


def summarize(statuses: list[str]) -> dict[str, Any]:
    """Run-level leakage report: counts, excluded share, and the protocol's > 10% warning."""
    n = len(statuses)
    n_leak = statuses.count("leak")
    n_failed = statuses.count("screen_failed")
    rate = (n_leak + n_failed) / n if n else 0.0
    out: dict[str, Any] = {"n": n, "n_clean": n - n_leak - n_failed, "n_leak": n_leak, "n_screen_failed": n_failed,
                           "excluded_rate": rate, "warning": None}
    if rate > LEAK_RATE_WARN:
        out["warning"] = (f"{rate:.0%} of bundles excluded (leak {n_leak}, screen failed {n_failed}), above the "
                          f"{LEAK_RATE_WARN:.0%} threshold in protocol T3: check the retrieval before trusting this run")
    return out
