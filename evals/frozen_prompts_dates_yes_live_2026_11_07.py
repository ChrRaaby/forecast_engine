"""FROZEN copy of the LIVE forecast-prompt builder for BotConfig.prompt_version "dates-yes-live-2026-11-07" (EXP-002, live from
2026-11-07). Never edit this file.

Same text as the experimental variant "dates-yes-2026-11-07" (evals/frozen_prompts_dates_yes_2026_11_07.py) that passed EXP-002's
no-harm check; copied from `forecast_engine/prompts.py` plus `ResearchBundle.as_prompt_text()` and `today_str()`. A test pins this
file's hash; the drift guard checks that, while this is the live version, it renders exactly what the live code renders.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from textwrap import dedent
from typing import Any

from forecast_engine.schema import QuestionSnapshot

VERSION = "dates-yes-live-2026-11-07"


def _clean(text: str) -> str:
    return dedent(text).strip() + "\n"


def research_text(items: Sequence[Any]) -> str:
    """ResearchBundle.as_prompt_text() as of this version; `items` have .source, .title and .text."""
    if not any(item.text.strip() for item in items):
        return "No research is available for this question."
    blocks = []
    for item in items:
        header = f"[{item.source}]" + (f" {item.title}" if item.title else "")
        blocks.append(f"{header}\n{item.text.strip()}")
    return "\n\n".join(blocks)


def today_text(as_of: datetime) -> str:
    """today_str(FixedClock(as_of)) as of this version."""
    return as_of.strftime("%Y-%m-%d")


def build(q: QuestionSnapshot, items: Sequence[Any], as_of: datetime) -> str:
    """The forecast prompt this version sends for question `q`, research `items` and as-of time `as_of`."""
    return forecast_prompt(q, research_text(items), today_text(as_of))


def dates_line(q: QuestionSnapshot) -> str:
    if q.scheduled_resolution_time is None:
        return ""
    return (f"This question is still open. It is scheduled to resolve on "
            f"{q.scheduled_resolution_time.strftime('%Y-%m-%d')}.")


def _today_and_dates(q: QuestionSnapshot, today: str) -> str:
    line = dates_line(q)
    return f"Today is {today}." + (f"\n        {line}" if line else "")


def binary_prompt(q: QuestionSnapshot, research: str, today: str) -> str:
    return _clean(
        f"""
        You are a professional forecaster interviewing for a job.

        Your interview question is:
        {q.question_text}

        Question background:
        {q.background_info}


        This question's outcome will be determined by the specific criteria below. These criteria have not yet been satisfied:
        {q.resolution_criteria}

        {q.fine_print}


        Your research assistant says:
        {research}

        {_today_and_dates(q, today)}

        Before answering you write:
        (a) The time left until the outcome to the question is known.
        (b) The status quo outcome if nothing changed.
        (c) A brief description of a scenario that results in a No outcome.
        (d) A brief description of a scenario that results in a Yes outcome.

        You write your rationale remembering that good forecasters put extra weight on the status quo outcome since the world changes slowly most of the time.

        The last thing you write is your final answer as: "Probability: ZZ%", 0-100, where ZZ is the probability that this question resolves YES
        """
    )


def multiple_choice_prompt(q: QuestionSnapshot, research: str, today: str) -> str:
    options = list(q.options)
    return _clean(
        f"""
        You are a professional forecaster interviewing for a job.

        Your interview question is:
        {q.question_text}

        The options are: {options}


        Background:
        {q.background_info}

        {q.resolution_criteria}

        {q.fine_print}


        Your research assistant says:
        {research}

        {_today_and_dates(q, today)}

        Before answering you write:
        (a) The time left until the outcome to the question is known.
        (b) The status quo outcome if nothing changed.
        (c) A description of an scenario that results in an unexpected outcome.

        You write your rationale remembering that (1) good forecasters put extra weight on the status quo outcome since the world changes slowly most of the time, and (2) good forecasters leave some moderate probability on most options to account for unexpected outcomes.

        The last thing you write is your final probabilities for the N options in this order {options} as:
        Option_A: Probability_A
        Option_B: Probability_B
        ...
        Option_N: Probability_N
        """
    )


def _bound_messages(q: QuestionSnapshot) -> tuple[str, str]:
    upper = q.nominal_upper_bound if q.nominal_upper_bound is not None else q.upper_bound
    lower = q.nominal_lower_bound if q.nominal_lower_bound is not None else q.lower_bound
    unit = q.unit_of_measure
    if q.open_upper_bound:
        upper_msg = f"The question creator thinks the number is likely not higher than {upper} {unit}."
    else:
        upper_msg = f"The outcome can not be higher than {upper} {unit}."
    if q.open_lower_bound:
        lower_msg = f"The question creator thinks the number is likely not lower than {lower} {unit}."
    else:
        lower_msg = f"The outcome can not be lower than {lower} {unit}."
    return upper_msg, lower_msg


def numeric_prompt(q: QuestionSnapshot, research: str, today: str) -> str:
    upper_msg, lower_msg = _bound_messages(q)
    units = q.unit_of_measure if q.unit_of_measure else "Not stated (please infer this)"
    return _clean(
        f"""
        You are a professional forecaster interviewing for a job.

        Your interview question is:
        {q.question_text}

        Background:
        {q.background_info}

        {q.resolution_criteria}

        {q.fine_print}

        Units for answer: {units}

        Your research assistant says:
        {research}

        {_today_and_dates(q, today)}

        {lower_msg}
        {upper_msg}

        Formatting Instructions:
        - Please notice the units requested and give your answer in these units (e.g. whether you represent a number as 1,000,000 or 1 million).
        - Never use scientific notation.
        - Always start with a smaller number (more negative if negative) and then increase from there. The value for percentile 10 should always be less than the value for percentile 20, and so on.

        Before answering you write:
        (a) The time left until the outcome to the question is known.
        (b) The outcome if nothing changed.
        (c) The outcome if the current trend continued.
        (d) The expectations of experts and markets.
        (e) A brief description of an unexpected scenario that results in a low outcome.
        (f) A brief description of an unexpected scenario that results in a high outcome.

        You remind yourself that good forecasters are humble and set wide 90/10 confidence intervals to account for unknown unknowns.

        The last thing you write is your final answer as:
        "
        Percentile 10: XX (lowest number value)
        Percentile 20: XX
        Percentile 40: XX
        Percentile 60: XX
        Percentile 80: XX
        Percentile 90: XX (highest number value)
        "
        """
    )


def forecast_prompt(q: QuestionSnapshot, research: str, today: str) -> str:
    if q.question_type == "binary":
        return binary_prompt(q, research, today)
    if q.question_type == "multiple_choice":
        return multiple_choice_prompt(q, research, today)
    if q.question_type in ("numeric", "discrete"):
        return numeric_prompt(q, research, today)
    raise ValueError(f"Unsupported question type: {q.question_type}")
