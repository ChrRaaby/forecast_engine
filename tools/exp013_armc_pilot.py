"""EXP-013 arm C pilot: measure a stronger Gemini model with Google Search grounding as the research-brief writer.

Standalone and offline from the live bot: it reads question snapshots from archived live records (data/runs/), calls each
candidate model once per question with the live research prompt plus arm C's strict rules, and reports NUMBERS ONLY:
Google search queries per call, tokens, cost at list price (tokens and grounding), latency, whether the brief carries links,
dates and "NOT FOUND". No forecasts, no scores. Nothing in forecast_engine/ or main.py is changed or imported for writing.

    # free: list the Gemini models the key can see, pick questions, count today's grounded queries from the archive, estimate
    poetry run python tools/exp013_armc_pilot.py --dry-run
    # paid (~$1, needs Christian's "go"): 5 questions x the candidate models
    poetry run python tools/exp013_armc_pilot.py --models gemini-3.5-flash,gemini-3.1-pro-preview --n 5

Outputs: data/experiments/EXP-013/armC-pilot-<run>/ (private data repo): calls.jsonl (incl. the brief text), summary.csv,
report.md. The console prints counts and costs only, never research text.

Billing note (ai.google.dev/gemini-api/docs/pricing, read 2026-11-11): Gemini 3 models share 5,000 free grounded "prompts" a
month on the paid tier, then $14 per 1,000 search QUERIES; one request can run several queries. Whether the free 5,000 counts
prompts or queries, and whether a model's SKU falls under it, is only visible in the Google Cloud billing console. Costs here
are therefore given twice: tokens only, and tokens + every query at list price (the upper bound, as research_followup does).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.replay import live_records, snapshot_from_dict  # noqa: E402
from forecast_engine.prompts import research_prompt  # noqa: E402
from forecast_engine.schema import QuestionSnapshot  # noqa: E402

RUNS = ROOT / "data" / "runs"
OUT = ROOT / "data" / "experiments" / "EXP-013"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000"
PROMPT_VERSION = "exp013-c-writer-pilot-2026-11-11"

# List prices, USD per 1M tokens (output includes thinking), paid tier, prompts <= 200k tokens.
# Source: ai.google.dev/gemini-api/docs/pricing, read 2026-11-11. Re-check before the card freezes.
PRICES: dict[str, tuple[float, float]] = {
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-3.1-pro-preview": (2.00, 12.00),
    "gemini-3-flash-preview": (0.50, 3.00),
    "gemini-3.5-flash-lite": (0.30, 2.50),  # the live writer; price as in forecast_engine/config.py
}
GROUNDING_USD_PER_QUERY = 0.014  # $14 per 1,000 search queries for Gemini 3 after the free allowance
CAP_PER_Q = 0.05  # the card's cap for arm C, at list price
FREE_PER_MONTH = 5000
PRIOR_CALL_USD = 0.15  # worst-case guess per call before any is made (tokens + ~5 queries at list price)

STRICT_RULES = """
Rules for this rundown (follow all of them):
1. Every number, percentage, count, price, poll figure or date-specific fact you state must be followed by its source link
   (the full URL) and the date that source was published or last updated, like this: (source: https://..., 2026-11-03).
2. If you cannot find a value, write "NOT FOUND: <what you looked for>". Never estimate, extrapolate, round from memory or
   fill in a number you did not find in a source.
3. If the resolution criteria or fine print name a source (a website, dataset, report, agency or official page), look for it
   first and start with a section headed "Resolution source" that states its latest value and that value's date, with the
   link. If you cannot retrieve it, write "Resolution source: NOT FOUND".
4. Today is {today}. Prefer the most recent information and say how old each key fact is.
5. Do not give a forecast or a probability.
"""

URL_RE = re.compile(r"https?://\S+")
DATE_RE = re.compile(r"\b(19|20)\d{2}-\d{2}-\d{2}\b|\b\d{1,2} (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* (19|20)\d{2}\b"
                     r"|\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2},? (19|20)\d{2}\b")
NUM_RE = re.compile(r"\d[\d,.]*\s?%?")


def pilot_prompt(q: QuestionSnapshot, today: str) -> str:
    return research_prompt(q) + STRICT_RULES.format(today=today)


# ---------------------------------------------------------------------------------------------------------------- questions
def pick_questions(runs_dir: Path, n: int, ids: list[int] | None = None) -> list[QuestionSnapshot]:
    """Newest distinct questions from live (non-shadow) records, types taken in turn so the pilot sees a mix; questions whose
    resolution text contains a link come first within a type (arm C's rule 3 is then exercised)."""
    latest: dict[int, tuple[str, dict[str, Any]]] = {}
    for path, rec in live_records(runs_dir):
        if "-shadow-" in path.parent.name:
            continue
        q = rec.get("question") or {}
        qid = q.get("question_id")
        if qid is None:
            continue
        if qid not in latest or rec.get("as_of", "") > latest[qid][0]:
            latest[qid] = (rec.get("as_of", ""), q)
    if ids:
        missing = [i for i in ids if i not in latest]
        if missing:
            raise SystemExit(f"question ids not in the archive: {missing}")
        return [snapshot_from_dict(latest[i][1]) for i in ids]
    newest = sorted(latest.values(), key=lambda x: x[0], reverse=True)[:60]
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for _, q in newest:
        by_type[q.get("question_type", "?")].append(q)
    for qs in by_type.values():
        qs.sort(key=lambda q: not URL_RE.search((q.get("resolution_criteria") or "") + (q.get("fine_print") or "")))
    picked: list[dict[str, Any]] = []
    order = sorted(by_type, key=lambda t: (t != "binary", t))
    while len(picked) < n and any(by_type[t] for t in order):
        for t in order:
            if by_type[t] and len(picked) < n:
                picked.append(by_type[t].pop(0))
    return [snapshot_from_dict(q) for q in picked]


# -------------------------------------------------------------------------------------------------- archive query counts
def _gemini_calls(obj: Any):
    if isinstance(obj, dict):
        if obj.get("provider") == "gemini" and isinstance(obj.get("extra"), dict) and "search_queries" in obj["extra"]:
            yield obj
        for v in obj.values():
            yield from _gemini_calls(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _gemini_calls(v)


def archive_query_counts(runs_dir: Path, now: datetime, days: int = 30) -> dict[str, Any]:
    """Grounded calls and queries recorded in the archive over the last `days` (live + shadow + follow-up), each call counted
    once even when a shadow record repeats the live research bundle. Plus live questions forecast in that window."""
    since = now - timedelta(days=days)
    seen: set[tuple[str, str, int]] = set()
    calls_by_model: Counter = Counter()
    queries_by_model: Counter = Counter()
    zero_query_calls: Counter = Counter()
    live_q: set[int] = set()
    for path, rec in live_records(runs_dir):
        try:
            as_of = datetime.fromisoformat(rec.get("as_of", ""))
        except ValueError:
            continue
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=timezone.utc)
        if as_of < since:
            continue
        if "-shadow-" not in path.parent.name and (rec.get("question") or {}).get("question_id") is not None:
            live_q.add(rec["question"]["question_id"])
        for c in _gemini_calls(rec):
            key = (str(c.get("requested_at")), str(c.get("model")), hash(c.get("prompt") or ""))
            if key in seen:
                continue
            seen.add(key)
            n = len(c["extra"].get("search_queries") or [])
            calls_by_model[c.get("model")] += 1
            queries_by_model[c.get("model")] += n
            if n == 0:
                zero_query_calls[c.get("model")] += 1
    return {"days": days, "calls": dict(calls_by_model), "queries": dict(queries_by_model),
            "zero_query_calls": dict(zero_query_calls), "live_questions": len(live_q),
            "total_calls": sum(calls_by_model.values()), "total_queries": sum(queries_by_model.values())}


# ------------------------------------------------------------------------------------------------------------- the call
def post_gemini(model: str, prompt: str, thinking_level: str | None) -> dict:
    body: dict[str, Any] = {"contents": [{"parts": [{"text": prompt}]}], "tools": [{"google_search": {}}]}
    if thinking_level:
        body["generationConfig"] = {"thinkingConfig": {"thinkingLevel": thinking_level}}
    resp = requests.post(GEMINI_URL.format(model=model),
                         headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"], "Content-Type": "application/json"},
                         json=body, timeout=180)
    if resp.status_code != 200:
        raise RuntimeError(f"Gemini HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def brief_stats(text: str) -> dict[str, Any]:
    """Rough, descriptive checks of the strict rules. A 'fact line' is a line or sentence holding a number that is not just a
    date or a list marker; it 'has a link' / 'has a date' when that same line carries one."""
    pieces = [p.strip() for p in re.split(r"\n+|(?<=[.!?])\s+(?=[A-Z])", text) if p.strip()]
    fact = [p for p in pieces if NUM_RE.search(DATE_RE.sub("", URL_RE.sub("", p)).lstrip("0123456789. -*#"))]
    linked = [p for p in fact if URL_RE.search(p)]
    dated = [p for p in fact if DATE_RE.search(URL_RE.sub("", p))]
    return {
        "chars": len(text),
        "links_in_text": len(URL_RE.findall(text)),
        "redirect_links_in_text": len(re.findall(r"vertexaisearch\.cloud\.google\.com", text)),
        "fact_lines": len(fact),
        "fact_lines_with_link": len(linked),
        "fact_lines_with_date": len(dated),
        "not_found": len(re.findall(r"NOT FOUND", text)),
        "has_resolution_source_section": bool(re.search(r"resolution source", text, re.I)),
    }


@dataclass
class Result:
    question_id: int
    question_type: str
    model: str
    ok: bool
    error: str = ""
    model_version: str = ""
    finish_reason: str = ""
    latency_s: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_thinking: int = 0
    search_queries: int = 0
    grounding_chunks: int = 0
    token_usd: float = 0.0
    grounding_usd_list: float = 0.0
    stats: dict[str, Any] | None = None
    text: str = ""
    queries: list[str] | None = None
    sources: list[dict[str, Any]] | None = None

    @property
    def total_usd_list(self) -> float:
        return self.token_usd + self.grounding_usd_list


def run_one(q: QuestionSnapshot, model: str, today: str, thinking_level: str | None,
            post: Callable[[str, str, str | None], dict] = post_gemini) -> Result:
    prompt = pilot_prompt(q, today)
    r = Result(question_id=q.question_id, question_type=str(q.question_type), model=model, ok=False)
    start = time.monotonic()
    try:
        data = post(model, prompt, thinking_level)
        cand = (data.get("candidates") or [{}])[0]
        r.text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []) if not p.get("thought"))
        meta = cand.get("groundingMetadata") or {}
        usage = data.get("usageMetadata") or {}
        r.finish_reason = str(cand.get("finishReason", ""))
        r.model_version = str(data.get("modelVersion", ""))
        r.tokens_in = int(usage.get("promptTokenCount") or 0)
        r.tokens_out = int(usage.get("candidatesTokenCount") or 0)
        r.tokens_thinking = int(usage.get("thoughtsTokenCount") or 0)
        r.queries = list(meta.get("webSearchQueries") or [])
        r.search_queries = len(r.queries)
        chunks = meta.get("groundingChunks") or []
        r.grounding_chunks = len(chunks)
        r.sources = [{"title": (c.get("web") or {}).get("title"), "uri": (c.get("web") or {}).get("uri")} for c in chunks]
        p_in, p_out = PRICES[model]
        r.token_usd = (r.tokens_in * p_in + (r.tokens_out + r.tokens_thinking) * p_out) / 1e6
        r.grounding_usd_list = r.search_queries * GROUNDING_USD_PER_QUERY
        r.stats = brief_stats(r.text)
        r.ok = bool(r.text.strip())
        if not r.ok:
            r.error = f"empty response (finishReason={r.finish_reason})"
    except Exception as e:  # noqa: BLE001 - a failed call is a result of the pilot, reported per model
        r.error = f"{type(e).__name__}: {e}"[:300]
    r.latency_s = round(time.monotonic() - start, 1)
    return r


# ----------------------------------------------------------------------------------------------------------- reporting
def summarise(results: list[Result], archive: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for model in dict.fromkeys(r.model for r in results):
        rs = [r for r in results if r.model == model]
        ok = [r for r in rs if r.ok]
        n = len(ok) or 1
        mean = lambda f: sum(f(r) for r in ok) / n  # noqa: E731
        fact = sum(r.stats["fact_lines"] for r in ok) or 1
        q_per_call = mean(lambda r: r.search_queries)
        month_q = archive.get("live_questions", 0) * 30 / max(archive.get("days", 30), 1)
        rows.append({
            "model": model, "calls": len(rs), "ok": len(ok),
            "queries_per_call_mean": round(q_per_call, 2),
            "queries_per_call_max": max((r.search_queries for r in ok), default=0),
            "calls_with_0_queries": sum(1 for r in ok if r.search_queries == 0),
            "tokens_in_mean": round(mean(lambda r: r.tokens_in)),
            "tokens_out_mean": round(mean(lambda r: r.tokens_out)),
            "tokens_thinking_mean": round(mean(lambda r: r.tokens_thinking)),
            "token_usd_mean": round(mean(lambda r: r.token_usd), 4),
            "token_usd_max": round(max((r.token_usd for r in ok), default=0), 4),
            "total_usd_list_mean": round(mean(lambda r: r.total_usd_list), 4),
            "calls_over_cap_tokens_only": sum(1 for r in ok if r.token_usd > CAP_PER_Q),
            "calls_over_cap_incl_grounding": sum(1 for r in ok if r.total_usd_list > CAP_PER_Q),
            "latency_s_mean": round(mean(lambda r: r.latency_s), 1),
            "latency_s_max": max((r.latency_s for r in ok), default=0),
            "fact_lines_with_link_share": round(sum(r.stats["fact_lines_with_link"] for r in ok) / fact, 2),
            "fact_lines_with_date_share": round(sum(r.stats["fact_lines_with_date"] for r in ok) / fact, 2),
            "redirect_links_share": round(sum(r.stats["redirect_links_in_text"] for r in ok)
                                          / max(sum(r.stats["links_in_text"] for r in ok), 1), 2),
            "not_found_total": sum(r.stats["not_found"] for r in ok),
            "resolution_source_section": sum(1 for r in ok if r.stats["has_resolution_source_section"]),
            "extra_queries_per_month_est": round(q_per_call * month_q),
        })
    return rows


def report_lines(rows: list[dict[str, Any]], archive: dict[str, Any], questions: list[QuestionSnapshot],
                 spent_tokens: float, spent_list: float) -> list[str]:
    days = archive["days"]
    scale = 30 / days
    live_writer_q = archive["queries"]
    out = [f"# EXP-013 arm C pilot ({PROMPT_VERSION})", "",
           f"Questions (n = {len(questions)}): " + ", ".join(f"{q.question_id} ({q.question_type})" for q in questions), "",
           f"Archive, last {days} days: {archive['live_questions']} live questions; grounded calls by model "
           f"{archive['calls']}; search queries by model {live_writer_q}; total {archive['total_queries']} queries "
           f"(~{round(archive['total_queries'] * scale)} per 30 days) vs {FREE_PER_MONTH} free.", "",
           "Arm C REPLACES the live Flash-Lite call only in the shadow arm; the live call keeps running, so arm C's queries "
           "come ON TOP of today's total.", "",
           "| " + " | ".join(rows[0].keys()) + " |" if rows else "(no results)",
           "|" + "---|" * (len(rows[0]) if rows else 0)]
    out += ["| " + " | ".join(str(v) for v in r.values()) + " |" for r in rows]
    for r in rows:
        total = round(archive["total_queries"] * scale) + r["extra_queries_per_month_est"]
        out.append(f"- {r['model']}: today ~{round(archive['total_queries'] * scale)} + arm C ~{r['extra_queries_per_month_est']} "
                   f"= ~{total} queries per 30 days ({'within' if total <= FREE_PER_MONTH else 'OVER'} {FREE_PER_MONTH}, "
                   f"if the free tier counts queries and covers this model; check the billing console).")
    out += ["", f"Spent: ${spent_tokens:.3f} tokens at list price; ${spent_list:.3f} if every query were billed.",
            "Certainty: n = 5 questions per model; queries and cost per call vary by question, so these are rough means."]
    return out


def list_models(key: str) -> list[str]:
    resp = requests.get(MODELS_URL, headers={"x-goog-api-key": key}, timeout=30)
    resp.raise_for_status()
    return sorted(m["name"].removeprefix("models/") for m in resp.json().get("models", []))


# ---------------------------------------------------------------------------------------------------------------- main
def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--models", default="gemini-3.5-flash,gemini-3.1-pro-preview")
    p.add_argument("--n", type=int, default=5)
    p.add_argument("--question-ids", default="", help="comma-separated; default: newest live questions, mixed types")
    p.add_argument("--thinking-level", default="low", help="Gemini 3 thinkingLevel; '' = model default")
    p.add_argument("--max-cost", type=float, default=2.0, help="hard cap, USD, tokens + every query at list price")
    p.add_argument("--dry-run", action="store_true", help="no paid calls")
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    unknown = [m for m in models if m not in PRICES]
    if unknown:
        print(f"no list price in PRICES for {unknown}; add it (from the Gemini pricing page) first")
        return 2
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    ids = [int(x) for x in args.question_ids.split(",") if x.strip()] or None
    questions = pick_questions(RUNS, args.n, ids)
    archive = archive_query_counts(RUNS, now)
    key = os.getenv("GEMINI_API_KEY")
    if key:
        available = list_models(key)
        print("Gemini models visible to this key (pro/flash):",
              ", ".join(m for m in available if ("pro" in m or "flash" in m) and "tts" not in m and "image" not in m))
        missing = [m for m in models if m not in available]
        if missing:
            print(f"NOT available to this key: {missing}")
            return 2
    else:
        print("GEMINI_API_KEY not set: model ids not checked")
    print(f"Questions: {[(q.question_id, str(q.question_type)) for q in questions]}")
    print(f"Archive last {archive['days']} days: live questions {archive['live_questions']}, grounded calls {archive['calls']}, "
          f"queries {archive['queries']}, calls with 0 queries {archive['zero_query_calls']}")
    est = len(questions) * len(models) * PRIOR_CALL_USD
    print(f"Plan: {len(questions)} questions x {len(models)} models = {len(questions) * len(models)} calls, thinking "
          f"'{args.thinking_level or 'default'}'; worst-case estimate ${est:.2f}, hard cap ${args.max_cost:.2f}")
    if args.dry_run:
        return 0
    if not key:
        return 2
    run = now.strftime("%Y%m%dT%H%M%SZ")
    out = OUT / f"armC-pilot-{run}"
    out.mkdir(parents=True, exist_ok=True)
    results: list[Result] = []
    spent_list = spent_tokens = 0.0
    capped = False
    with open(out / "calls.jsonl", "w", encoding="utf-8") as fh:
        for q in questions:
            for model in models:
                if capped or spent_list + PRIOR_CALL_USD > args.max_cost:
                    if not capped:
                        print(f"cap: stopped before {model} on {q.question_id} (spent ${spent_list:.3f} at list price)")
                    capped = True
                    continue
                r = run_one(q, model, today, args.thinking_level or None)
                results.append(r)
                spent_tokens += r.token_usd
                spent_list += r.total_usd_list
                fh.write(json.dumps({**r.__dict__, "prompt_version": PROMPT_VERSION, "thinking_level": args.thinking_level,
                                     "run": run}, ensure_ascii=False) + "\n")
                print(f"{q.question_id} {model}: ok={r.ok} queries={r.search_queries} tokens={r.tokens_in}/{r.tokens_out}"
                      f"/{r.tokens_thinking} ${r.token_usd:.4f} (+${r.grounding_usd_list:.3f} grounding at list) "
                      f"{r.latency_s}s {r.error[:120]}")
    rows = summarise(results, archive)
    if rows:
        with open(out / "summary.csv", "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    lines = report_lines(rows, archive, questions, spent_tokens, spent_list)
    (out / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nWritten: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
