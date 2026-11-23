"""Researcher pilot (B-102): can a self-directed researcher find the figure a question resolves on? Four readers, 15 questions.

No forecasts are made and no scores are read. Nothing in main.py or the live path is used for writing.

    # free: draw 5 questions per group (JavaScript-only / index page / no usable link), save and print the list
    poetry run python tools/researcher_pilot.py --pick --data data
    # free: open the picked questions' links in the browser and print what came back (no model)
    poetry run python tools/researcher_pilot.py --check-browser --picks <picks.json>
    # PAID, needs Christian's "go" naming up to $3.25
    poetry run python tools/researcher_pilot.py --run --go --picks <picks.json>
    # free, after the hand check: fill checked_value, verdict (found / wrong / not found), rows_checked and rows_correct in sheet.csv
    poetry run python tools/researcher_pilot.py --report --sheet <run>/sheet.csv

Questions: "open" means the outcome is not due yet (scheduled resolution at least a day away). Tournament questions take forecasts for
only a few hours, so on most days none is open in that sense; a pending one still has a current figure that can be checked by hand.

Needs, outside poetry.lock (ADR-0014): `pip install playwright pypdf` in this checkout's .venv, and Google Chrome on the PC.
Outputs: data/experiments/B-102/ (private data repo): picks-<date>.json, run-<time>/{sheet.csv, summary.md, results.jsonl}. The public
repo gets counts only. Key: OPENROUTER_API_KEY (experiments) and GEMINI_API_KEY; the live key is never read.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals import source_links  # noqa: E402
from evals.replay import live_records, snapshot_from_dict  # noqa: E402
from evals.researcher import CAP_USD, R0, R1, R2, R3, R3_MODEL, Reader, ReadResult  # noqa: E402
from forecast_engine.clock import SystemClock  # noqa: E402
from forecast_engine.schema import QuestionSnapshot  # noqa: E402

TOURNAMENTS = {"fall-futureeval-2026", "minibench"}
SEED, PER_GROUP = 1100, 5
MAX_RUN_USD = 3.25
RESERVE_USD = {"R0": 0.02, "R1": 0.02, "R2": CAP_USD + 0.08, "R3": 0.25}  # worst case kept free before a reader starts
READERS = ("R0", "R1", "R2", "R3")
OUT_DIR = Path("experiments") / "B-102"
VERDICTS = ("found", "wrong", "not found")
HAND = ("checked_value", "verdict", "rows_checked", "rows_correct")
SHEET = ("question_id", "group", "type", "question", "reader", "status", "value", "as_of_date", "source_url", "quote", "quote_check",
         "problems", "history_rows", "rows_on_pages", "developments", "cost_usd", "booked_usd", "seconds", "searches", "page_opens",
         "note") + HAND


# --------------------------------------------------------------------------------------------------------------- questions
_DATES = re.compile(r"\b(19|20)\d{2}\b|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:\s*[-–]\s*\d{1,2})?\b"
                    r"|\b\d{1,2}(?:\s*[-–]\s*\d{1,2})?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", re.I)
_THRESHOLD = re.compile(r"\b(at least|more than|less than|fewer than|above|below|over|under|exceed\w*|reach\w*|surpass\w*|between|"
                        r"or more|or higher|or lower|or fewer|close at|settle)\b", re.I)


def is_number_question(q: QuestionSnapshot) -> bool:
    """Numeric / discrete, multiple choice between number ranges, or a yes/no question about a figure passing a threshold. Dates in
    the text are not figures. A pattern, not a judgement: the drawn list is read by a person before anything is paid for."""
    has_number = lambda s: bool(re.search(r"\d", _DATES.sub(" ", s)))  # noqa: E731
    if q.question_type in ("numeric", "discrete"):
        return True
    if q.question_type == "multiple_choice":
        return bool(q.options) and sum(has_number(o) for o in q.options) * 2 >= len(q.options)
    return bool(_THRESHOLD.search(q.question_text)) and has_number(q.question_text)


def pending_questions(runs: Path, now: datetime) -> list[QuestionSnapshot]:
    """Distinct tournament questions from live records whose scheduled resolution is at least a day away (newest snapshot each).
    Only the question is read from the record, never its forecast or any outcome."""
    latest: dict[int, tuple[str, dict[str, Any]]] = {}
    for path, rec in live_records(runs):
        q = rec.get("question") or {}
        if "-shadow-" in path.parent.name or q.get("question_id") is None:
            continue
        if q["question_id"] not in latest or rec.get("as_of", "") > latest[q["question_id"]][0]:
            latest[q["question_id"]] = (rec.get("as_of", ""), q)
    out = []
    for _, q in latest.values():
        s = snapshot_from_dict(q)
        due = s.scheduled_resolution_time
        if due is None or not set(s.tournaments) & TOURNAMENTS:
            continue
        due = due if due.tzinfo else due.replace(tzinfo=timezone.utc)
        if due > now + timedelta(days=1):
            out.append(s)
    return sorted(out, key=lambda s: s.question_id)


def draw(qs: Iterable[QuestionSnapshot], kinds: dict[int, str], seed: int = SEED,
         per_group: int = PER_GROUP, exclude: Iterable[int] = ()) -> tuple[dict[str, list[int]], dict[str, int]]:
    """(group -> drawn ids, group -> number of candidates). Each group is put in a seeded order and the first `per_group` not in
    `exclude` are taken, so an excluded question is replaced by the next one and the others stay. A short group stays short: it is
    never filled from another."""
    pools: dict[str, list[int]] = {g: [] for g in source_links.GROUPS}
    for q in sorted(qs, key=lambda q: q.question_id):
        g = source_links.group_of(kinds[q.question_id])
        if g and is_number_question(q):
            pools[g].append(q.question_id)
    drawn = {}
    for g, ids in pools.items():
        order = list(ids)
        random.Random(f"{seed}:{g}").shuffle(order)
        drawn[g] = sorted([i for i in order if i not in set(exclude)][:per_group])
    return drawn, {g: len(ids) for g, ids in pools.items()}


def pick(data: Path, now: datetime, get=None, exclude: dict[int, str] | None = None) -> dict[str, Any]:
    qs = pending_questions(data / "runs", now)
    number = [q for q in qs if is_number_question(q)]
    kinds, links = source_links.classify_questions(number, get=get)
    drawn, pool = draw(number, kinds, exclude=exclude or {})
    by_id = {q.question_id: q for q in number}
    rows = []
    for g in source_links.GROUPS:
        for qid in drawn[g]:
            q = by_id[qid]
            urls = [u for u in links if u in f"{q.resolution_criteria}\n{q.fine_print}"]
            rows.append({"question_id": qid, "group": g, "kind": kinds[qid], "type": q.question_type, "question": q.question_text,
                         "page_url": q.page_url, "links": urls, "resolves": str(q.scheduled_resolution_time)[:10],
                         "snapshot": q.to_dict()})
    return {"picked_at": now.isoformat(), "seed": SEED, "pending": len(qs), "number_questions": len(number),
            "pool": pool, "excluded": {str(k): v for k, v in (exclude or {}).items()}, "kinds": dict(Counter(kinds.values())), "questions": rows}


def pick_lines(p: dict[str, Any]) -> list[str]:
    out = [f"{p['pending']} pending tournament questions, {p['number_questions']} about a number; candidates per group: {p['pool']}"]
    for g in source_links.GROUPS:
        rows = [r for r in p["questions"] if r["group"] == g]
        out.append(f"\n{g}: {len(rows)} of {PER_GROUP}" + ("  (SHORT)" if len(rows) < PER_GROUP else ""))
        out += [f"  {r['question_id']} {r['type']}, {r['kind']}, resolves {r['resolves']}: {r['question']}" for r in rows]
    return out


# --------------------------------------------------------------------------------------------------------------------- run
def sheet_row(r: ReadResult, row: dict[str, Any]) -> dict[str, Any]:
    raw = r.raw or {}
    lst = lambda k: raw.get(k) if isinstance(raw.get(k), list) else []  # noqa: E731
    auto = {"not_found": "not found", "no_link": "not found", "over_cap": "not found", "error": "not found"}.get(r.status, "")
    return {"question_id": r.question_id, "group": row["group"], "type": row["type"], "question": row["question"], "reader": r.reader,
            "status": r.status, "value": raw.get("current_value") or "", "as_of_date": raw.get("as_of_date") or "",
            "source_url": raw.get("source_url") or "", "quote": str(raw.get("quote") or "")[:300], "quote_check": r.quote_check,
            "problems": "; ".join(r.problems), "history_rows": len(lst("history")), "rows_on_pages": r.rows_on_pages,
            "developments": len(lst("developments")), "cost_usd": round(r.cost_usd, 5), "booked_usd": round(r.booked_usd, 5),
            "seconds": r.seconds, "searches": r.searches, "page_opens": r.page_opens, "note": r.note,
            "checked_value": "", "verdict": auto, "rows_checked": "", "rows_correct": ""}


def write_sheet(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as fh:  # utf-8-sig: Excel opens it right
        w = csv.DictWriter(fh, fieldnames=SHEET)
        w.writeheader()
        w.writerows(rows)


async def run(picks: dict[str, Any], readers: dict[str, Reader], out: Path, max_run_usd: float = MAX_RUN_USD) -> list[dict[str, Any]]:
    """Every reader on every question, one at a time. Stops before a reader whose worst case would pass the run's limit; written to
    disk after every answer, so an aborted run keeps what was paid for."""
    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    spent, stopped = 0.0, ""
    with (out / "results.jsonl").open("w", encoding="utf-8") as fh:
        for row in picks["questions"]:
            q = snapshot_from_dict(row["snapshot"])
            for name in readers:
                if stopped:
                    break
                if spent + RESERVE_USD[name] > max_run_usd:
                    stopped = f"run limit: ${spent:.3f} spent, {name} could need ${RESERVE_USD[name]:.2f} of ${max_run_usd:.2f}"
                    print(stopped)
                    break
                try:
                    r = await readers[name].read(q)
                except Exception as e:  # noqa: BLE001 - one failed reading must not lose the run
                    r = ReadResult(name, q.question_id, status="error", note=f"{type(e).__name__}: {e}"[:200],
                                   booked_usd=RESERVE_USD[name])
                spent += r.booked_usd
                fh.write(json.dumps({**asdict(r), "group": row["group"]}, ensure_ascii=False, default=str) + "\n")
                fh.flush()
                rows.append(sheet_row(r, row))
                write_sheet(out / "sheet.csv", rows)
                print(f"{q.question_id} {row['group']} {name}: {r.status} {('; '.join(r.problems))[:80]} ${r.cost_usd:.4f} "
                      f"{r.seconds:.0f}s (run ${spent:.3f})")
    expected = len(picks["questions"]) * len(readers)
    (out / "summary.md").write_text("\n".join(report_lines(rows, stopped=stopped, expected=expected)) + "\n", encoding="utf-8")
    return rows


# ------------------------------------------------------------------------------------------------------------------ report
def _f(x: Any) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def invented(r: dict[str, Any]) -> bool:
    """Pass rule: a figure whose quote is not in the fetched text, or that differs from the hand check."""
    return bool(r["value"]) and (r["quote_check"] == "failed" or r["verdict"] == "wrong")


def reader_verdict(rows: list[dict[str, Any]], n_questions: int = 15) -> tuple[str, list[str]]:
    """('pass' / 'fail' / 'open', reasons). Open: figures not checked by hand yet, or not every question run."""
    why: list[str] = []
    over = sum(r["status"] == "over_cap" or _f(r["cost_usd"]) > CAP_USD for r in rows)
    bad = sum(invented(r) for r in rows)
    found = sum(r["verdict"] == "found" and r["status"] == "found" for r in rows)
    unchecked = sum(bool(r["value"]) and r["verdict"] not in VERDICTS for r in rows)
    if over:
        why.append(f"over the cap on {over}")
    if bad:
        why.append(f"{bad} invented figure(s)")
    if len(rows) < n_questions:
        why.append(f"{len(rows)} of {n_questions} questions run")
    if unchecked:
        why.append(f"{unchecked} figures not checked by hand")
    if not over and not bad and (len(rows) < n_questions or unchecked):
        return "open", why
    if found < 8:
        why.append(f"correct figure on {found}, needs 8")
    return ("fail" if over or bad or found < 8 else "pass"), why


def report_lines(rows: list[dict[str, Any]], stopped: str = "", expected: int = 60) -> list[str]:
    out = ["# Researcher pilot (B-102)", "", "Counts, not percentages: 5 questions per group is small. No forecasts, no scores.", ""]
    if stopped or len(rows) < expected:
        out += [f"**Incomplete: {len(rows)} of {expected} readings.** {stopped}", ""]
    by_reader: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_reader[r["reader"]].append(r)
    out += ["| Reader | Read | Block accepted | Refused by validator | Not found / no link | Over cap | Error | Correct (hand) | Wrong (hand) | "
            "History rows | Rows on the cited page | Rows correct / checked | Cost, total | Cost per question, mean / max | Booked, total | "
            "Seconds, mean / max | Searches | Page opens |", "|" + "---|" * 18]
    for name in READERS:
        rs = by_reader.get(name) or []
        if not rs:
            continue
        c = Counter(r["status"] for r in rs)
        costs, secs = [_f(r["cost_usd"]) for r in rs], [_f(r["seconds"]) for r in rs]
        out.append(f"| {name} | {len(rs)} | {c['found']} | {c['rejected']} | {c['not_found'] + c['no_link']} | {c['over_cap']} | "
                   f"{c['error']} | {sum(r['verdict'] == 'found' for r in rs)} | {sum(r['verdict'] == 'wrong' for r in rs)} | "
                   f"{int(sum(_f(r['history_rows']) for r in rs))} | {int(sum(_f(r['rows_on_pages']) for r in rs))} | "
                   f"{int(sum(_f(r['rows_correct']) for r in rs))} / {int(sum(_f(r['rows_checked']) for r in rs))} | ${sum(costs):.3f} | "
                   f"${sum(costs) / len(rs):.3f} / ${max(costs):.3f} | ${sum(_f(r['booked_usd']) for r in rs):.3f} | "
                   f"{sum(secs) / len(rs):.0f} / {max(secs):.0f} | {int(sum(_f(r['searches']) for r in rs))} | "
                   f"{int(sum(_f(r['page_opens']) for r in rs))} |")
    out += ["", "| Group | " + " | ".join(f"{n}: correct / wrong / not found / unchecked" for n in READERS) + " |",
            "|---|" + "---|" * len(READERS)]
    for g in source_links.GROUPS:
        cells = []
        for name in READERS:
            rs = [r for r in by_reader.get(name) or [] if r["group"] == g]
            v = Counter(r["verdict"] or "unchecked" for r in rs)
            cells.append(f"{v['found']} / {v['wrong']} / {v['not found']} / {v['unchecked']} (n {len(rs)})")
        out.append(f"| {g} | " + " | ".join(cells) + " |")
    out += ["", "Pass rules (fixed before the run): under the caps, no invented figure, correct figure on at least 8 of 15."]
    for name in READERS:
        rs = by_reader.get(name) or []
        if rs:
            v, why = reader_verdict(rs, max(15, expected // len(READERS)))
            out.append(f"- {name}: **{v}**" + (f" ({'; '.join(why)})" if why else ""))
    js = [r for r in by_reader.get("R1") or [] if r["group"] == "javascript"]
    if js:
        ok = sum(r["verdict"] == "found" and r["status"] == "found" for r in js)
        unchecked = sum(bool(r["value"]) and r["verdict"] not in VERDICTS for r in js)
        out += ["", f"R1 on the JavaScript group: {ok} of {len(js)}" + (f" so far, {unchecked} unchecked" if unchecked else "")
                + (". Solved (4 or 5 of 5): a small fix proposal for EXP-013's source step." if len(js) == 5 and ok >= 4 else
                   f". Not a 4-of-5 result{' (group short of 5)' if len(js) < 5 else ''}." if not unchecked else ".")]
    return out


def read_sheet(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    bad = sorted({r["verdict"] for r in rows} - {"", *VERDICTS})
    if bad:
        raise SystemExit(f"verdict must be one of {VERDICTS} or empty; found {bad}")
    return rows


# -------------------------------------------------------------------------------------------------------------------- main
async def _run(args: argparse.Namespace, picks: dict[str, Any], out: Path) -> None:
    from evals.researcher_web import Renderer
    from forecast_engine.llm import OpenRouterClient

    clock = SystemClock()
    llm = OpenRouterClient(clock)  # OPENROUTER_API_KEY, the experiments key
    async with Renderer() as web:
        readers: dict[str, Reader] = {"R0": R0(llm, clock), "R1": R1(llm, clock, web), "R2": R2(clock, web), "R3": R3(clock, web)}
        readers = {n: r for n, r in readers.items() if n in args.readers.split(",")}
        await run(picks, readers, out, min(args.max_cost, MAX_RUN_USD))
        print(f"addresses the proxy refused: {len(web.refused)}")


async def _check_browser(picks: dict[str, Any]) -> None:
    from evals.researcher_web import Renderer

    async with Renderer() as web:
        for row in picks["questions"]:
            for url in row["links"]:
                p = await web.open(url)
                print(f"{row['question_id']} {row['group']} {p.kind} status {p.status} chars {len(p.text)} links {len(p.links)} "
                      f"{p.seconds:.0f}s {p.error} {url[:90]}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = p.add_mutually_exclusive_group(required=True)
    for m in ("--pick", "--check-browser", "--run", "--report"):
        mode.add_argument(m, action="store_true")
    p.add_argument("--data", type=Path, default=ROOT / "data")
    p.add_argument("--picks", type=Path)
    p.add_argument("--sheet", type=Path)
    p.add_argument("--exclude", action="append", default=[], metavar="ID:REASON",
                   help="--pick only: leave a question out (before any paid call), with the reason saved in the picks file")
    p.add_argument("--readers", default=",".join(READERS))
    p.add_argument("--max-cost", type=float, default=MAX_RUN_USD, help=f"USD, never above {MAX_RUN_USD}")
    p.add_argument("--go", action="store_true", help="required for --run: Christian has approved the spend")
    args = p.parse_args(argv)
    now = datetime.now(timezone.utc)
    out_dir = args.data / OUT_DIR
    if args.pick:
        picks = pick(args.data, now, exclude={int(x.split(":", 1)[0]): x.partition(":")[2] for x in args.exclude})
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"picks-{now:%Y-%m-%d}.json"
        path.write_text(json.dumps(picks, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print("\n".join(pick_lines(picks)), f"\n\nsaved: {path}")
        return 0
    if args.report:
        if not args.sheet:
            p.error("--report needs --sheet")
        rows = read_sheet(args.sheet)
        lines = report_lines(rows, expected=max(60, len(rows)))
        (args.sheet.parent / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n".join(lines))
        return 0
    if not args.picks:
        p.error("needs --picks (the list shown to Christian)")
    picks = json.loads(args.picks.read_text(encoding="utf-8"))
    if args.check_browser:
        asyncio.run(_check_browser(picks))
        return 0
    if not args.go:
        print(f"--run is PAID (up to ${MAX_RUN_USD:.2f}; R3 is {R3_MODEL}). It needs Christian's go in the thread, then --go.")
        return 2
    import dotenv

    dotenv.load_dotenv(ROOT / ".env")
    missing = [k for k in ("OPENROUTER_API_KEY", "GEMINI_API_KEY") if not os.getenv(k)]
    if missing:
        print(f"not set: {missing}")
        return 2
    due = [r["question_id"] for r in picks["questions"] if r["resolves"] <= f"{now:%Y-%m-%d}"]
    if due:
        print(f"already due, draw again: {due}")
        return 2
    out = out_dir / f"run-{now:%Y%m%dT%H%M%SZ}"
    asyncio.run(_run(args, picks, out))
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
