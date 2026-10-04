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

import asyncio
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

# Release dates of models that may be used as the screen (vendor announcement dates; OpenRouter listing date as a cross-check via
# tools/check_models.py). A model not in this table can't be used. Later models are listed only so the cutoff check can reject them.
SCREEN_MODEL_RELEASE_DATES: dict[str, date] = {
    "google/gemini-2.5-flash": date(2025, 6, 17),  # Google GA announcement; OpenRouter listing 2025-06-17
    "openai/gpt-5-mini": date(2025, 8, 7),  # OpenRouter listing; verify against OpenAI before use
    "anthropic/claude-haiku-4.5": date(2025, 10, 15),  # OpenRouter listing; verify against Anthropic before use
    "google/gemini-3.5-flash-lite": date(2026, 7, 21),  # live forecaster; after the cutoff, so always rejected
}


@dataclass(frozen=True)
class ScreenConfig:
    # OpenRouter id; its release date comes from SCREEN_MODEL_RELEASE_DATES, never from the caller (B-45 review: a separate
    # date field let a newer model pass the cutoff check with the default's date). Verify with tools/check_models.py.
    model: str = "google/gemini-2.5-flash"
    max_tokens: int = 8000  # room for the model's thinking tokens plus a short JSON answer
    reasoning_effort: str | None = None
    # v3 (2026-10-04, B-45): on 30 real bundles v2 flagged 37-67% of clean bundles, nearly all for schedules, previews, forecasts
    # or betting odds written before the reference date. v3 says these are allowed (protocol T7 allows as-of market prices) and
    # flags only reports of things that already happened after the reference date, hindsight, or platform aggregate forecasts.
    # v2 (2026-10-03): v1 flagged an Oct 29-30 2025 Fed article as "after" a 2025-11-01 reference date (2 of 2 runs); v2 states
    # that publication dates are pre-checked and asks for the described event's date. On the PC calibration (one real bundle +
    # two planted hindsight items, 2 runs each) v2 had 0 false positives and caught both plants every time; v1 caught the plants
    # but flagged the clean bundle. See ADR-0008.
    prompt_version: str = "leak-screen-v3"

    @property
    def model_release_date(self) -> date:
        return SCREEN_MODEL_RELEASE_DATES[self.model]

    def __post_init__(self) -> None:
        if self.model not in SCREEN_MODEL_RELEASE_DATES:
            raise ValueError(f"unknown screen model {self.model}: add its vendor release date to SCREEN_MODEL_RELEASE_DATES first")
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

Judge only from the text above. Every item was published on or before {ref}; that has already been checked
by machine, so a publication date is never a reason to flag. A leak is text that reveals what actually happened AFTER {ref}.

Flag an item only if it:
- states as fact that something has already happened, been decided, measured or announced on a date after {ref};
- uses hindsight about later events ("went on to", "ultimately", "in the end", "it later emerged");
- quotes an aggregate forecast for this question from Metaculus or another forecasting platform.

Do NOT flag text written before {ref} about the future: schedules, plans, deadlines, previews, expectations, forecasts, projections,
betting odds or market prices. These were available on {ref} and are allowed, even when they mention later dates or bear directly
on the question. Before flagging, find the date of the event the text reports as having happened and check it is after {ref}.

Answer with one JSON object and nothing else:
{{"leak": true or false, "items": [indices of flagged items], "reason": "one sentence quoting the decisive text and its event date"}}
"""


_DECODER = json.JSONDecoder(strict=False)  # tolerate raw newlines/control characters inside the reason string
_LEAK_FIELD = re.compile(r'"leak"\s*:\s*(true|false)')
_ITEMS_FIELD = re.compile(r'"items"\s*:\s*\[([0-9,\s]*)\]')


def parse_verdict(output: str, n_items: int) -> tuple[Status, list[int], str]:
    """Strict: anything but a well-formed verdict is a failure (fail closed)."""
    text = output or ""
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON object in the screen output")
    # The first complete JSON object; anything after it is ignored (B-45: a greedy match failed every Haiku verdict).
    try:
        data, _ = _DECODER.raw_decode(text, start)
    except json.JSONDecodeError:
        # B-45: models sometimes break the free-text "reason" (unescaped quotes, cut-off string). The decision fields are what
        # matter, so recover them if both are present and well-formed; otherwise fail closed as before.
        # Only a recovered "leak": true is accepted (excluding is the safe side); a broken "clean" verdict still fails closed, so
        # recovery can never let a question in.
        leak_m, items_m = _LEAK_FIELD.search(text, start), _ITEMS_FIELD.search(text, start)
        if not (leak_m and items_m) or leak_m.group(1) != "true":
            raise
        items = [int(x) for x in items_m.group(1).replace(" ", "").split(",") if x]
        data = {"leak": leak_m.group(1) == "true", "items": items, "reason": "(reason text unparseable)"}
    if not isinstance(data, dict):
        raise ValueError("the screen output's JSON is not an object")
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
    prompt = screen_prompt(q, bundle)
    for _attempt in range(2):  # one retry on an API error (B-45: transient empty completions); then fail closed
        call = await llm.complete(model=cfg.model, prompt=prompt, purpose="leakage_screen",
                                  max_tokens=cfg.max_tokens, reasoning_effort=cfg.reasoning_effort)
        if not call.error:
            break
    if call.error:
        return ScreenVerdict(status="screen_failed", reason=call.error, call=call)
    try:
        status, items, reason = parse_verdict(call.output, len(bundle.items))
    except (ValueError, json.JSONDecodeError) as e:
        return ScreenVerdict(status="screen_failed", reason=f"unparseable verdict: {e}", call=call)
    return ScreenVerdict(status=status, flagged_items=items, reason=reason, call=call)


@dataclass(frozen=True)
class DualScreenConfig:
    """Two pre-cutoff screens; a question is excluded as a leak only if BOTH flag it, and as screen_failed if EITHER fails.

    Chosen in B-45 (2026-10-04). On 50 real bundles with 100 planted leaks, single screens wrongly flagged 10-30% of clean bundles
    (mostly source text with wrong or missing years), while requiring agreement flagged 2 of 50 (4%) and still caught all 100 plants.
    The agreement rule was picked after seeing both samples, so it gets a fresh confirmation sample in the next AskNews period.
    """

    primary: ScreenConfig = ScreenConfig(model="openai/gpt-5-mini", reasoning_effort="low", max_tokens=16000)
    secondary: ScreenConfig = ScreenConfig()  # google/gemini-2.5-flash
    rule: str = "both-must-flag"

    @property
    def prompt_version(self) -> str:
        return self.primary.prompt_version

    def config_hash(self) -> str:
        blob = json.dumps({"primary": self.primary.config_hash(), "secondary": self.secondary.config_hash(), "rule": self.rule},
                          sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:16]


async def screen_bundle_dual(q: QuestionSnapshot, bundle: ResearchBundle, llm: LlmClient, cfg: DualScreenConfig) -> ScreenVerdict:
    a, b = await asyncio.gather(screen_bundle(q, bundle, llm, cfg.primary), screen_bundle(q, bundle, llm, cfg.secondary))
    if a.status == "screen_failed" or b.status == "screen_failed":  # fail closed
        failed = a if a.status == "screen_failed" else b
        return ScreenVerdict(status="screen_failed", reason=f"{failed.call.model if failed.call else '?'}: {failed.reason}",
                             call=failed.call)
    if a.status == "leak" and b.status == "leak":
        items = sorted(set(a.flagged_items) & set(b.flagged_items)) or sorted(set(a.flagged_items) | set(b.flagged_items))
        return ScreenVerdict(status="leak", flagged_items=items, reason=f"both screens: {a.reason} | {b.reason}", call=a.call)
    reason = "neither screen flagged" if a.status == b.status == "clean" else (
        f"screens disagree (only {'primary' if a.status == 'leak' else 'secondary'} flagged): "
        f"{a.reason if a.status == 'leak' else b.reason}")
    return ScreenVerdict(status="clean", reason=reason, call=a.call)


async def run_screen(q: QuestionSnapshot, bundle: ResearchBundle, llm: LlmClient,
                     cfg: ScreenConfig | DualScreenConfig) -> ScreenVerdict:
    if isinstance(cfg, DualScreenConfig):
        return await screen_bundle_dual(q, bundle, llm, cfg)
    return await screen_bundle(q, bundle, llm, cfg)


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
