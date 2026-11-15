"""Spiky-counts arm S1 prompt variant "per-value-discrete-2026-11-15": on a DISCRETE question with at most 25 possible values the
models give a probability for each value (and for "below"/"above" when a bound is open) instead of six percentiles. Every other
question (binary, multiple choice, numeric, discrete with more than 25 values) gets the live prompt, word for word, and is parsed
and aggregated as today.

The possible values come from the question snapshot alone: Metaculus puts a discrete question's range half a step outside its
first and last value, so step = 2 × (nominal_lower_bound − lower_bound) and there are (upper − lower) / step values. A snapshot
without nominal bounds, or whose bounds don't give a whole number of steps, is treated as more than 25 values (as today).

Besides the prompt, this module carries the per-value answer path that `forecast_engine.core.forecast` uses when the config names
this version and `applies(q)` is true: `parse_answer` (one "value: probability" line per value, normalised per member),
`aggregate` (member mean, floor 0.5% per value, normalised), and `cdf_from_values` (the CDF Metaculus would get). Used only by
replay or shadow arms (ADR-0012). Never edit this module once archived: a changed prompt is a new version.
"""
from __future__ import annotations

import math
import re
from collections.abc import Sequence
from textwrap import dedent
from typing import Any

from forecast_engine.parsing import ParseError
from forecast_engine.schema import QuestionSnapshot

VERSION = "per-value-discrete-2026-11-15"
MAX_VALUES = 25
FLOOR = 0.005  # per value (and per open-bound bucket), after the member mean
BELOW, ABOVE = "below", "above"
KIND = "per_value"


def _clean(text: str) -> str:
    return dedent(text).strip() + "\n"


# --------------------------------------------------------------------------- which questions, which values
def grid(q: QuestionSnapshot) -> tuple[float, int] | None:
    """(step, number of values) of a discrete question, or None if it can't be read from the snapshot."""
    if q.question_type != "discrete" or None in (q.lower_bound, q.upper_bound, q.nominal_lower_bound):
        return None
    step = 2.0 * (float(q.nominal_lower_bound) - float(q.lower_bound))
    if not step > 0:
        return None
    n_float = (float(q.upper_bound) - float(q.lower_bound)) / step
    n = round(n_float)
    if n < 2 or abs(n_float - n) > 1e-6 * max(1.0, n):
        return None
    return step, n


def _decimals(step: float) -> int:
    return max(0, -math.floor(math.log10(step) + 1e-9)) if step < 1 else 0


def values(q: QuestionSnapshot) -> list[str] | None:
    """The possible values as the prompt writes them (lowest first), or None when the question is not handled by this arm."""
    g = grid(q)
    if g is None or g[1] > MAX_VALUES:
        return None
    step, n = g
    d = _decimals(step)
    return [f"{float(q.lower_bound) + step * (k + 0.5):.{d}f}" for k in range(n)]


def labels(q: QuestionSnapshot) -> list[str] | None:
    """The answer lines in order: "below" if the lower bound is open, every value, "above" if the upper bound is open."""
    vs = values(q)
    if vs is None:
        return None
    return ([BELOW] if q.open_lower_bound else []) + vs + ([ABOVE] if q.open_upper_bound else [])


def applies(q: QuestionSnapshot) -> bool:
    return values(q) is not None


def _line_name(q: QuestionSnapshot, label: str) -> str:
    vs = values(q) or []
    if label == BELOW:
        return f"Below {vs[0]}"
    if label == ABOVE:
        return f"Above {vs[-1]}"
    return label


# --------------------------------------------------------------------------- the per-value prompt
def per_value_prompt(q: QuestionSnapshot, research: str, today: str) -> str:
    names = [_line_name(q, lb) for lb in labels(q) or []]
    vs = values(q) or []
    units = q.unit_of_measure if q.unit_of_measure else "Not stated (please infer this)"
    answer_lines = "\n        ".join(f"{name}: XX%" for name in names)
    extra = []
    if q.open_lower_bound:
        extra.append(f'"Below {vs[0]}" means any outcome lower than {vs[0]}.')
    if q.open_upper_bound:
        extra.append(f'"Above {vs[-1]}" means any outcome higher than {vs[-1]}.')
    extra_text = " ".join(extra)
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

        The outcome will be one of these values: {", ".join(vs)}. {extra_text}

        Before answering you write:
        (a) The time left until the outcome to the question is known.
        (b) The outcome if nothing changed.
        (c) The outcome if the current trend continued.
        (d) The expectations of experts and markets.
        (e) A brief description of an unexpected scenario that results in a low outcome.
        (f) A brief description of an unexpected scenario that results in a high outcome.

        Give each value the probability that it is the outcome.

        The last thing you write is your final answer: one line per value, in this order, with probabilities that add up to 100%:
        {answer_lines}
        """
    )


# --------------------------------------------------------------------------- the live prompt, copied word for word (prompts.py)
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
    if applies(q):
        return per_value_prompt(q, research, today)
    if q.question_type == "binary":
        return binary_prompt(q, research, today)
    if q.question_type == "multiple_choice":
        return multiple_choice_prompt(q, research, today)
    if q.question_type in ("numeric", "discrete"):
        return numeric_prompt(q, research, today)
    raise ValueError(f"Unsupported question type: {q.question_type}")


# --------------------------------------------------------------------------- answer path (used by core.forecast for applies(q))
_NUM = r"[-+]?\d+(?:[.,]\d+)?"


def parse_answer(q: QuestionSnapshot, text: str) -> dict[str, float]:
    """One member's answer -> {label: probability} over labels(q), normalised to 1. The last line naming a label wins (models
    often draft the list before their final answer). A label with no line gets 0. The found lines must add up to 90-110% (or
    0.9-1.1 when written as fractions), so a partial or garbled list is a parse error, never stretched to 100%."""
    lbs = labels(q)
    if lbs is None:
        raise ParseError("question not handled by the per-value arm")
    found: dict[str, float] = {}
    for line in text.splitlines():
        s = line.strip().strip("*").strip().lstrip("-•").strip().replace("**", "")
        m = re.match(rf"^(below|above)?\s*({_NUM})\s*[:=]\s*({_NUM})\s*%?\s*$", s, flags=re.IGNORECASE)
        if not m:
            continue
        word, num, prob = (m.group(1) or "").lower(), m.group(2).replace(",", "."), float(m.group(3).replace(",", "."))
        label = _match_label(q, word, num)
        if label is not None:
            found[label] = prob
    if not found:
        raise ParseError("no value lines found")
    if any(p < 0 for p in found.values()):
        raise ParseError("negative probability")
    total = sum(found.values())
    if not (90 <= total <= 110 or 0.9 <= total <= 1.1):
        raise ParseError(f"value probabilities add up to {total:g}, not 100%")
    return {lb: found.get(lb, 0.0) / total for lb in lbs}


def _match_label(q: QuestionSnapshot, word: str, num: str) -> str | None:
    vs = values(q) or []
    if word == "below":
        return BELOW if q.open_lower_bound and vs and _same(num, vs[0]) else None
    if word == "above":
        return ABOVE if q.open_upper_bound and vs and _same(num, vs[-1]) else None
    return next((v for v in vs if _same(num, v)), None)


def _same(a: str, b: str) -> bool:
    try:
        return math.isclose(float(a), float(b), rel_tol=0, abs_tol=1e-9)
    except ValueError:
        return False


def floored(probs: Sequence[float], floor: float = FLOOR) -> list[float]:
    """Mix with a uniform so every entry is at least `floor` and the total is 1: p' = floor + (1 − m·floor)·p/Σp."""
    m, total = len(probs), sum(probs)
    if m * floor >= 1:
        raise ValueError("floor too high for this many values")
    return [floor + (1 - m * floor) * p / total for p in probs]


def aggregate(q: QuestionSnapshot, preds: Sequence[dict[str, float]]) -> dict[str, Any]:
    """Member mean per label, floor 0.5% per label, normalised. Stored as the record's aggregate."""
    lbs = labels(q) or []
    mean = [sum(p[lb] for p in preds) / len(preds) for lb in lbs]
    return {"kind": KIND, "labels": lbs, "probabilities": floored(mean)}


def is_per_value(aggregate_: Any) -> bool:
    return isinstance(aggregate_, dict) and aggregate_.get("kind") == KIND


def cdf_from_values(aggregate_: dict[str, Any], open_lower: bool, open_upper: bool) -> list[float]:
    """The cdf Metaculus gets (cdf_size = values + 1 points): cdf[0] = P(below), cdf[k] = cdf[k−1] + P(value k), cdf[-1] =
    1 − P(above). A closed bound has no label, so its end is exactly 0 or 1."""
    probs = dict(zip(aggregate_["labels"], aggregate_["probabilities"]))
    inner = [p for lb, p in probs.items() if lb not in (BELOW, ABOVE)]
    cdf = [probs.get(BELOW, 0.0) if open_lower else 0.0]
    for p in inner:
        cdf.append(cdf[-1] + p)
    if not open_upper:
        cdf[-1] = 1.0
    return [min(1.0, max(0.0, c)) for c in cdf]
