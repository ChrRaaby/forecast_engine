"""forecast(question, clock, research, config, llm) -> ForecastRecord.

The one entry point shared by the live bot and the M2 harness (ADR-0005). Given the same inputs it makes the same
calls; everything that happened is in the returned record. It never publishes and never reads the system clock.
"""
from __future__ import annotations

import asyncio

from . import parsing
from .clock import Clock, today_str
from .config import CONFIG_VERSION, BotConfig
from .llm import LlmClient
from .prompts import forecast_prompt
from .schema import ForecasterOutput, ForecastRecord, QuestionSnapshot, ResearchBundle



class ForecastFailed(RuntimeError):
    def __init__(self, message: str, record: ForecastRecord) -> None:
        super().__init__(message)
        self.record = record


async def forecast(
    q: QuestionSnapshot,
    clock: Clock,
    research: ResearchBundle,
    cfg: BotConfig,
    llm: LlmClient,
) -> ForecastRecord:
    prompt = forecast_prompt(q, research.as_prompt_text(), today_str(clock))
    calls = await asyncio.gather(
        *[llm.complete(
                model=f.model, prompt=prompt, purpose="forecast", max_tokens=f.max_tokens,
                reasoning_effort=f.reasoning_effort,
            ) for f in cfg.forecasters]
    )
    outputs = [_parse(q, call, cfg) for call in calls]

    record = ForecastRecord(
        config_version=CONFIG_VERSION,
        config_hash=cfg.config_hash(),
        question=q,
        as_of=clock.now(),
        forecasters=outputs,
        aggregate=None,
        aggregation=cfg.aggregation,
        research=research,
    )
    ok = [o.prediction for o in outputs if o.ok]
    if len(ok) < cfg.min_successful_forecasters:
        errors = [f"{o.model}: {o.call.error or o.parse_error}" for o in outputs if not o.ok]
        record.errors.append(f"only {len(ok)}/{len(outputs)} forecasters succeeded")
        record.errors.extend(errors)
        raise ForecastFailed(f"only {len(ok)} of {len(outputs)} forecasters succeeded: {errors}", record)

    if cfg.aggregation != "median":
        raise ValueError(f"unknown aggregation {cfg.aggregation!r}")
    if q.question_type == "binary":
        record.aggregate = parsing.median_binary(ok)
    elif q.question_type == "multiple_choice":
        record.aggregate = parsing.median_multiple_choice(ok)
    else:
        record.aggregate = parsing.median_numeric(ok)
    return record


def _parse(q: QuestionSnapshot, call, cfg: BotConfig) -> ForecasterOutput:
    out = ForecasterOutput(model=call.model, call=call)
    if call.error:
        return out
    try:
        if q.question_type == "binary":
            out.prediction = parsing.parse_binary(call.output, cfg.binary_clip)
        elif q.question_type == "multiple_choice":
            out.prediction = parsing.parse_multiple_choice(call.output, q.options)
        else:
            out.prediction = parsing.parse_numeric(call.output, q.unit_of_measure)
    except parsing.ParseError as e:
        out.parse_error = str(e)
    return out
