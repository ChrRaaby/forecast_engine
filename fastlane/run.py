"""FAST lane runner.   python -m fastlane.run --pick-only | --run --go | --resolve | --report | --check-feeds

--pick-only   fetch candidates and write the list to data/fastlane/dryrun/<date>.json. No model call, no spend. (Step 3 dry run.)
--run --go    pick, research, forecast with every arm, write records. Needs FASTLANE_ON=1 in the environment as well, so a stray
              command or a workflow without the switch cannot spend. Guards: $80 cap on the lane's own ledger, AskNews guard; both fail closed.
              A question the AskNews guard refuses is skipped and counted, so all forecast questions have the same research.
--resolve     look up outcomes of closed questions (no model call).
--report      counts only (picked / resolved / voided / open, records per arm, AskNews and spend).
--look        the experiment's one look (fastlane.analyze): refuses to print a score before the card's question count or date.
A run serves one experiment (--experiment, default EXP-017; fastlane/experiments.py), which fixes its arms, cap and stops. Arms come
from fastlane/arms.py. EXP-017's A is `cheap`: base research, one AskNews and one Gemini search. A is not the live bot's research
(the full EXP-013 package since 2026-11-23, about 7 AskNews credits a question, too dear here): results compare arms with each
other, not with the live bot (Christian, 2026-11-24). exp013-d-news costs 6 extra AskNews credits a question and is only used if named with --arms.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from forecast_engine import prompt_versions, provenance, research_followup, research_v2
from forecast_engine.clock import Clock, SystemClock
from forecast_engine.core import ForecastFailed, forecast
from forecast_engine.llm import LlmClient, OpenRouterClient
from forecast_engine.records import RecordWriter
from forecast_engine.research import gather_research
from forecast_engine.schema import ResearchBundle

from . import analyze, picker, resolve, series
from .arms import ARMS
from .experiments import DEFAULT_EXPERIMENT, EXPERIMENTS, PINNED_HOSTS, Experiment
from .guard import AskNewsGuard, GuardStop, SpendGuard
from .scrub import scrub_bundle
from .types import FastQuestion, GetJson

logger = logging.getLogger("fastlane")
ROOT = Path("data/fastlane")
DEFAULT_MAX_RUN_USD = 2.0  # one run is a handful of questions at about $0.05-0.10 each; the $80 lane cap is the long-run limit
DEFAULT_ARMS = EXPERIMENTS[DEFAULT_EXPERIMENT].arms
NEWS_CREDITS = 1 + 5  # EXP-013 D: keyword latest search + historical search (research_v2.CREDITS_*)


def http_get(url: str, params: dict[str, Any] | None = None) -> Any:
    r = requests.get(url, params=params, headers={"User-Agent": "fastlane-research/1.0"}, timeout=30)
    r.raise_for_status()
    return r.json()


def load_picks(root: Path = ROOT) -> list[dict[str, Any]]:
    p = root / "picks.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []


def collect(get: GetJson, now: datetime, root: Path = ROOT, n_series: int = 6, cfg: picker.PickerConfig = picker.PickerConfig()
            ) -> list[FastQuestion]:
    picks = load_picks(root)
    done = {p["key"] for p in picks}
    done_events = {str(p["meta"].get("event_slug")) for p in picks if p["source"] == "polymarket" and p["meta"].get("event_slug")}
    qs: list[FastQuestion] = []
    try:
        qs += picker.pick(picker.fetch_events(get, now, cfg), now, done, cfg, done_events)
    except Exception as e:
        logger.warning(f"Polymarket picker failed: {type(e).__name__}: {e}")
    qs += series.make_questions(get, now, n_series, done=done)
    return qs


def pick_only(get: GetJson, clock: Clock, root: Path = ROOT, n_series: int = 6) -> Path:
    now = clock.now()
    qs = collect(get, now, root, n_series)
    out = root / "dryrun" / f"{now:%Y%m%dT%H%M%SZ}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([q.to_pick(now) for q in qs], indent=1, ensure_ascii=False), encoding="utf-8")
    return out


def _google_queries(calls) -> int:
    """Grounding queries Gemini ran in these calls, for the spend guard's fee at list price. A call whose cost already includes
    the fee (the EXP-013 writer records `grounding_fee_usd`) is left out, so the fee is counted once."""
    return sum(len(c.extra.get("search_queries") or []) for c in calls
               if c.provider == "gemini" and "grounding_fee_usd" not in c.extra)


async def run_questions(qs: list[FastQuestion], arms: tuple[str, ...], clock: Clock, llm: LlmClient, spend: SpendGuard,
                        news: AskNewsGuard, root: Path = ROOT, max_run_usd: float = DEFAULT_MAX_RUN_USD,
                        experiment: str | None = None) -> dict[str, Any]:
    unknown = [a for a in arms if a not in ARMS]
    if unknown or ARMS[arms[0]].research != "shared":
        raise ValueError(f"unknown arms {unknown}, or the baseline {arms[0]!r} has no research")
    run_id = clock.now().strftime("%Y%m%dT%H%M%SZ")
    run_info = provenance.gather_run_info(run_id, "fastlane", False)
    writers = {a: RecordWriter(f"{run_id}-fast-{a}", root=root / "forecasts") for a in arms}
    summary = {"questions": 0, "records": 0, "failed": 0, "usd": 0.0, "asknews_credits": 0, "asknews_skipped": 0,
               "odds_scrubbed_questions": 0, "research_incomplete": 0, "stopped": None}
    for fq in qs:
        if summary["usd"] >= max_run_usd:
            summary["stopped"] = f"run cost cap ${max_run_usd:g}"
            break
        snap, base = fq.snapshot, ARMS[arms[0]].cfg
        degraded: list[str] = []
        wants_news = "asknews_latest" in base.research.providers
        why = news.why_not(1) if wants_news else None
        if why:  # no forecast on a different research package: skip the question, count it, say why; it can be picked again
            news.skip(1)
            summary["asknews_skipped"] += 1
            summary["asknews_why"] = why
            continue
        if wants_news:
            news.take(1)
            summary["asknews_credits"] += 1
        research = await gather_research(snap, clock, base.research)
        extra = sum(c.extra.get("asknews_calls", 0) for c in research.calls) - 1
        if wants_news and extra > 0:  # the provider retried after a rate limit: that attempt may be billed too
            news.take(extra)
            summary["asknews_credits"] += extra
        if research.errors:
            degraded.append(f"fastlane: research incomplete ({len(research.errors)} provider error(s))")
            summary["research_incomplete"] += 1
        charged = {id(c) for c in research.calls}
        spend.add(research.cost_usd, _google_queries(research.calls))
        summary["usd"] += research.cost_usd
        research, report = scrub_bundle(research)
        if report.any:
            degraded.append(report.flag())
            summary["odds_scrubbed_questions"] += 1
        cache: dict = {}
        _append(root / "picks.jsonl", {**fq.to_pick(clock.now()), "experiment": experiment})
        if fq.price_at_pick is not None:  # private: no prompt, research call or record reads this file
            _append(root / "market_prices.jsonl", {"key": fq.key, "price_at_pick": fq.price_at_pick, "at": clock.now().isoformat()})
        summary["questions"] += 1
        for arm in arms:
            scfg = ARMS[arm].cfg
            if scfg.research.news is not None:  # EXP-013 D: more AskNews credits, taken up front or the arm skips this question
                if not news.allow(NEWS_CREDITS):
                    news.skip(NEWS_CREDITS)
                    summary["asknews_skipped"] += NEWS_CREDITS
                    logger.info(f"{arm} skipped on {fq.key}: AskNews guard (counted)")
                    continue
                news.take(NEWS_CREDITS)
                summary["asknews_credits"] += NEWS_CREDITS
            prov = provenance.record_provenance(run_info, scfg, role="fastlane", roster=arm)
            arm_flags = list(degraded)
            try:
                bundle = research
                if ARMS[arm].research == "none":  # the sensitivity control: nothing fetched, nothing shown
                    bundle = ResearchBundle(as_of=research.as_of, providers=[], items=[], calls=[],
                                            errors=["fastlane: no-research control arm"])
                elif research_v2.active(scfg.research):
                    bundle = await research_v2.apply(snap, bundle, clock, scfg.research, llm, cache)
                if scfg.research.followup is not None:
                    bundle = await research_followup.followup(snap, bundle, clock, scfg.research, llm)
                new = [c for c in bundle.calls if id(c) not in charged]
                charged.update(id(c) for c in new)
                step_usd, step_queries = sum(c.cost_usd or 0.0 for c in new), _google_queries(new)
                bundle, rep2 = scrub_bundle(bundle)  # steps that add research (EXP-013) are scrubbed too
                if rep2.any and rep2.flag() not in arm_flags:
                    arm_flags.append(rep2.flag())
                spend.add(step_usd, step_queries)  # the research steps' own calls, charged once here
                rec = await forecast(snap, clock, bundle, scfg, llm)
                usd = rec.cost_usd - bundle.cost_usd  # the forecasters (and polarity check) only: the research is already charged
                status = "fastlane"
            except ForecastFailed as e:
                rec, usd, status = e.record, e.record.cost_usd - bundle.cost_usd, "fastlane_failed"
                summary["failed"] += 1
            except GuardStop:
                raise
            except Exception as e:  # one arm never takes the others down
                logger.warning(f"{arm} failed on {fq.key}: {type(e).__name__}: {e}")
                summary["failed"] += 1
                continue
            rec.flags.extend(arm_flags)
            writers[arm].write(rec, provenance=prov, published=False, status=status)
            summary["records"] += 1
            summary["usd"] += usd + step_usd
            spend.add(usd)
        spend.save()  # after every question: a crash loses at most one question's spend record
    return summary


def picking_over(exp: Experiment, root: Path, now: datetime) -> str | None:
    """Why the experiment takes no new questions, or None."""
    if now.date() > exp.last_pick_day:
        return f"last picking day {exp.last_pick_day} has passed"
    n = len([p for p in load_picks(root) if p.get("experiment") == exp.name])
    return f"{n} questions picked, the card's maximum is {exp.max_picked}" if n >= exp.max_picked else None


def _append(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    ap = argparse.ArgumentParser(description="FAST lane (EXP-017 screen)")
    g = ap.add_mutually_exclusive_group(required=True)
    for flag in ("pick-only", "run", "resolve", "report", "look", "check-feeds"):
        g.add_argument(f"--{flag}", action="store_true")
    ap.add_argument("--go", action="store_true", help="with --run: really call the models (also needs FASTLANE_ON=1)")
    ap.add_argument("--experiment", default=DEFAULT_EXPERIMENT, choices=sorted(EXPERIMENTS))
    ap.add_argument("--series", type=int, default=6, help="code-made questions per run")
    ap.add_argument("--max-picks", type=int, default=12, help="Polymarket questions per run")
    ap.add_argument("--max-run-usd", type=float, default=DEFAULT_MAX_RUN_USD)
    a = ap.parse_args(argv)
    clock = SystemClock()
    if a.check_feeds:
        res = series.check_feeds(http_get)
        print(json.dumps(res, indent=1))
        return 1 if any(v.startswith("FAILED") for v in res.values()) else 0
    if a.resolve:
        print(resolve.update(http_get, clock.now()))
        return 0
    if a.report:
        print(json.dumps(resolve.counts(), indent=1))
        return 0
    if a.look:
        text, ok = analyze.look(EXPERIMENTS[a.experiment], ROOT, clock.now())
        print(text)
        return 0 if ok else 2
    if a.pick_only:
        print(pick_only(http_get, clock, n_series=a.series))
        return 0
    # --run
    if not (a.go and os.getenv("FASTLANE_ON") == "1"):
        print("refusing to spend: --run needs both --go and FASTLANE_ON=1 (no model calls were made)")
        return 2
    exp = EXPERIMENTS[a.experiment]
    why_stop = picking_over(exp, ROOT, clock.now())
    if why_stop:
        print(f"{exp.name}: no new questions ({why_stop})")
        return 0
    spend = SpendGuard(experiment=exp.name, experiment_cap_usd=exp.cap_usd)
    try:
        spend.start(clock.now().date())
    except GuardStop as e:
        print(f"stopped: {e}")
        return 0
    news = AskNewsGuard()
    why = news.why_not(1)
    if why:
        print(f"stopped before picking: {why}")
        return 0
    qs = collect(http_get, clock.now(), n_series=a.series, cfg=picker.PickerConfig(max_picks=a.max_picks))
    saved = True
    try:
        qs = qs[:max(0, exp.max_picked - len([p for p in load_picks(ROOT) if p.get("experiment") == exp.name]))]
        llm = OpenRouterClient(clock, pinned_hosts=PINNED_HOSTS)
        summary = asyncio.run(run_questions(qs, exp.arms, clock, llm, spend, news, max_run_usd=a.max_run_usd,
                                            experiment=exp.name))
    except GuardStop as e:
        summary = {"stopped": str(e)}
    finally:
        try:
            spend.save()
        except GuardStop as e:  # state unreadable: say so, don't mask the run's own outcome
            print(f"spend state not saved: {e}")
            saved = False
    print(json.dumps(summary, indent=1))
    return 0 if saved else 1  # non-zero keeps the workflow's RUNNING marker, so nothing spends until a person has looked


if __name__ == "__main__":
    sys.exit(main())
