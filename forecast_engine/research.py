"""gather_research(question, clock, config) -> ResearchBundle.

Live providers (M1):
  - asknews_latest: AskNews "latest news" search, exactly one call per question (grant: 1,000 calls/month;
    archive calls are reserved for backtests, research/03 addendum b). Items carry publication dates.
  - gemini_grounded: Gemini with Google Search grounding via Google AI Studio (free quota). Items are undated,
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

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


async def gather_research(q: QuestionSnapshot, clock: Clock, cfg: ResearchConfig) -> ResearchBundle:
    bundle = ResearchBundle(as_of=clock.now(), providers=[], items=[], calls=[])
    for provider in cfg.providers:
        if provider == "asknews_latest":
            if not (os.getenv("ASKNEWS_CLIENT_ID") and os.getenv("ASKNEWS_SECRET")):
                bundle.errors.append("asknews_latest skipped: ASKNEWS_CLIENT_ID/ASKNEWS_SECRET not set")
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
        async with AsyncAskNewsSDK(
            client_id=os.getenv("ASKNEWS_CLIENT_ID"),
            client_secret=os.getenv("ASKNEWS_SECRET"),
            scopes={"news"},
        ) as ask:
            resp = await ask.news.search_news(
                query=q.question_text,
                n_articles=cfg.asknews_n_articles,
                return_type="dicts",
                strategy="latest news",
            )
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
        call.extra["asknews_calls"] = 1
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
        cost_usd=0.0,  # free AI Studio quota
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
