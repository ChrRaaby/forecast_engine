"""Cost watch (B-101, Christian 2026-11-23: "monitor those cost continuously, notify me as soon as estimates do not seem to hold").

Compares what was really spent with the estimates and says OK or WARN per line. Read-only and free: it reads the archived run
records and logs, the OpenRouter usage of the experiments key (GET /key) and a dated reading of the AskNews usage page. It runs in
the daily archive task (tools/build_monitor.py --sync-only: WARN lines land in data/runs/_sync.log) and by hand. When a line turns
WARN, a Windows notification that stays on screen until it is closed is shown in the evening (Christian, 2026-11-24: "around 10 pm";
the task has a 22:00 start): a run earlier in the day holds it back, and if the PC was off that evening the next run shows it.

    poetry run python tools/cost_watch.py            # print the report; exit 1 if anything is WARN
    poetry run python tools/cost_watch.py --save --notify   # what the archive task does

Saved to the private data repo: data/cost_watch/latest.md + latest.json (for the weekly report) and history.jsonl (key readings).
Numbers that can change (AskNews wallet, price per credit, renewal day) are read from data/cost_watch/asknews_usage.json or the fast
lane's reading, whichever is newer; update it from the AskNews usage page. Estimates and limits are in `Limits` below.

"Real" $ = OpenRouter cost + Gemini tokens at list price; Google's search fee is left out (not billed so far, checked 2026-11-14).
A shared research step (same call in several records) is counted once, for the live forecast first.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "data" / "cost_watch"
EVENING = time(21, 45)  # local time; the archive task's evening start is 22:00
FASTLANE = ROOT.parent / "forecast_engine_data_fastlane" / "fastlane"


@dataclass(frozen=True)
class Limits:
    horizon: date = date(2027, 1, 31)  # projections run to the end of the experiments (cards: 2027-01-31)
    window_days: int = 7
    min_questions: int = 8  # fewer in the window: report, don't judge
    # live $ per question on OpenRouter, by config hash: (estimate, warn above)
    live_per_q: tuple[tuple[str, float, float], ...] = (
        ("f3c50c57f8d1", 0.13, 0.16),  # frontier on the base research (until the EXP-013 go-live)
        ("d7d19c6f8464", 0.19, 0.22),  # frontier on the full research (ADR-0013)
    )
    season_cap: float = 300.0
    # EXP-013: cheap arms B + C + D on a $60 line (card). Frontier arm: the card said $57-68 to 31 January ($0.12 per question); measured
    # $0.166, and Christian accepted about $0.17 per question, up to about $124, on 2026-11-23.
    cheap_line: float = 60.0
    frontier_arm_est: float = 124.0
    frontier_arm_per_q: tuple[float, float] = (0.17, 0.20)
    asknews_per_q: tuple[float, float] = (7.0, 9.0)
    asknews_burn: tuple[float, float] = (42.0, 52.5)  # credits/day, whole account, 16-23 Nov; warn 25% above
    wallet_floor: float = 5.0
    google_free_per_month: int = 5000
    google_warn: int = 4000


CHEAP_ARMS = ("exp013-b-source", "exp013-c-writer", "exp013-d-news")
FRONTIER_ARMS = ("exp013-f-frontier-full", "exp013-frontier-old")


@dataclass
class Line:
    key: str
    status: str  # OK | WARN | INFO
    text: str


# ------------------------------------------------------------------------------------------------------------ facts
def call_key(c: dict[str, Any]) -> tuple:
    return (c.get("purpose"), c.get("requested_at"), c.get("model"))


def step_cost(c: dict[str, Any]) -> float:
    return float((c.get("extra") or {}).get("token_cost_usd", c.get("cost_usd") or 0) or 0)


def record_facts(name: str, r: dict[str, Any], seen: set[tuple]) -> dict[str, Any]:
    """Spend facts of one stored record. `seen` holds the research calls of this question already counted (live first)."""
    m = re.search(r"-shadow-(.+)$", name)
    forecasts = sum(float(c.get("cost_usd") or 0) for x in r["forecasters"] for c in (x["call"], x.get("check")) if c)
    openrouter_steps = gemini_steps = 0.0
    credits = queries = 0
    for c in r["research"]["calls"]:
        k = call_key(c)
        if k in seen:
            continue
        seen.add(k)
        x = c.get("extra") or {}
        queries += len(x.get("search_queries") or [])
        if c.get("provider") == "asknews":
            if not c.get("error"):
                credits += int(x.get("asknews_credits", x.get("asknews_calls", 1)))
        elif c.get("provider") == "openrouter":
            openrouter_steps += step_cost(c)
        elif c.get("provider") == "gemini":
            gemini_steps += step_cost(c)
    return {"arm": m.group(1) if m else "live", "at": r["as_of"][:19], "qid": r["question"]["question_id"],
            "hash": r.get("config_hash"), "forecasts": forecasts, "openrouter_steps": openrouter_steps, "gemini_steps": gemini_steps,
            "credits": credits, "queries": queries}


def load_facts(runs: Path, since: datetime) -> list[dict[str, Any]]:
    groups: dict[tuple, list[tuple[str, Path]]] = {}
    stamp = since.strftime("%Y%m%dT%H%M%S")
    for f in runs.glob("*/data/forecasts/*/q*.json"):
        name = f.parent.name
        if name < stamp or not ("-tournament" in name) or "dryrun" in name:
            continue
        groups.setdefault((name[:16], f.name), []).append((name, f))
    out = []
    for files in groups.values():
        seen: set[tuple] = set()
        for name, f in sorted(files, key=lambda x: "-shadow-" in x[0]):  # the live record first
            try:
                out.append(record_facts(name, json.loads(f.read_text(encoding="utf-8")), seen))
            except (OSError, ValueError, KeyError, TypeError):
                continue
    return out


SEASON = re.compile(r"Season fall-2026: \$([0-9.]+) of \$300 \(source: OpenRouter live key")


def season_readings(runs: Path) -> list[tuple[str, float]]:
    """(time, season total) read by the bot from the live key at the start of each archived run."""
    rows = []
    for p in runs.glob("*/_run.log"):
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m, d = SEASON.search(t), re.search(r"(20\d\d-\d\d-\d\dT\d\d:\d\d)", t)
        if m and d:
            rows.append((d.group(1), float(m.group(1))))
    return sorted(rows)


def read_json(p: Path) -> dict[str, Any] | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def asknews_reading(paths: Iterable[Path]) -> dict[str, Any] | None:
    """The newest dated reading of the AskNews usage page (ours or the fast lane's)."""
    found = [d for d in (read_json(p) for p in paths) if d and d.get("as_of") and d.get("renews")]
    return max(found, key=lambda d: d["as_of"], default=None)


def openrouter_key() -> dict[str, float] | None:
    import requests

    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        return None
    try:
        d = requests.get("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"}, timeout=20).json()["data"]
        return {"usage": float(d["usage"]), "limit": float(d["limit"]) if d.get("limit") else None}
    except Exception as e:  # the watch still reports the rest
        print(f"warning: OpenRouter key usage unavailable: {type(e).__name__}")
        return None


# ----------------------------------------------------------------------------------------------------------- checks
def rate(rows: list[tuple[str, float]], now: datetime, days: int) -> float | None:
    """$ per day between the newest reading and the latest one at least `days` (else the oldest one >= 3 days) older."""
    if len(rows) < 2:
        return None
    t = lambda s: datetime.fromisoformat(s).replace(tzinfo=timezone.utc)  # noqa: E731
    end_t, end_v = rows[-1]
    older = [r for r in rows if (t(end_t) - t(r[0])).total_seconds() >= days * 86400] or rows[:1]
    start_t, start_v = older[-1]
    span = (t(end_t) - t(start_t)).total_seconds() / 86400
    return (end_v - start_v) / span if span >= 3 else None


def wallet_at_horizon(reading: dict[str, Any], burn: float, horizon: date, today: date) -> float:
    """Wallet left at the horizon if the whole account keeps burning `burn` credits a day: plan credits are used first in each
    30-day period, the rest is paid from the wallet. The reading is rolled forward from its own date."""
    price, plan = float(reading["usd_per_credit"]), int(reading["plan_credits"])
    wallet, used, day = float(reading["wallet_usd"]), float(reading["period_used"]), date.fromisoformat(reading["as_of"])
    renew = date.fromisoformat(reading["renews"])
    while day < horizon:
        if day >= renew:
            used, renew = 0.0, renew + timedelta(days=30)
        over_before, used = max(0.0, used - plan), used + burn
        wallet -= (max(0.0, used - plan) - over_before) * price
        day += timedelta(days=1)
    return wallet


def evaluate(facts: list[dict[str, Any]], season: list[tuple[str, float]], key_rows: list[tuple[str, float]],
             key_limit: float | None, reading: dict[str, Any] | None, fastlane_credits: int, now: datetime,
             lim: Limits = Limits()) -> list[Line]:
    today, left = now.date(), max(0, (lim.horizon - now.date()).days)
    win = (now - timedelta(days=lim.window_days)).isoformat()[:19]
    recent = [f for f in facts if f["at"] >= win]
    live = [f for f in recent if f["arm"] == "live"]
    out: list[Line] = []

    def add(key: str, bad: bool | None, text: str) -> None:
        out.append(Line(key, "INFO" if bad is None else "WARN" if bad else "OK", text))

    # 1 live $ per question
    for h, est, warn in lim.live_per_q:
        v = [f["forecasts"] + f["openrouter_steps"] for f in live if f["hash"] == h]
        if v:
            mean = sum(v) / len(v)
            add(f"live per question ({h})", mean > warn if len(v) >= lim.min_questions else None,
                f"live forecasts cost ${mean:.3f} per question on OpenRouter (last {lim.window_days} days, {len(v)} questions); "
                f"estimate ${est:.2f}, warn above ${warn:.2f}")
    # 2 season cap
    r = rate(season, now, lim.window_days)
    if season and r is not None:
        proj = season[-1][1] + r * left
        add("season cap", proj > lim.season_cap, f"live key: ${season[-1][1]:.2f} of ${lim.season_cap:.0f} spent, ${r:.2f} a day, "
            f"projected ${proj:.0f} by {lim.horizon} (cap ${lim.season_cap:.0f})")
    # 3 experiments key
    if key_rows:
        r = rate(key_rows, now, lim.window_days)
        usage = key_rows[-1][1]
        if key_limit and r:
            days = (key_limit - usage) / r if r > 0 else 9999
            add("experiments key", days < left, f"experiments key: ${usage:.2f} of ${key_limit:.0f} used, ${r:.2f} a day; at that pace "
                f"the limit is reached around {today + timedelta(days=min(days, 3000))} ({lim.horizon}: ${usage + r * left:.0f})")
        else:
            add("experiments key", None, f"experiments key: ${usage:.2f} used; not enough readings for a pace yet")
    # 4 EXP-013
    def arm_spend(arms: tuple[str, ...], rows: list[dict[str, Any]]) -> float:
        return sum(f["forecasts"] + f["openrouter_steps"] + f["gemini_steps"] for f in rows if f["arm"] in arms)

    first = min((f["at"] for f in facts if f["arm"] in CHEAP_ARMS), default=None)
    if first:
        days = max(1.0, (now - datetime.fromisoformat(first).replace(tzinfo=timezone.utc)).total_seconds() / 86400)
        spent = arm_spend(CHEAP_ARMS, facts)
        proj = spent + spent / days * left
        add("EXP-013 cheap arms", proj > lim.cheap_line, f"EXP-013 arms B, C, D: ${spent:.2f} so far, ${spent / days:.2f} a day, "
            f"projected ${proj:.0f} by {lim.horizon} (line ${lim.cheap_line:.0f})")
        fr = [f for f in facts if f["arm"] in FRONTIER_ARMS]
        if fr:
            spent = sum(f["forecasts"] for f in fr)
            proj, per_q = spent + spent / days * left, spent / len(fr)
            add("EXP-013 frontier arm", proj > lim.frontier_arm_est, f"EXP-013 frontier arm (F / frontier-old): ${spent:.2f} so far, "
                f"projected ${proj:.0f} by {lim.horizon}; accepted up to ${lim.frontier_arm_est:.0f}")
            add("EXP-013 frontier arm per question", per_q > lim.frontier_arm_per_q[1], f"EXP-013 frontier arm: ${per_q:.3f} per "
                f"question ({len(fr)} questions); accepted ${lim.frontier_arm_per_q[0]:.2f}, warn above ${lim.frontier_arm_per_q[1]:.2f}")
    # 5 AskNews (our bookkeeping; live + shadows, plus the fast lane's own count)
    if live:
        ours = sum(f["credits"] for f in recent)
        per_q = ours / len(live)
        add("AskNews per question", per_q > lim.asknews_per_q[1] if len(live) >= lim.min_questions else None,
            f"AskNews: {per_q:.1f} credits per question (last {lim.window_days} days, {len(live)} questions); estimate "
            f"{lim.asknews_per_q[0]:.0f}, warn above {lim.asknews_per_q[1]:.0f}")
        burn = (ours + fastlane_credits) / lim.window_days
        driver = "fast lane" if fastlane_credits > ours else "live bot + shadows"
        add("AskNews burn", burn > lim.asknews_burn[1], f"AskNews: {burn:.0f} credits a day (bot {ours / lim.window_days:.0f}, fast lane "
            f"{fastlane_credits / lim.window_days:.0f}; mostly {driver}); was {lim.asknews_burn[0]:.0f}, warn above {lim.asknews_burn[1]:.0f}")
        if reading:
            age = (today - date.fromisoformat(reading["as_of"])).days
            left_usd = wallet_at_horizon(reading, burn, lim.horizon, today)
            add("AskNews wallet", left_usd < lim.wallet_floor, f"AskNews wallet: ${reading['wallet_usd']:.2f} on {reading['as_of']} "
                f"(page: {reading['period_used']} of {reading['plan_credits']} credits), at {burn:.0f} a day about ${left_usd:.2f} left on "
                f"{lim.horizon}; warn below ${lim.wallet_floor:.0f}" + (f". Reading is {age} days old: update it" if age > 7 else ""))
    # 6 Google searches (recorded queries are a lower bound: some answers come back without search data)
    month = (now - timedelta(days=30)).isoformat()[:19]
    q30 = [f for f in facts if f["at"] >= month]
    if recent:
        proj30 = sum(f["queries"] for f in recent) / lim.window_days * 30
        add("Google searches", proj30 > lim.google_warn, f"Google searches: {sum(f['queries'] for f in q30)} recorded in the last 30 days "
            f"of data, {proj30:.0f} per 30 days at the last week's pace (lower bound); free {lim.google_free_per_month} a month, warn "
            f"above {lim.google_warn}")
    return out


# ----------------------------------------------------------------------------------------------------------- output
def new_warnings(lines: list[Line], previous: dict[str, Any] | None, now: datetime, remind_days: int = 7) -> list[Line]:
    """WARN lines to notify: newly WARN, or still WARN `remind_days` after the last notification."""
    told = (previous or {}).get("notified", {})
    cut = (now - timedelta(days=remind_days)).isoformat()
    return [x for x in lines if x.status == "WARN" and told.get(x.key, "") < cut]


def evening_due(first_seen: datetime, now: datetime, at: time = EVENING) -> bool:
    """Show it now? Yes from `at` (local time) to midnight, or when an evening has passed since it was first seen (PC was off)."""
    local = now.astimezone()
    slot = datetime.combine(local.date(), at).astimezone()  # a local wall-clock time, also when the clocks change
    if local >= slot:
        return True
    last = datetime.combine(local.date() - timedelta(days=1), at).astimezone()
    return first_seen.astimezone() < last


def toast(title: str, text: str) -> bool:
    """A Windows notification (PowerShell, no extra packages). False if it could not be shown; never raises."""
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;")  # noqa: E731
    # "reminder" stays on screen until closed (it needs a button); an ordinary one is gone after seconds (missed on 2026-11-23/24)
    xml = (f'<toast scenario="reminder"><visual><binding template="ToastGeneric"><text>{esc(title)}</text><text>{esc(text[:400])}</text>'
           '</binding></visual><actions><action content="Close" activationType="system" arguments="dismiss"/></actions>'
           '<audio src="ms-winsoundevent:Notification.Reminder"/></toast>')
    ps = ("[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]|Out-Null;"
          "[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom,ContentType=WindowsRuntime]|Out-Null;"
          "$x=New-Object Windows.Data.Xml.Dom.XmlDocument;$x.LoadXml($env:COST_WATCH_TOAST);"
          "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}"
          "\\WindowsPowerShell\\v1.0\\powershell.exe').Show([Windows.UI.Notifications.ToastNotification]::new($x))")
    try:
        return subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, timeout=30,
                              env={**os.environ, "COST_WATCH_TOAST": xml}).returncode == 0
    except Exception:
        return False


def report(lines: list[Line], now: datetime) -> str:
    n = sum(x.status == "WARN" for x in lines)
    head = f"# Cost watch {now:%Y-%m-%d %H:%M} UTC: {'all estimates hold' if not n else f'{n} of {len(lines)} do not hold'}\n\n"
    return head + "\n".join(f"- {x.status}: {x.text}" for x in sorted(lines, key=lambda x: ("WARN", "INFO", "OK").index(x.status))) + "\n"


def run(save: bool, notify: bool, now: datetime | None = None, out_dir: Path = OUT, runs: Path | None = None) -> list[Line]:
    now = now or datetime.now(timezone.utc)
    runs = runs or ROOT / "data" / "runs"
    out_dir_history = out_dir / "history.jsonl"
    rows = [json.loads(x) for x in out_dir_history.read_text(encoding="utf-8").splitlines()] if out_dir_history.exists() else []
    key = openrouter_key()
    if key:
        rows.append({"at": now.isoformat()[:16], "experiments_key_usd": key["usage"]})
    key_rows = [(r["at"], float(r["experiments_key_usd"])) for r in rows if "experiments_key_usd" in r]
    reading = asknews_reading([out_dir / "asknews_usage.json", FASTLANE / "asknews_usage.json"])
    lane = (read_json(FASTLANE / "asknews.json") or {}).get("by_day", {})
    since = (now - timedelta(days=Limits().window_days)).date().isoformat()
    lane_credits = sum(int(v) for d, v in lane.items() if d >= since)
    start = min(now - timedelta(days=31), datetime(2026, 11, 14, 20, 13, tzinfo=timezone.utc))
    lines = evaluate(load_facts(runs, start), season_readings(runs), key_rows, key["limit"] if key else None, reading, lane_credits, now)
    previous = read_json(out_dir / "latest.json")
    fresh = new_warnings(lines, previous, now)
    told = dict((previous or {}).get("notified", {}))
    warn_keys = {x.key for x in lines if x.status == "WARN"}
    told = {k: v for k, v in told.items() if k in warn_keys}  # a line that is fine again is notified anew next time
    waiting = (previous or {}).get("waiting", {})  # line -> when it was first due to be told, so a missed evening is caught up
    waiting = {x.key: waiting.get(x.key, now.isoformat()) for x in fresh}
    due = [x for x in fresh if evening_due(datetime.fromisoformat(waiting[x.key]), now)]
    if notify and due and toast("Forecast bot: cost estimate does not hold", "; ".join(x.text for x in due)):
        told.update({x.key: now.isoformat() for x in due})
        waiting = {k: v for k, v in waiting.items() if k not in {x.key for x in due}}
    if save:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "latest.md").write_text(report(lines, now), encoding="utf-8")
        (out_dir / "latest.json").write_text(json.dumps({"at": now.isoformat(), "lines": [x.__dict__ for x in lines], "notified": told,
                                                         "waiting": waiting}, indent=1), encoding="utf-8")
        if key:
            with out_dir_history.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rows[-1]) + "\n")
    return lines


def main() -> int:
    from dotenv import load_dotenv

    ap = argparse.ArgumentParser(description="Compare real spend with the estimates")
    ap.add_argument("--save", action="store_true", help="write data/cost_watch/ (the archive task does)")
    ap.add_argument("--notify", action="store_true", help="show a Windows notification for new warnings")
    ap.add_argument("--test-notification", action="store_true")
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")
    if args.test_notification:
        ok = toast("Forecast bot: cost watch", "Test notification. If you see this, cost warnings will reach you.")
        print("notification shown" if ok else "notification could NOT be shown")
        return 0 if ok else 1
    lines = run(args.save, args.notify)
    print(report(lines, datetime.now(timezone.utc)))
    return 1 if Counter(x.status for x in lines)["WARN"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
