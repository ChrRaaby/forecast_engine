"""followup(question, bundle, clock, research config, llm) -> enlarged copy of the bundle (B-17; EXP-008 research desk).

After the normal research is gathered, a cheap critic model reads the brief and lists up to `max_gaps` gaps ("gaps" mode: missing
facts, stakeholders' views, base-rate data, contrary evidence; "against" mode: evidence against the brief's leading conclusion).
Each gap becomes one targeted Gemini grounded search. The answers are added to a copy of the bundle as one separately labelled
section ("research_followup" items), so the forecasting prompt template is unchanged and the record stays replayable: the enlarged
bundle is what the shadow record stores, and `ResearchBundle.as_prompt_text()` rebuilds the exact prompt from it.

"control" mode is the compute-matched control (protocol §5): no critic, the same number of searches with generic,
question-independent instructions, so a win can't come from merely searching more. With rounds >= 2 the critic sees the enlarged
brief after each round and ends the loop early by returning no gaps.

Every call (critic and searches) is recorded in the bundle's `calls` with prompt, model, tokens, cost, search queries and source
titles/URLs. A spend cap per question stops further searches. Nothing here ever raises: on failure the copy holds no new items
(so the prompt is unchanged), the calls already paid for, and the error. Only the shadow runner calls this; the live path never does.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import replace
from typing import Awaitable, Callable

from .clock import Clock, today_str
from .config import FollowupConfig, ResearchConfig
from .llm import LlmClient
from .research import _post_gemini
from .schema import LlmCall, QuestionSnapshot, ResearchBundle, ResearchItem

SOURCE = "research_followup"
# The section's label is the same in every mode, so the arms differ only in what was searched for.
SECTION_TITLE = "Additional research"
SECTION_TEXT = ("Targeted follow-up searches made after the research above. Each entry names what was searched for; "
                "the searches can return nothing new.")

CRITIC_PROMPTS = {
    "gaps": """You review the research brief that a team of forecasters will receive for the question below. Do not forecast.
List the most important things the brief is MISSING that would change a careful forecast: missing facts or recent developments,
views of key stakeholders or decision makers, base-rate or historical data for similar situations, and evidence that cuts against
what the brief suggests. Each item must be answerable by one web search. Skip anything the brief already covers.""",
    "against": """You review the research brief that a team of forecasters will receive for the question below. Do not forecast.
First state in one sentence the conclusion the brief leans towards. Then list web searches that would find the strongest evidence
AGAINST that conclusion: facts, developments, stakeholders' views or historical cases that point the other way. Each item must be
answerable by one web search. Skip anything the brief already covers.""",
}

CRITIC_TEMPLATE = """{instructions}

Question:
{question}

Resolution criteria:
{criteria}

{fine_print}

Today is {today}.

Research brief:
{brief}

Answer with one JSON object and nothing after it:
{{{lead}"gaps": ["...", "..."]}}
List at most {max_gaps} items, most important first, each one sentence. If nothing important is missing, return {{{lead}"gaps": []}}."""

SEARCH_TEMPLATE = """You are an assistant to a superforecaster. Do not produce a forecast.

The superforecaster is working on this question:
{question}

This question's outcome will be determined by the specific criteria below:
{criteria}

Today is {today}.

Search the web and report on this point: {query}

Give concrete facts with their dates and figures, and name the sources. If you find nothing relevant, say so plainly."""

# Control mode: the standard research request asked again in different words. Generic on purpose (the same for every question).
CONTROL_QUERIES = (
    "a concise but detailed rundown of the most relevant recent news on this question",
    "the current state of affairs on this question and how it got here",
    "the key facts and figures someone forecasting this question should know",
    "the most recent developments related to this question, with their dates",
    "what news sources currently report about the subject of this question",
    "background on the people, organisations and processes involved in this question",
)

Search = Callable[[str, str], Awaitable[dict]]  # (gemini model, prompt) -> raw generateContent response


async def _gemini_search(model: str, prompt: str) -> dict:
    return await asyncio.to_thread(_post_gemini, model, prompt)


def critic_prompt(q: QuestionSnapshot, brief: str, today: str, mode: str, max_gaps: int) -> str:
    lead = '"leading_conclusion": "...", ' if mode == "against" else ""
    return CRITIC_TEMPLATE.format(instructions=CRITIC_PROMPTS[mode], question=q.question_text, criteria=q.resolution_criteria,
                                  fine_print=q.fine_print, today=today, brief=brief, lead=lead, max_gaps=max_gaps)


def search_prompt(q: QuestionSnapshot, query: str, today: str) -> str:
    return SEARCH_TEMPLATE.format(question=q.question_text, criteria=q.resolution_criteria, today=today, query=query)


def parse_gaps(output: str, max_gaps: int) -> list[str] | None:
    """The critic's gap list (deduplicated, at most max_gaps, each <= 300 chars), or None if there is no valid JSON list."""
    for m in re.finditer(r"\{.*\}", output or "", re.S):
        try:
            obj = json.loads(m.group(0))
        except ValueError:
            continue
        gaps = obj.get("gaps") if isinstance(obj, dict) else None
        if not isinstance(gaps, list):
            continue
        out: list[str] = []
        for g in gaps:
            g = " ".join(str(g).split())[:300]
            if g and g.lower() not in {x.lower() for x in out}:
                out.append(g)
        return out[:max_gaps]
    return None


async def followup(
    q: QuestionSnapshot,
    bundle: ResearchBundle,
    clock: Clock,
    cfg: ResearchConfig,
    llm: LlmClient,
    search: Search | None = None,
) -> ResearchBundle:
    """A copy of `bundle` with the follow-up section and calls added. `bundle` itself is never modified."""
    out = replace(bundle, providers=list(bundle.providers), items=list(bundle.items), calls=list(bundle.calls),
                  errors=list(bundle.errors))
    fcfg = cfg.followup
    if fcfg is None:
        return out
    try:
        await _run(q, out, clock, cfg, fcfg, llm, search or _gemini_search)
    except Exception as e:  # never fail the forecast: drop what was added, keep the paid calls and the error
        out.items = list(bundle.items)
        out.providers = list(bundle.providers)
        out.errors.append(f"{SOURCE} failed: {type(e).__name__}: {e}")
    return out


async def _run(q: QuestionSnapshot, out: ResearchBundle, clock: Clock, cfg: ResearchConfig, fcfg: FollowupConfig,
               llm: LlmClient, search: Search) -> None:
    if not os.getenv("GEMINI_API_KEY") and search is _gemini_search:
        out.errors.append(f"{SOURCE} skipped: GEMINI_API_KEY not set")
        return
    today = today_str(clock)
    n_before = len(out.calls)
    spent = 0.0
    search_costs: list[float] = []
    found: list[ResearchItem] = []
    searched: list[str] = []
    stop: str | None = None
    for rnd in range(1, fcfg.rounds + 1):
        if fcfg.critic_mode == "control":
            queries = [CONTROL_QUERIES[(len(searched) + i) % len(CONTROL_QUERIES)] for i in range(fcfg.max_gaps)]
        else:
            if spent > 0 and spent + max(search_costs, default=fcfg.search_cost_prior_usd) > fcfg.max_cost_usd_per_q:
                stop = f"cost cap ${fcfg.max_cost_usd_per_q:.2f} reached before round {rnd}"
                break
            brief = ResearchBundle(as_of=out.as_of, providers=[], items=out.items + found, calls=[]).as_prompt_text()
            call = await llm.complete(model=fcfg.critic_model, prompt=critic_prompt(q, brief, today, fcfg.critic_mode, fcfg.max_gaps),
                                      purpose="research_followup_critic", max_tokens=fcfg.critic_max_tokens,
                                      reasoning_effort=fcfg.critic_reasoning_effort)
            call.extra["round"] = rnd
            out.calls.append(call)
            spent += call.cost_usd or 0.0
            if call.error:
                raise RuntimeError(f"critic call failed: {call.error}")
            gaps = parse_gaps(call.output, fcfg.max_gaps)
            if gaps is None:
                raise RuntimeError("critic answer has no JSON gap list")
            gaps = [g for g in gaps if g.lower() not in {s.lower() for s in searched}]
            call.extra["gaps"] = gaps
            if not gaps:
                stop = f"critic found no further gaps in round {rnd}"
                break
            queries = gaps
        for query in queries:
            expected = max(search_costs, default=fcfg.search_cost_prior_usd)
            if spent + expected > fcfg.max_cost_usd_per_q:
                stop = (f"cost cap ${fcfg.max_cost_usd_per_q:.2f}: spent ${spent:.4f}, next search expected ${expected:.4f}; "
                        f"{len(searched)} searches made")
                break
            call, item = await _search(q, query, len(searched) + 1, rnd, today, clock, cfg, fcfg, search)
            out.calls.append(call)
            searched.append(query)
            cost = call.cost_usd or 0.0
            spent += cost
            search_costs.append(cost)
            if item is not None:
                found.append(item)
            else:
                out.errors.append(f"{SOURCE} search {len(searched)} failed: {call.error}")
        if stop:
            break
    own = out.calls[n_before:]
    if own:  # on the step's last call; the live calls in the copy are shared objects and never touched
        own[-1].extra["followup_summary"] = {
            "mode": fcfg.critic_mode, "searches": len(searched), "sections_added": len(found), "spent_usd": round(spent, 6),
            "stopped": stop or "all rounds done",
        }
    if stop and stop.startswith("cost cap"):
        out.errors.append(f"{SOURCE} stopped: {stop}")
    if found:
        out.items.append(ResearchItem(source=SOURCE, title=SECTION_TITLE, text=SECTION_TEXT))
        out.items.extend(found)
        out.providers.append(SOURCE)
        out.as_of = clock.now()  # the brief now holds searches made up to this moment


async def _search(q: QuestionSnapshot, query: str, n: int, rnd: int, today: str, clock: Clock, cfg: ResearchConfig,
                  fcfg: FollowupConfig, search: Search) -> tuple[LlmCall, ResearchItem | None]:
    prompt = search_prompt(q, query, today)
    call = LlmCall(purpose="research_followup_search", provider="gemini", model=cfg.gemini_model,
                   params={"tools": ["google_search"]}, prompt=prompt, requested_at=clock.now(),
                   extra={"round": rnd, "query": query, "mode": fcfg.critic_mode})
    start = time.monotonic()
    item = None
    try:
        data = await search(cfg.gemini_model, prompt)
        cand = (data.get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
        meta = cand.get("groundingMetadata", {}) or {}
        sources = [c.get("web", {}) for c in meta.get("groundingChunks", []) or []]
        usage = data.get("usageMetadata", {}) or {}
        call.output = text
        call.tokens_in = usage.get("promptTokenCount")
        call.tokens_out = usage.get("candidatesTokenCount")
        thinking = usage.get("thoughtsTokenCount") or 0
        queries = meta.get("webSearchQueries", []) or []
        token_cost = ((call.tokens_in or 0) * cfg.gemini_usd_per_m_in
                      + ((call.tokens_out or 0) + thinking) * cfg.gemini_usd_per_m_out) / 1e6
        fee = len(queries) * fcfg.grounding_usd_per_query
        call.cost_usd = token_cost + fee
        call.extra.update({
            "thinking_tokens": thinking, "token_cost_usd": token_cost, "grounding_fee_usd": fee,
            "cost_note": "tokens at list price + grounding fee per search query at list price (free tier ignored)",
            "search_queries": queries, "sources": [{"title": s.get("title"), "uri": s.get("uri")} for s in sources],
        })
        if not text.strip():
            raise RuntimeError(f"empty Gemini response (finishReason={cand.get('finishReason')})")
        source_list = "\n".join(f"- {s.get('title')}" for s in sources if s.get("title"))
        item = ResearchItem(source=SOURCE, title=f"Follow-up search {n}: {query}",
                            text=text + (f"\n\nSources consulted:\n{source_list}" if source_list else ""))
    except Exception as e:
        call.error = f"{type(e).__name__}: {e}"
    call.latency_s = time.monotonic() - start
    return call, item
