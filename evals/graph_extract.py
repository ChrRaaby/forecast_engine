"""Offline reasoning-graph extraction from stored forecast records (B-46). Doesn't change forecasts.

Per member per binary question, a cheap model reads that member's text (`ForecasterOutput.call.output`) and the research bundle
and returns a graph as JSON; forecast_engine/graph.py then checks it (quotes, tracing, final probability from the record).
Driven by tools/extract_graphs.py.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests

from forecast_engine.graph import (
    EXTRACTOR_RESPONSE_SCHEMA,
    GRAPH_SCHEMA_VERSION,
    MalformedGraph,
    build_graph,
    graph_features,
)

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


@dataclass(frozen=True)
class ExtractorConfig:
    # Google AI Studio id (GEMINI_API_KEY); same model as the research step, verify with tools/check_models.py.
    model: str = "gemini-3.5-flash-lite"
    # List price used for the estimate, as in forecast_engine/config.py ResearchConfig (Google returns no cost).
    usd_per_m_in: float = 0.30
    usd_per_m_out: float = 2.50  # thinking tokens are billed as output
    thinking_level: str = "low"
    max_item_chars: int = 4000  # per research item shown to the extractor
    prompt_version: str = "graph-extract-v2"  # v2: source fields only when the member cites them


def extractor_prompt(question_text: str, rationale: str, research_items: list[dict[str, Any]], max_item_chars: int) -> str:
    blocks = []
    for i, it in enumerate(research_items):
        head = f"[{i}] source={it.get('source')}"
        for k in ("title", "url", "published_at"):
            if it.get(k):
                head += f" | {k}={it[k]}"
        blocks.append(f"{head}\n{(it.get('text') or '').strip()[:max_item_chars]}")
    research = "\n\n".join(blocks) or "(no research items)"
    return f"""You map a forecaster's written reasoning into a structured graph. You do not forecast and you do not judge.
Report only what the FORECASTER'S TEXT says. Do not add drivers, evidence or numbers that are not in it.

Question: {question_text}

FORECASTER'S TEXT (the only source for nodes):
<<<
{rationale}
>>>

RESEARCH ITEMS the forecaster was given (only to identify where a piece of evidence came from):
{research}

Return JSON with:
- base_rate: the outside view / reference class the forecaster states (e.g. "historically X% of such cases..."). status "stated"
  only if the text explicitly gives a base rate or reference class; otherwise status "none", reference_class "", value null,
  quote "". value is a probability 0-1 if the text gives a number, else null.
- drivers: the main factors (cruxes) the forecaster says move the probability. id "d1", "d2", ...; direction "up" if the
  factor raises the probability of YES, "down" if it lowers it; strength 1 (minor) to 3 (decisive) as the text weighs it.
- evidence: concrete facts the forecaster cites. claim = the fact in a few words; driver_id = the driver it supports (or null);
  direction and strength as for drivers; source_title / source_url / source_date only if the FORECASTER'S TEXT names them
  (copy them as written there), else null; research_item = index of the research item the fact comes from, or null if it isn't in
  the research (e.g. the forecaster's own background knowledge).
- scenarios: the YES/NO (or other) scenarios the text describes, with the probability the text gives each, else null.
- final: stated_shift_from_base = how the text says the final number moves away from the base rate (or null); quote = the
  sentence with the final probability.

Every quote must be ONE sentence or line copied EXACTLY, character for character, from the FORECASTER'S TEXT (not from the
research, not paraphrased, not shortened with "..."). A node whose quote is not found verbatim in the text is discarded."""


def post_gemini(model: str, prompt: str, thinking_level: str) -> dict:
    resp = requests.post(
        GEMINI_URL.format(model=model),
        headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"], "Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": EXTRACTOR_RESPONSE_SCHEMA,
                "temperature": 0,
                "thinkingConfig": {"thinkingLevel": thinking_level},
            },
        },
        timeout=120,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Gemini HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def parse_json_text(text: str) -> Any:
    """Parse the model's JSON, tolerating a ```json fence; raise MalformedGraph otherwise."""
    t = text.strip()
    m = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", t, flags=re.S)
    if m:
        t = m.group(1)
    try:
        return json.loads(t)
    except json.JSONDecodeError as e:
        raise MalformedGraph(f"not valid JSON ({e}): {t[:200]}") from e


def estimate_tokens(prompt: str) -> int:
    return len(prompt) // 4  # rough chars-per-token for English


def estimate_cost(prompt: str, cfg: ExtractorConfig, out_tokens: int = 2500) -> float:
    """Pre-run estimate: prompt tokens from length, a fixed allowance for answer + thinking."""
    return (estimate_tokens(prompt) * cfg.usd_per_m_in + out_tokens * cfg.usd_per_m_out) / 1e6


def binary_members(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Members with a parsed numeric prediction and non-empty text. Excluded members are kept (flagged), since their text is
    part of what the inspection should show."""
    if record["question"]["question_type"] != "binary":
        return []
    return [f for f in record["forecasters"]
            if isinstance(f.get("prediction"), (int, float)) and (f.get("call") or {}).get("output", "").strip()]


def extract_member(record: dict[str, Any], member: dict[str, Any], cfg: ExtractorConfig,
                   post: Callable[[str, str, str], dict] = post_gemini) -> dict[str, Any]:
    items = record["research"]["items"]
    rationale = member["call"]["output"]
    prompt = extractor_prompt(record["question"]["question_text"], rationale, items, cfg.max_item_chars)
    out: dict[str, Any] = {"model": member["model"], "final_probability": member["prediction"],
                           "excluded": member.get("excluded"), "graph": None, "features": None, "error": None,
                           "call": {"tokens_in": None, "tokens_out": None, "thinking_tokens": None, "cost_usd": None,
                                    "latency_s": None}, "raw_output": None}
    start = time.monotonic()
    try:
        data = post(cfg.model, prompt, cfg.thinking_level)
    except Exception as e:  # recorded, not raised: one failed call must not sink the batch
        out["error"] = f"{type(e).__name__}: {e}"
        out["call"]["latency_s"] = round(time.monotonic() - start, 2)
        return out
    out["call"]["latency_s"] = round(time.monotonic() - start, 2)
    usage = data.get("usageMetadata", {}) or {}
    tin, tout, think = usage.get("promptTokenCount") or 0, usage.get("candidatesTokenCount") or 0, usage.get("thoughtsTokenCount") or 0
    out["call"].update(tokens_in=tin, tokens_out=tout, thinking_tokens=think,
                       cost_usd=round((tin * cfg.usd_per_m_in + (tout + think) * cfg.usd_per_m_out) / 1e6, 6))
    cand = (data.get("candidates") or [{}])[0]
    text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []) if not p.get("thought"))
    out["raw_output"] = text
    try:
        g = build_graph(parse_json_text(text), rationale=rationale, final_probability=member["prediction"],
                        research_items=items)
    except MalformedGraph as e:
        out["error"] = f"malformed: {e} (finishReason={cand.get('finishReason')})"
        return out
    out["graph"] = g.to_dict()
    out["features"] = graph_features(g)
    return out


def extract_record(record: dict[str, Any], record_path: str, cfg: ExtractorConfig,
                   post: Callable[[str, str, str], dict] = post_gemini) -> dict[str, Any]:
    members = [extract_member(record, m, cfg, post) for m in binary_members(record)]
    return {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "extractor": {"model": cfg.model, "prompt_version": cfg.prompt_version, "thinking_level": cfg.thinking_level},
        "record": record_path,
        "question_id": record["question"]["question_id"],
        "question_text": record["question"]["question_text"],
        "config_version": record["config_version"],
        "as_of": record["as_of"],
        "aggregate": record["aggregate"],
        "n_research_items": len(record["research"]["items"]),
        "members": members,
        "cost_usd": round(sum(m["call"]["cost_usd"] or 0.0 for m in members), 6),
        "errors": [f"{m['model']}: {m['error']}" for m in members if m["error"]],
    }
