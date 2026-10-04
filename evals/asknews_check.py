"""One-command live check of the as-of research layer (B-28). Run on the PC, where .env holds the keys:

    poetry run python -m evals.asknews_check --dry-run          # budget status only, spends nothing
    poetry run python -m evals.asknews_check                    # one archive search (5 AskNews credits) + one screen call
    poetry run python -m evals.asknews_check --used 35          # also pass the total shown on the AskNews usage page

What it checks, end to end: budget guard → AskNews archive search with end_timestamp = as_of → date guard → cache round trip
(in a temporary folder) → leakage screen (only if OPENROUTER_API_KEY is set; skip with --no-screen; costs ~$0.001).
It prints counts, dates and titles to the terminal only; article text is not printed and nothing is written to the repo.
Exit code 0 = all checks passed.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from forecast_engine.clock import FixedClock, SystemClock
from forecast_engine.schema import QuestionSnapshot

from .asknews_archive import ArchiveConfig, SdkArchiveClient, archive_search
from .asknews_budget import CREDITS_PER_ARCHIVE_SEARCH, AskNewsBudget, BudgetConfig, BudgetExceeded
from .asof import LeakageError, assert_bundle_as_of
from .leakage_screen import DualScreenConfig, run_screen, ScreenConfig, screen_bundle
from .research_cache import ResearchCache

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AS_OF = "2025-11-01T00:00:00+00:00"
DEFAULT_QUERY = "Will the US Federal Reserve cut its policy interest rate at its next meeting?"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="print the budget and stop; spends nothing")
    ap.add_argument("--as-of", default=DEFAULT_AS_OF, help=f"ISO timestamp with time zone (default {DEFAULT_AS_OF})")
    ap.add_argument("--query", default=DEFAULT_QUERY)
    ap.add_argument("--used", type=int, default=None, help="credits used this period, from the AskNews usage page")
    ap.add_argument("--allow-overage", action="store_true", help="let the search use the $5 wallet if the plan is spent")
    ap.add_argument("--no-screen", action="store_true", help="skip the leakage-screen call")
    args = ap.parse_args(argv)
    load_dotenv(ROOT / ".env")

    budget = AskNewsBudget(SystemClock(), BudgetConfig(allow_overage=args.allow_overage), observed_used=args.used)
    print(budget.status().summary())
    if args.dry_run:
        return 0
    try:
        budget.check(CREDITS_PER_ARCHIVE_SEARCH)
    except BudgetExceeded as e:
        print(f"STOP  {e}")
        return 2

    as_of = datetime.fromisoformat(args.as_of)
    if as_of.tzinfo is None:
        sys.exit("--as-of needs a time zone, e.g. 2025-11-01T00:00:00+00:00")
    q = QuestionSnapshot(question_id=0, post_id=None, question_type="binary", question_text=args.query)
    cfg = ArchiveConfig(min_interval_s=0)
    results: list[tuple[str, bool, str]] = []

    try:
        bundle = asyncio.run(archive_search(q, FixedClock(as_of), cfg, SdkArchiveClient.from_env(), budget))
    except LeakageError as e:
        print(f"FAIL  date guard let a bad item through: {e}")
        return 1
    call = bundle.calls[0]
    print(f"\nArchive search as of {as_of.isoformat()} ({call.latency_s:.1f} s): {call.error or 'ok'}")
    if call.error:
        return 1
    x = call.extra
    print(f"  returned {x['n_articles_returned']}, kept {x['n_kept']}, rejected {dict(x['rejected']) or 'none'}")
    print(f"  returned pub_date range: {x['returned_pub_min']} .. {x['returned_pub_max']}")
    for item in bundle.items:
        print(f"  {item.published_at:%Y-%m-%d %H:%M}  {(item.title or '')[:90]}")

    rej = x["rejected"]
    results.append(("search returned articles", x["n_articles_returned"] > 0, f"{x['n_articles_returned']} returned"))
    results.append(("server honoured end_timestamp", rej.get("after_as_of", 0) == 0, f"{rej.get('after_as_of', 0)} after as_of"))
    results.append(("every article dated", rej.get("undated", 0) + rej.get("naive_date", 0) == 0,
                    f"{rej.get('undated', 0)} undated, {rej.get('naive_date', 0)} without time zone"))
    dates = [i.published_at for i in bundle.items]
    if len(dates) >= 3:
        spread = max(dates) - min(dates)
        results.append(("lookback window used (not just the SDK's 24 h default)", spread > timedelta(days=1),
                        f"kept articles span {spread}"))
    else:  # too few articles to measure the span: that's a failure of the check, not a pass (B-45 review)
        results.append(("lookback window used (not just the SDK's 24 h default)", False,
                        f"only {len(dates)} articles kept; can't verify the window (try another --query or --as-of)"))
    try:
        with tempfile.TemporaryDirectory() as tmp:
            cache = ResearchCache(tmp)
            cache.put(q.question_id, as_of, cfg.config_hash(), q.question_text, bundle)
            back = cache.get(q.question_id, as_of, cfg.config_hash(), q.question_text)
            assert back is not None
            assert_bundle_as_of(back, as_of)
            same = back.to_dict() == bundle.to_dict()
        results.append(("cache round trip", same, "identical bundle read back" if same else "bundle changed on read"))
    except Exception as e:
        results.append(("cache round trip", False, f"{type(e).__name__}: {e}"))

    if args.no_screen or not os.getenv("OPENROUTER_API_KEY"):
        print("\nLeakage screen skipped" + ("" if args.no_screen else " (OPENROUTER_API_KEY not set)"))
    else:
        from forecast_engine.llm import OpenRouterClient

        scfg = DualScreenConfig()
        verdict = asyncio.run(run_screen(q, bundle, OpenRouterClient(FixedClock(as_of)), scfg))
        cost = verdict.call.cost_usd if verdict.call else None
        print(f"\nLeakage screen ({scfg.primary.model} + {scfg.secondary.model}, both must flag): {verdict.status}, items {verdict.flagged_items}, cost {cost}")
        print(f"  reason: {verdict.reason[:300]}")
        results.append(("screen returned a verdict", verdict.status != "screen_failed", verdict.status))

    print("\n" + budget.status().summary())
    print()
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
