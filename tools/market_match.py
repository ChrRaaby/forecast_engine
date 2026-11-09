"""EXP-012 step 1: match every live binary forecast record to prediction markets that existed at its as_of.

    PYTHONUTF8=1 poetry run python tools/market_match.py --dry-run          # counts only, no API
    PYTHONUTF8=1 poetry run python tools/market_match.py --max-cost 5       # match, write data/markets/exp012/matches.jsonl
    PYTHONUTF8=1 poetry run python tools/market_match.py --report-only      # rebuild the step 1b report and the 20-match audit

Per record: candidates (markets created before and open at as_of, evals/markets.py) -> ONE judge call (Gemini Flash-Lite via
OpenRouter, temperature 0) -> grade A/B/none + polarity for each candidate -> price at as_of for the A/B ones only.
The judge sees, per candidate, only title, rules text and end date: no price, volume, status or resolution notes (LK3). Prices
are fetched after the judge has answered. This script never reads the question results file (LK4); the table is only written,
not frozen, here. Spend is booked in docs/experiments/budget-ledger.md under EXP-012 (cap $5: the run stops above it).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals import markets as mk  # noqa: E402

RUNS = ROOT / "data" / "runs"
OUT_DIR = ROOT / "data" / "markets" / "exp012"
LEDGER = ROOT / "docs" / "experiments" / "budget-ledger.md"
JUDGE_MODEL = "google/gemini-3.5-flash-lite"
JUDGE_PROMPT_VERSION = "exp012-judge-v1"
FIRST_RECORD = datetime(2026, 10, 1, tzinfo=timezone.utc)
MAX_COST_USD = 5.0
CONCURRENCY = 6
RULES_CHARS = 1500
GRADES = ("A", "B", "none")

JUDGE_PROMPT = """You compare one forecasting question with candidate prediction-market contracts and decide, for each contract, whether it asks the same question.

FORECASTING QUESTION
Title: {title}
Resolution criteria: {criteria}
Fine print: {fine_print}
Scheduled resolution date: {close}

CANDIDATE CONTRACTS
{candidates}

Grades:
A = same event, same threshold or number, deadline within 3 days of the question's, and a compatible resolution source.
B = same event and threshold, deadline within 31 days of the question's, or a slightly different resolution source.
none = anything else (different event, different threshold, different period, or you cannot tell).

Polarity: "same" if the contract's YES means the question resolves YES; "inverted" if the contract's YES means the question resolves NO.

Judge only from the texts above. Answer with one JSON object and nothing else:
{{"matches": [{{"n": 1, "grade": "A" | "B" | "none", "polarity": "same" | "inverted", "why": "one short sentence"}}, ...]}}
Include one entry for every contract, numbered as above."""


# ---------- records ----------

def tournament_of(q: dict[str, Any]) -> str:
    slugs = [str(s).lower() for s in (q.get("tournaments") or [])]
    if any("minibench" in s for s in slugs):
        return "MiniBench"
    if any("futureeval" in s for s in slugs):
        return "FutureEval"
    return "other"


def eligible_records(runs_dir: Path = RUNS) -> list[tuple[Path, dict[str, Any]]]:
    """Live, published binary records from 2026-10-01 on (no test-questions runs, no shadow arms)."""
    out = []
    for path in sorted(runs_dir.rglob("q*.json")):
        name = path.parent.name
        if not (name.endswith("-tournament") or name.endswith("-wide")):
            continue
        rec = json.loads(path.read_text(encoding="utf-8"))
        q = rec.get("question", {})
        if q.get("question_type") != "binary" or not rec.get("published"):
            continue
        if not isinstance(rec.get("aggregate"), (int, float)):
            continue
        if mk.parse_time(rec["as_of"]) < FIRST_RECORD:
            continue
        out.append((path, rec))
    return out


def record_id(path: Path, runs_dir: Path = RUNS) -> str:
    try:
        return path.relative_to(runs_dir).as_posix()
    except ValueError:
        return path.as_posix()


# ---------- judge ----------

def render_candidate(n: int, m: mk.Market) -> str:
    end = m.end.date().isoformat() if m.end else "not stated"
    return f"[{n}]\nTitle: {m.title}\nRules: {(m.rules or 'not stated')[:RULES_CHARS]}\nEnd date: {end}"


def judge_prompt(q: dict[str, Any], cands: Sequence[mk.Market]) -> str:
    """The only thing the judge sees. Built from the question and the markets' title, rules and end date; a Market has no other
    fields, so no price, volume, status or result can enter (LK3)."""
    close = (q.get("scheduled_resolution_time") or "")[:10] or "not stated"
    return JUDGE_PROMPT.format(
        title=q.get("question_text", ""), criteria=(q.get("resolution_criteria") or "")[:2500] or "not stated",
        fine_print=(q.get("fine_print") or "")[:1500] or "none", close=close,
        candidates="\n\n".join(render_candidate(i + 1, m) for i, m in enumerate(cands)))


def parse_judge(text: str, n: int) -> list[dict[str, str]] | None:
    """One {grade, polarity, why} per candidate, or None if the answer is unusable."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        items = json.loads(m.group(0)).get("matches")
    except (ValueError, AttributeError):
        return None
    if not isinstance(items, list):
        return None
    out: dict[int, dict[str, str]] = {}
    for it in items:
        try:
            k = int(it["n"])
        except (KeyError, TypeError, ValueError):
            continue
        grade = str(it.get("grade", "")).strip()
        grade = "none" if grade.lower() == "none" else grade.upper()
        pol = str(it.get("polarity", "same")).strip().lower()
        if 1 <= k <= n and grade in GRADES and pol in ("same", "inverted"):
            out[k] = {"grade": grade, "polarity": pol, "why": str(it.get("why", ""))[:200]}
    if len(out) != n:
        return None
    return [out[i + 1] for i in range(n)]


class Judge:
    def __init__(self, model: str = JUDGE_MODEL, cache_path: Path | None = None) -> None:
        from dotenv import load_dotenv
        from openai import AsyncOpenAI

        load_dotenv(ROOT / ".env")
        key = os.getenv("OPENROUTER_API_KEY")
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY is not set")
        self.model = model
        self.client = AsyncOpenAI(base_url="https://openrouter.ai/api/v1", api_key=key, timeout=120, max_retries=2)
        self.cache_path = cache_path or (OUT_DIR / "judge_cache.jsonl")
        self.cache: dict[str, dict[str, Any]] = {}
        if self.cache_path.exists():
            for ln in self.cache_path.read_text(encoding="utf-8").splitlines():
                if ln.strip():
                    d = json.loads(ln)
                    self.cache[d["key"]] = d
        self.cost = 0.0
        self.calls = 0

    def _key(self, prompt: str) -> str:
        return hashlib.sha256(f"{self.model}|{JUDGE_PROMPT_VERSION}|{prompt}".encode()).hexdigest()

    async def grade(self, prompt: str, n: int) -> tuple[list[dict[str, str]] | None, str | None]:
        key = self._key(prompt)
        hit = self.cache.get(key)
        if hit is None:
            try:
                r = await self.client.chat.completions.create(
                    model=self.model, messages=[{"role": "user", "content": prompt}], max_tokens=3000, temperature=0,
                    extra_body={"usage": {"include": True}, "reasoning": {"effort": "low"}})
                text = (r.choices[0].message.content or "") if r.choices else ""
                cost = float(((r.usage.model_extra or {}).get("cost") if r.usage else None) or 0.0)
                hit = {"key": key, "output": text, "cost": cost, "at": mk.now().isoformat()}
            except Exception as e:  # recorded, not raised: one failed call must not stop the run
                return None, f"{type(e).__name__}: {e}"
            self.calls += 1
            self.cost += hit["cost"]
            self.cache[key] = hit
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            with self.cache_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(hit, ensure_ascii=False) + "\n")
        parsed = parse_judge(hit["output"], n)
        return parsed, (None if parsed else "unparseable judge answer")


# ---------- one record ----------

def cand_row(m: mk.Market, verdict: dict[str, str], price: mk.PricePoint | None) -> dict[str, Any]:
    now = mk.now()
    row = {
        "venue": m.venue, "market_id": m.market_id, "real_money": m.real_money, "title": m.title, "rules": (m.rules or "")[:RULES_CHARS],
        "end": mk.iso(m.end), "created": mk.iso(m.created), "closed_now": bool(m.closed_at and m.closed_at <= now), "url": m.url,
        "grade": verdict["grade"], "polarity": verdict["polarity"], "why": verdict["why"],
        "price_raw": None, "price": None, "price_t": None, "price_age_h": None, "fidelity_min": None, "n_points": None, "gap_min": None,
    }
    if price is not None:
        row.update(price_raw=round(price.p, 4), price=round(mk.flip_and_clamp(price.p, verdict["polarity"]), 4),
                   price_t=mk.iso(price.t), price_age_h=round(price.age.total_seconds() / 3600, 2), fidelity_min=price.fidelity_min,
                   n_points=price.n_points, gap_min=price.gap_min)
    return row


def summarise_row(row: dict[str, Any]) -> None:
    """Fill the record-level fields from its candidates: the grade-A real-money markets with a price, and their blended price."""
    a_real = [c for c in row["candidates"] if c["grade"] == "A" and c["real_money"]]
    a_priced = [c for c in a_real if c["price"] is not None]
    row["has_A_real"] = bool(a_real)
    row["has_A_real_priced"] = bool(a_priced)
    row["market_p"] = round(mk.mean_logit_price([c["price"] for c in a_priced]), 4) if a_priced else None
    row["market_venues"] = sorted({c["venue"] for c in a_priced}, key=mk.VENUE_ORDER.index)


async def match_record(path: Path, rec: dict[str, Any], http: mk.Http, kalshi_series: Sequence[dict[str, Any]], judge: Judge,
                       sem: asyncio.Semaphore) -> dict[str, Any]:
    q = rec["question"]
    as_of = mk.parse_time(rec["as_of"])
    row: dict[str, Any] = {
        "record": record_id(path), "question_id": q["question_id"], "post_id": q.get("post_id"), "as_of": mk.iso(as_of),
        "tournament": tournament_of(q), "config_version": rec.get("config_version"), "bot_p": float(rec["aggregate"]),
        "question_text": q.get("question_text"), "candidates": [], "judge_error": None, "n_candidates": 0,
        "judge_prompt_version": JUDGE_PROMPT_VERSION, "judge_model": judge.model,
    }
    loop = asyncio.get_running_loop()
    async with sem:
        cands = await loop.run_in_executor(None, lambda: mk.candidates(q["question_text"], as_of, http, kalshi_series))
        row["n_candidates"] = len(cands)
        if cands:
            verdicts, err = await judge.grade(judge_prompt(q, cands), len(cands))
            row["judge_error"] = err
            if verdicts:
                for m, v in zip(cands, verdicts):
                    price = None
                    if v["grade"] in ("A", "B"):
                        price = await loop.run_in_executor(None, lambda m=m: mk.price_as_of(m, as_of, http))
                    row["candidates"].append(cand_row(m, v, price))
    summarise_row(row)
    return row


# ---------- ledger ----------

def append_ledger(run: str, calls: int, spent: float, note: str, ledger: Path = LEDGER, experiment: str = "EXP-012") -> None:
    """Add one row to the first table of the budget ledger (same layout as the other experiment tools)."""
    if not ledger.exists():
        return
    lines = ledger.read_text(encoding="utf-8").splitlines()
    end = next((i for i, ln in enumerate(lines) if i > 0 and ln.startswith("## ")), len(lines))
    main = [i for i in range(end) if lines[i].startswith("| 20")]
    cum = sum(float(lines[i].split("|")[6].strip().lstrip("$")) for i in main) + spent
    row = f"| {run[:4]}-{run[4:6]}-{run[6:8]} | {experiment} | {run} | {note} | {calls} | ${spent:.3f} | ${cum:.3f} | ${180 - cum:.2f} |"
    lines.insert((main[-1] + 1) if main else end, row)
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


# ---------- report ----------

def _pct(k: int, n: int) -> str:
    return f"{k}/{n} = {100 * k / n:.1f}%" if n else "0/0"


def _med(xs: Sequence[float]) -> str:
    return f"{statistics.median(xs):.2f}" if xs else "n/a"


def build_report(rows: Sequence[dict[str, Any]], meta: dict[str, Any]) -> str:
    n = len(rows)
    uq = {r["question_id"] for r in rows}
    L: list[str] = ["# EXP-012 step 1b report (draft table, NOT frozen, no results read)", ""]
    L.append(f"Generated {mk.now():%Y-%m-%d %H:%M} UTC. Judge `{meta['model']}` (prompt `{JUDGE_PROMPT_VERSION}`), temperature 0.")
    L.append(f"Records scanned: **{n}** ({len(uq)} unique questions; {n - len(uq)} records repeat a question). "
             "Scope: published live binary records from 2026-10-01 (`-tournament` and `-wide` runs, any config era).")
    L.append("")

    def best(r: dict[str, Any]) -> str:
        gs = {c["grade"] for c in r["candidates"]}
        return "A" if "A" in gs else "B" if "B" in gs else "none"

    L += ["## Grades per tournament (best grade over the record's candidates, any venue)", "",
          "| Tournament | records | no candidates | A | B | none | A real-money | A real-money with price |", "|---|---|---|---|---|---|---|---|"]
    for t in ["FutureEval", "MiniBench", "other", None]:
        sub = [r for r in rows if t is None or r["tournament"] == t]
        if not sub:
            continue
        g = {k: sum(1 for r in sub if r["candidates"] and best(r) == k) for k in ("A", "B", "none")}
        L.append(f"| {t or 'all'} | {len(sub)} | {sum(1 for r in sub if not r['candidates'])} | {g['A']} | {g['B']} | {g['none']} | "
                 f"{sum(r['has_A_real'] for r in sub)} | {sum(r['has_A_real_priced'] for r in sub)} |")
    L.append("")

    L += ["## Grades per venue (candidate level, n = judged candidates)", "", "| Venue | candidates judged | A | B | none | A with price | B with price |", "|---|---|---|---|---|---|---|"]
    for v in mk.VENUE_ORDER:
        cs = [c for r in rows for c in r["candidates"] if c["venue"] == v]
        L.append(f"| {v} | {len(cs)} | {sum(c['grade'] == 'A' for c in cs)} | {sum(c['grade'] == 'B' for c in cs)} | "
                 f"{sum(c['grade'] == 'none' for c in cs)} | {sum(c['grade'] == 'A' and c['price'] is not None for c in cs)} | "
                 f"{sum(c['grade'] == 'B' and c['price'] is not None for c in cs)} |")
    L.append("")

    a_real = sum(r["has_A_real"] for r in rows)
    a_priced = [r for r in rows if r["has_A_real_priced"]]
    gaps = [abs(r["bot_p"] - r["market_p"]) * 100 for r in a_priced]
    L += ["## Futility check (card decision rule 1)", "",
          f"- Real-money grade-A match rate on binary records: **{_pct(a_real, n)}** (any price status); "
          f"**{_pct(len(a_priced), n)}** with an as-of price (alive within 7 days).",
          f"- Same on unique questions: {_pct(len({r['question_id'] for r in rows if r['has_A_real']}), len(uq))} / "
          f"{_pct(len({r['question_id'] for r in a_priced}), len(uq))}.",
          f"- Median |bot - market| gap on grade-A real-money matches with a price: **{_med(gaps)} percentage points** (n = {len(gaps)}); "
          f"mean {statistics.fmean(gaps):.2f}." if gaps else "- Median gap: n/a (no priced grade-A real-money match).", ]
    rate = len(a_priced) / n if n else 0.0
    rate_any = a_real / n if n else 0.0
    med = statistics.median(gaps) if gaps else 0.0
    trig_rate = rate < 0.08
    trig_gap = bool(gaps) and med < 5.0
    L.append(f"- Rate < 8%? any price status: {'YES' if rate_any < 0.08 else 'no'}; with price: {'YES' if trig_rate else 'no'}. "
             f"Median gap < 5 pts? {'YES' if trig_gap else 'no'}.")
    L.append(f"- **Futility rule triggers: {'YES' if (trig_rate or trig_gap or not gaps) else 'NO'}.** The card does not say whether a "
             "grade-A match with no live price counts, so both rates are shown; the with-price rate is the stricter one.")
    L.append("")

    L += ["## LK5 (no price or market from after as_of)", ""]
    bad = mk.audit_rows(rows)
    L.append(f"- Checked every candidate of {n} records: **{len(bad)} violations**." + ("" if not bad else " STOP: " + "; ".join(bad[:5])))
    L.append("")

    L += ["## LK6 (price-history granularity per venue, among grade A/B candidates)", "",
          "| Venue | A/B candidates | with price | dead (no point within 7 d) | median price age h | p90 age h | median gap between last points (min) | closed markets with price | open markets with price |",
          "|---|---|---|---|---|---|---|---|---|"]
    for v in mk.VENUE_ORDER:
        cs = [c for r in rows for c in r["candidates"] if c["venue"] == v and c["grade"] in ("A", "B")]
        pr = [c for c in cs if c["price"] is not None]
        ages = sorted(c["price_age_h"] for c in pr)
        p90 = f"{ages[int(0.9 * (len(ages) - 1))]:.2f}" if ages else "n/a"
        gaps_m = [c["gap_min"] for c in pr if c.get("gap_min") is not None]
        L.append(f"| {v} | {len(cs)} | {len(pr)} | {len(cs) - len(pr)} | {_med(ages)} | {p90} | {_med(gaps_m)} | "
                 f"{sum(c['closed_now'] for c in pr)} | {sum(not c['closed_now'] for c in pr)} |")
    fid = {}
    for r in rows:
        for c in r["candidates"]:
            if c["venue"] == "polymarket" and c["price"] is not None:
                fid[c["fidelity_min"]] = fid.get(c["fidelity_min"], 0) + 1
    L.append("")
    L.append("- Polymarket history bucket size actually used (minutes -> priced candidates): " + (", ".join(f"{k}: {v}" for k, v in sorted(fid.items())) or "none") + ".")
    L.append("- Coarse points respect LK1 (never after as_of) but raise the price age; ages are in the table above.")
    L.append("")

    L += ["## Other facts for the freeze decision", "",
          f"- Records with no candidate at all: {_pct(sum(1 for r in rows if not r['candidates'] and not r['judge_error']), n)}.",
          f"- Judge failures (call error or unparseable answer, counted as no match): {sum(1 for r in rows if r['judge_error'])}.",
          f"- Config eras among records: " + ", ".join(f"{k}: {v}" for k, v in sorted(_count(r['config_version'] for r in rows).items())) + ".",
          f"- Judge cost this run: ${meta['cost']:.4f} over {meta['calls']} live calls ({meta['cached']} answered from the judge cache); "
          f"market API requests: {meta['live_requests']} live, {meta['cache_hits']} from the raw cache, {meta.get('http_failures', 0)} gave up after retries (treated as no data).",
          f"- Candidates whose rules text contains resolution-note wording (\"resolved to\", \"has been resolved\", \"final result\"): "
          f"{sum(1 for r in rows for c in r['candidates'] if re.search(r'resolved to|has been resolved|has resolved|final result', c['rules'], re.I))} "
          "of the judged candidates. Rules texts are shown to the judge as stored on the venue today.",
          "- Polymarket end dates are the venue's `endDate` as stored today; Kalshi uses `latest_expiration_time`; Manifold's end date is "
          "hidden when it equals the resolution time. Residual risk: an edited end date can leak timing.", ""]
    return "\n".join(L)


def _count(it) -> dict[Any, int]:
    d: dict[Any, int] = {}
    for x in it:
        d[x] = d.get(x, 0) + 1
    return d


def build_audit(rows: Sequence[dict[str, Any]], k: int = 20, seed: int = 12) -> str:
    """20 random grade-A real-money matches: question vs market text as the judge saw it. No prices, no results."""
    pool = [(r, c) for r in rows for c in r["candidates"] if c["grade"] == "A" and c["real_money"]]
    pick = random.Random(seed).sample(pool, min(k, len(pool)))
    L = [f"# EXP-012 blind audit: {len(pick)} random grade-A real-money matches (of {len(pool)}), seed {seed}",
         "", "No prices, no results. For each: is the market the same question? Is the polarity right? Mark right / wrong by hand.", ""]
    for i, (r, c) in enumerate(pick, 1):
        L += [f"## {i}. {r['record']} ({r['tournament']})", "", f"**Question:** {r['question_text']}", "",
              f"**Market ({c['venue']}):** {c['title']}", "", f"**Market end date:** {c['end']}  |  judge: grade {c['grade']}, polarity {c['polarity']}",
              "", f"**Judge's reason:** {c['why']}", "", "**Market rules (excerpt):**", "", "> " + (c["rules"][:700].replace("\n", "\n> ") or "not stated"),
              "", "**Verdict:** ", ""]
    return "\n".join(L)


# ---------- main ----------

def load_rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] if path.exists() else []


async def run(args: argparse.Namespace) -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    matches = OUT_DIR / "matches.jsonl"
    recs = eligible_records()
    if args.limit:
        recs = recs[: args.limit]
    print(f"{len(recs)} eligible binary records ({len({r['question']['question_id'] for _, r in recs})} unique questions)")
    if args.dry_run:
        return 0
    http = mk.Http()
    kalshi_series = mk.kalshi_series_index(http, refresh=args.refresh_index)
    print(f"Kalshi series index: {len(kalshi_series)} series")
    judge = Judge()
    cached_before = len(judge.cache)
    sem = asyncio.Semaphore(CONCURRENCY)
    rows: list[dict[str, Any]] = []
    spent_before = judge.cost
    aborted = False

    async def one(path, rec):
        nonlocal aborted
        if judge.cost > args.max_cost:
            aborted = True
            return None
        return await match_record(path, rec, http, kalshi_series, judge, sem)

    results = []
    for i in range(0, len(recs), 24):
        batch = await asyncio.gather(*(one(p, r) for p, r in recs[i:i + 24]))
        results += [b for b in batch if b]
        print(f"  {len(results)}/{len(recs)} records, judge ${judge.cost:.4f}", flush=True)
        if aborted:
            break
    rows = results
    if aborted:
        print(f"ABORTED: judge cost ${judge.cost:.4f} above the cap ${args.max_cost:.2f}; table is incomplete")
    matches.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    meta = {"model": judge.model, "cost": judge.cost, "calls": judge.calls, "cached": cached_before, "live_requests": http.live_requests,
            "cache_hits": http.cache_hits,
            "http_failures": len(http.failures)}
    (OUT_DIR / "run_meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    if judge.cost > 0:
        stamp = mk.now().strftime("%Y%m%dT%H%M%SZ")
        append_ledger(stamp, judge.calls, judge.cost, f"market matcher, judge {judge.model}, {len(rows)} binary records (step 1b, draft table)")
    return 3 if aborted else 0


def report_only() -> int:
    rows = load_rows(OUT_DIR / "matches.jsonl")
    if not rows:
        print("no matches.jsonl")
        return 1
    meta_path = OUT_DIR / "run_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {"model": JUDGE_MODEL, "cost": 0.0, "calls": 0, "cached": 0, "live_requests": 0, "cache_hits": 0}
    (OUT_DIR / "step1b-report.md").write_text(build_report(rows, meta), encoding="utf-8")
    (OUT_DIR / "audit-20.md").write_text(build_audit(rows), encoding="utf-8")
    print(f"wrote step1b-report.md and audit-20.md for {len(rows)} records")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=0, help="only the first N records (smoke test)")
    ap.add_argument("--max-cost", type=float, default=MAX_COST_USD)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--refresh-index", action="store_true", help="re-download the Kalshi series list")
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()
    if args.report_only:
        return report_only()
    code = asyncio.run(run(args))
    if code in (0, 3):
        report_only()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
