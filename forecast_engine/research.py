"""gather_research(question, clock, config) -> ResearchBundle.

Live providers (M1):
  - asknews_latest: AskNews "latest news" search, exactly one call per question (grant: 1,000 calls/month;
    archive calls are reserved for backtests, research/03 addendum b). Items carry publication dates.
  - gemini_grounded: Gemini with Google Search grounding via Google AI Studio (billing enabled). Items are undated,
    so this provider can never be used in backtests (evaluation protocol T2).
Providers without credentials are skipped and noted in `errors`. The as-of guard and research cache arrive in M2 (B-28).
"""
from __future__ import annotations

import asyncio
import os
import time

import requests

from .clock import Clock
from .config import ResearchConfig
from .prompts import research_prompt
from .schema import LlmCall, QuestionSnapshot, ResearchBundle, ResearchItem

_ASKNEWS_LOCK = asyncio.Lock()  # serialises AskNews calls across concurrently researched questions
_asknews_last_call = [float("-inf")]

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


async def gather_research(q: QuestionSnapshot, clock: Clock, cfg: ResearchConfig) -> ResearchBundle:
    bundle = ResearchBundle(as_of=clock.now(), providers=[], items=[], calls=[])
    for provider in cfg.providers:
        if provider == "asknews_latest":
            if not ((os.getenv("ASKNEWS_CLIENT_ID") and os.getenv("ASKNEWS_SECRET")) or os.getenv("ASKNEWS_API_KEY")):
                bundle.errors.append("asknews_latest skipped: no AskNews credentials (ASKNEWS_API_KEY or ASKNEWS_CLIENT_ID/SECRET)")
                continue
            await _asknews_latest(q, clock, cfg, bundle)
        elif provider == "gemini_grounded":
            if not os.getenv("GEMINI_API_KEY"):
                bundle.errors.append("gemini_grounded skipped: GEMINI_API_KEY not set")
                continue
            await _gemini_grounded(q, clock, cfg, bundle)
        else:
            bundle.errors.append(f"unknown research provider {provider!r}")
    return bundle


async def _asknews_latest(q: QuestionSnapshot, clock: Clock, cfg: ResearchConfig, bundle: ResearchBundle) -> None:
    from asknews_sdk import AsyncAskNewsSDK

    call = LlmCall(
        purpose="research",
        provider="asknews",
        model="news/search:latest news",
        params={"n_articles": cfg.asknews_n_articles, "strategy": "latest news"},
        prompt=q.question_text,
        requested_at=clock.now(),
    )
    start = time.monotonic()
    try:
        if os.getenv("ASKNEWS_CLIENT_ID") and os.getenv("ASKNEWS_SECRET"):
            creds = {"client_id": os.getenv("ASKNEWS_CLIENT_ID"), "client_secret": os.getenv("ASKNEWS_SECRET")}
        else:
            creds = {"api_key": os.getenv("ASKNEWS_API_KEY")}
        resp = None
        async with _ASKNEWS_LOCK:
            for attempt in range(2):  # one retry after a rate-limit error
                wait = cfg.asknews_min_interval_s - (time.monotonic() - _asknews_last_call[0])
                if wait > 0:
                    await asyncio.sleep(wait)
                try:
                    async with AsyncAskNewsSDK(**creds, scopes={"news"}) as ask:
                        call.extra["asknews_calls"] = call.extra.get("asknews_calls", 0) + 1
                        resp = await ask.news.search_news(
                            query=q.question_text,
                            n_articles=cfg.asknews_n_articles,
                            return_type="dicts",
                            strategy="latest news",
                        )
                    break
                except Exception as e:
                    if "RateLimit" not in type(e).__name__ or attempt == 1:
                        raise
                finally:
                    _asknews_last_call[0] = time.monotonic()
        articles = resp.as_dicts or []
        for a in articles:
            bundle.items.append(
                ResearchItem(
                    source="asknews_latest",
                    text=a.summary,
                    url=str(a.article_url),
                    title=a.eng_title,
                    published_at=a.pub_date,
                )
            )
        call.extra["n_articles_returned"] = len(articles)
        bundle.providers.append("asknews_latest")
    except Exception as e:
        call.error = f"{type(e).__name__}: {e}"
        bundle.errors.append(f"asknews_latest failed: {call.error}")
    call.latency_s = time.monotonic() - start
    bundle.calls.append(call)


async def _gemini_grounded(q: QuestionSnapshot, clock: Clock, cfg: ResearchConfig, bundle: ResearchBundle) -> None:
    prompt = research_prompt(q)
    call = LlmCall(
        purpose="research",
        provider="gemini",
        model=cfg.gemini_model,
        params={"tools": ["google_search"]},
        prompt=prompt,
        requested_at=clock.now(),
    )
    start = time.monotonic()
    try:
        data = await asyncio.to_thread(_post_gemini, cfg.gemini_model, prompt)
        cand = (data.get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
        meta = cand.get("groundingMetadata", {}) or {}
        sources = [c.get("web", {}) for c in meta.get("groundingChunks", []) or []]
        usage = data.get("usageMetadata", {}) or {}
        call.output = text
        call.tokens_in = usage.get("promptTokenCount")
        call.tokens_out = usage.get("candidatesTokenCount")
        thinking = usage.get("thoughtsTokenCount") or 0
        call.extra["thinking_tokens"] = thinking
        call.cost_usd = (
            (call.tokens_in or 0) * cfg.gemini_usd_per_m_in + ((call.tokens_out or 0) + thinking) * cfg.gemini_usd_per_m_out
        ) / 1e6
        call.extra["cost_note"] = "estimated from tokens at list price; grounding fee not included"
        call.extra["search_queries"] = meta.get("webSearchQueries", [])
        call.extra["sources"] = [{"title": s.get("title"), "uri": s.get("uri")} for s in sources]
        if not text.strip():
            raise RuntimeError(f"empty Gemini response (finishReason={cand.get('finishReason')})")
        source_list = "\n".join(f"- {s.get('title')}" for s in sources if s.get("title"))
        bundle.items.append(
            ResearchItem(
                source="gemini_grounded",
                text=text + (f"\n\nSources consulted:\n{source_list}" if source_list else ""),
                published_at=None,
            )
        )
        bundle.providers.append("gemini_grounded")
    except Exception as e:
        call.error = f"{type(e).__name__}: {e}"
        bundle.errors.append(f"gemini_grounded failed: {call.error}")
    call.latency_s = time.monotonic() - start
    bundle.calls.append(call)


def _post_gemini(model: str, prompt: str) -> dict:
    resp = requests.post(
        GEMINI_URL.format(model=model),
        headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"], "Content-Type": "application/json"},
        json={"contents": [{"parts": [{"text": prompt}]}], "tools": [{"google_search": {}}]},
        timeout=120,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Gemini HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()
