"""Binary polarity check (2026-10-05): does a member's "Probability: ZZ%" mean P(question resolves YES)?

The template prompt asks for "Probability: ZZ%" without saying of what. An audit of 111 live binary answers found 2 where Gemini
Flash-Lite gave the probability of NO (posts 45898 and 45925). The median of three absorbed both, but two flipped members on one
question would publish the wrong side. A cheap auditor model reads each binary member's text; a member judged to answer for NO is
excluded from the median (not flipped, in case the auditor is wrong) and the record is flagged. If the check itself fails, the
member is kept: the check guards against a rare bug and must not take forecasts down with it.

This doesn't change what the forecasting models see; clearer prompt wording is tested separately (EXP-002).
"""
from __future__ import annotations

import re

from .llm import LlmClient
from .schema import LlmCall, QuestionSnapshot

PROMPT = """A forecaster was asked a yes/no question and ended with a final line "Probability: ZZ%".
Decide what that final number refers to, judging from the rationale.

Question: {question}
Resolution criteria (excerpt): {criteria}

Forecaster's text (end):
{text}

Answer with one JSON object: {{"refers_to": "YES" | "NO" | "OTHER" | "UNCLEAR", "why": "one short sentence"}}
YES = the probability that the question resolves YES. NO = the probability it resolves NO. OTHER = some other event."""

_REFERS = re.compile(r'"refers_to"\s*:\s*"(YES|NO|OTHER|UNCLEAR)"')


def check_prompt(q: QuestionSnapshot, output: str) -> str:
    return PROMPT.format(question=q.question_text, criteria=(q.resolution_criteria or "")[:700], text=output[-2500:])


def parse_check(output: str) -> str | None:
    m = _REFERS.search(output or "")
    return m.group(1) if m else None


async def check_member(q: QuestionSnapshot, output: str, llm: LlmClient, model: str) -> tuple[str | None, LlmCall]:
    """(verdict or None if the check failed, the auditor call)."""
    call = await llm.complete(model=model, prompt=check_prompt(q, output), purpose="polarity_check", max_tokens=4000,
                              reasoning_effort="low")
    return (None if call.error else parse_check(call.output)), call
