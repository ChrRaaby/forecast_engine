"""Thin async client for OpenRouter chat completions that records tokens and the provider-reported cost."""
from __future__ import annotations

import os
import time
from collections.abc import Mapping
from typing import Protocol

from openai import AsyncOpenAI

from .clock import Clock
from .schema import LlmCall

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class LlmClient(Protocol):
    async def complete(
        self, *, model: str, prompt: str, purpose: str, max_tokens: int, reasoning_effort: str | None = None
    ) -> LlmCall: ...


class OpenRouterClient:
    def __init__(self, clock: Clock, api_key: str | None = None, timeout_s: float = 240.0,
                 pinned_hosts: Mapping[str, str] | None = None) -> None:
        """`pinned_hosts` (B-93, experiment runs only; the live bot passes none): model id -> the one OpenRouter host that may
        serve it, with fallbacks off, so both arms of an experiment run on the same build of an open-weight model."""
        key = api_key or os.getenv("OPENROUTER_API_KEY")
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY is not set")
        self._clock = clock
        self._pins = dict(pinned_hosts or {})
        self._client = AsyncOpenAI(base_url=OPENROUTER_BASE_URL, api_key=key, timeout=timeout_s, max_retries=2)

    async def complete(
        self, *, model: str, prompt: str, purpose: str, max_tokens: int, reasoning_effort: str | None = None
    ) -> LlmCall:
        params: dict = {"max_tokens": max_tokens}
        extra_body: dict = {"usage": {"include": True}}  # ask OpenRouter for the exact $ cost in `usage`
        if reasoning_effort:
            params["reasoning_effort"] = reasoning_effort
            extra_body["reasoning"] = {"effort": reasoning_effort}
        pin = self._pins.get(model)
        if pin:
            extra_body["provider"] = {"order": [pin], "allow_fallbacks": False}
        call = LlmCall(
            purpose=purpose,
            provider="openrouter",
            model=model,
            params=params,
            prompt=prompt,
            requested_at=self._clock.now(),
        )
        if pin:
            call.extra["pinned_host"] = pin
        start = time.monotonic()
        try:
            resp = await self._client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                extra_body=extra_body,
            )
        except Exception as e:  # recorded, not raised: one failed forecaster must not sink the question
            call.error = f"{type(e).__name__}: {e}"
            call.latency_s = time.monotonic() - start
            return call
        call.latency_s = time.monotonic() - start
        call.output = (resp.choices[0].message.content or "") if resp.choices else ""
        call.extra["served_model"] = resp.model
        # Provenance: which host OpenRouter routed this call to (hosts can run different builds of one model) and the call's id.
        # Set only when the response carries them; never guessed.
        upstream = (getattr(resp, "model_extra", None) or {}).get("provider")
        if upstream:
            call.extra["upstream_provider"] = upstream
        if getattr(resp, "id", None):
            call.extra["generation_id"] = resp.id
        call.extra["finish_reason"] = resp.choices[0].finish_reason if resp.choices else None
        if resp.usage is not None:
            call.tokens_in = resp.usage.prompt_tokens
            call.tokens_out = resp.usage.completion_tokens
            cost = (resp.usage.model_extra or {}).get("cost")
            call.cost_usd = float(cost) if cost is not None else None
        if not call.output.strip():
            call.error = f"empty completion (finish_reason={call.extra['finish_reason']})"
        return call
