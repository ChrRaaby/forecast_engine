"""EXP-002 prompt variant "dates-yes-2026-11-07" (B-09b): the live prompt plus (1) one line with the question's scheduled resolution
date and (2) binary answers that say what the number is: "Probability: ZZ% (the probability that this question resolves YES)".

Everything else is the live prompt (forecast_engine/prompts.py, template-2026-09-26) word for word. The date line goes right after
"Today is ...". It uses only dates the question snapshot already holds (no new date source): the scheduled resolution time. Records
don't store the close time, so the line can't name it (EXP-002 card, amendment 2026-11-07). A question without a scheduled
resolution time gets no date line. Dates are written like "today": YYYY-MM-DD, as stored (Metaculus gives UTC).

Used only by replay or shadow arms (ADR-0012). Never edit this module once archived: a changed prompt is a new version.
"""
from __future__ import annotations

from forecast_engine.prompts import _bound_messages, _clean
from forecast_engine.schema import QuestionSnapshot

VERSION = "dates-yes-2026-11-07"


def dates_line(q: QuestionSnapshot) -> str:
    """The date sentence for this question, or "" when the snapshot has no scheduled resolution time."""
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
