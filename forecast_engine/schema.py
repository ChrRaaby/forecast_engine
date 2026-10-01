"""Plain data types shared by the live adapter and the (future) evaluation harness.

Deliberately independent of forecasting-tools, so a backtest can build a QuestionSnapshot from a frozen manifest.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

QuestionType = Literal["binary", "multiple_choice", "numeric", "discrete"]


@dataclass(frozen=True)
class QuestionSnapshot:
    """The question as the forecaster may see it at as_of. No resolution fields, no community prediction (T4, T7)."""

    question_id: int
    post_id: int | None
    question_type: QuestionType
    question_text: str
    background_info: str = ""
    resolution_criteria: str = ""
    fine_print: str = ""
    page_url: str = ""
    tournaments: tuple[str, ...] = ()  # slugs, for monitoring only; never shown to the forecaster
    open_time: datetime | None = None
    scheduled_resolution_time: datetime | None = None
    # multiple choice
    options: tuple[str, ...] = ()
    # numeric / discrete
    unit_of_measure: str = ""
    lower_bound: float | None = None
    upper_bound: float | None = None
    open_lower_bound: bool = False
    open_upper_bound: bool = False
    nominal_lower_bound: float | None = None
    nominal_upper_bound: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


@dataclass
class LlmCall:
    """One request to a model, with everything needed to audit or replay it."""

    purpose: str  # e.g. "research", "forecast"
    provider: str  # "openrouter", "gemini", "asknews"
    model: str
    params: dict[str, Any]
    prompt: str
    requested_at: datetime
    output: str = ""
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None  # None = provider did not report a cost
    latency_s: float | None = None
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


@dataclass
class ResearchItem:
    source: str  # provider name
    text: str
    url: str | None = None
    title: str | None = None
    published_at: datetime | None = None  # None = undated; the M2 as-of guard will reject undated items in backtests


@dataclass
class ResearchBundle:
    as_of: datetime
    providers: list[str]
    items: list[ResearchItem]
    calls: list[LlmCall]
    errors: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not any(item.text.strip() for item in self.items)

    def as_prompt_text(self) -> str:
        if self.is_empty:
            return "No research is available for this question."
        blocks = []
        for item in self.items:
            header = f"[{item.source}]" + (f" {item.title}" if item.title else "")
            blocks.append(f"{header}\n{item.text.strip()}")
        return "\n\n".join(blocks)

    @property
    def cost_usd(self) -> float:
        return sum(c.cost_usd or 0.0 for c in self.calls)

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


@dataclass
class ForecasterOutput:
    model: str
    call: LlmCall
    prediction: Any = None  # float | dict[str, float] | list[tuple[float, float]]
    parse_error: str | None = None

    @property
    def ok(self) -> bool:
        return self.prediction is not None


@dataclass
class ForecastRecord:
    config_version: str
    config_hash: str
    question: QuestionSnapshot
    as_of: datetime
    forecasters: list[ForecasterOutput]
    aggregate: Any  # float | dict[str, float] | list[tuple[float, float]] | None
    aggregation: str
    research: ResearchBundle
    errors: list[str] = field(default_factory=list)

    @property
    def n_ok(self) -> int:
        return sum(1 for f in self.forecasters if f.ok)

    @property
    def cost_usd(self) -> float:
        return self.research.cost_usd + sum(f.call.cost_usd or 0.0 for f in self.forecasters)

    @property
    def cost_complete(self) -> bool:
        """False if any paid call failed to report its cost (so cost_usd is a lower bound)."""
        calls = [f.call for f in self.forecasters] + [c for c in self.research.calls if c.provider == "openrouter"]
        return all(c.cost_usd is not None for c in calls if c.error is None)

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(
            {
                "config_version": self.config_version,
                "config_hash": self.config_hash,
                "question": self.question.to_dict(),
                "as_of": self.as_of,
                "aggregation": self.aggregation,
                "aggregate": self.aggregate,
                "n_forecasters_ok": self.n_ok,
                "cost_usd": self.cost_usd,
                "cost_complete": self.cost_complete,
                "forecasters": [
                    {"model": f.model, "prediction": f.prediction, "parse_error": f.parse_error, "call": f.call.to_dict()}
                    for f in self.forecasters
                ],
                "research": self.research.to_dict(),
                "errors": self.errors,
            }
        )


def _jsonable(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return obj
