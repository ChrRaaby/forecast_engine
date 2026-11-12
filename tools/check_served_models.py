"""Served-model and host change alerts (B-94): did a live model start answering under a new name, or from a new host?

    poetry run python tools/check_served_models.py                  # last 7 days vs everything before, from data/runs/
    poetry run python tools/check_served_models.py --since 2026-11-01 --eras data/outcomes/model_eras.json

Every model call in a live or shadow record stores what OpenRouter (or Google, for grounded search) said it served:
`served_model` (OpenRouter's model name), `upstream_provider` (the host OpenRouter routed the call to; recorded since PR #21,
2026-11-03) and, for Gemini research calls, `model_version`. A vendor that swaps the weights behind the same name, or a route
to another host, can shift scores silently; before/after comparisons of live scores are then not like for like.

Alerts (printed as Markdown, for the weekly report; nothing is changed):
- WARNING  a served name or Gemini model version seen for the first time on or after --since, for a model that had another
           one before;
- WARNING  a new host for a model that had exactly one host before --since (e.g. a closed model now served by a second
           cloud);
- info     a new host for a model that already had several (an open-weight model's normal host lottery), and a model or
           host recorded for the first time (one line per model).
The eras file lists, per model, every served name / version / host with its first and last day and count: a new era starts
where a new served name or version first appears. Exit code 1 when there is a WARNING, else 0.

Limits: OpenRouter mostly returns the requested name itself (e.g. "openai/gpt-6.1-sol"), so a vendor's silent update behind
an unchanged name is NOT visible here; only renamed snapshots, host routes and Gemini's own version string are.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "data" / "runs"


@dataclass(frozen=True)
class Obs:
    day: str
    model: str
    kind: str  # "name" (served model / model version) or "host"
    value: str


def _calls(rec: dict[str, Any]):
    for f in rec.get("forecasters") or ():
        yield f.get("call") or {}
    for c in (rec.get("research") or {}).get("calls") or ():
        yield c
    for f in rec.get("forecasters") or ():  # polarity audits, when stored with the member
        if isinstance(f.get("check"), dict) and isinstance(f["check"].get("call"), dict):
            yield f["check"]["call"]


def observations(runs_dir: Path) -> list[Obs]:
    out: list[Obs] = []
    for path in sorted(runs_dir.rglob("q*.json")):
        if "test_questions" in path.parent.name:
            continue
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        day = str(rec.get("as_of") or "")[:10]
        if not day:
            continue
        for call in _calls(rec):
            if call.get("error") and not call.get("output"):
                continue
            model = str(call.get("model") or "")
            extra = call.get("extra") or {}
            if not model or call.get("provider") == "asknews":
                continue
            served = extra.get("served_model") or extra.get("model_version")
            if served:
                out.append(Obs(day, model, "name", str(served)))
            if extra.get("upstream_provider"):
                out.append(Obs(day, model, "host", str(extra["upstream_provider"])))
    return out


def eras(obs: list[Obs]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    acc: dict[tuple[str, str, str], list] = {}
    for o in obs:
        e = acc.setdefault((o.model, o.kind, o.value), [o.day, o.day, 0])
        e[0], e[1], e[2] = min(e[0], o.day), max(e[1], o.day), e[2] + 1
    out: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: {"name": [], "host": []})
    for (model, kind, value), (first, last, n) in sorted(acc.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[1][0], kv[0][2])):
        out[model][kind].append({"value": value, "first": first, "last": last, "n": n})
    return dict(out)


def alerts(obs: list[Obs], since: date) -> list[tuple[str, str]]:
    """[(level, text)], level WARNING or info."""
    s = since.isoformat()
    before: dict[tuple[str, str], set[str]] = defaultdict(set)
    first_new: dict[tuple[str, str, str], tuple[str, int]] = {}
    for o in obs:
        if o.day < s:
            before[(o.model, o.kind)].add(o.value)
    for o in obs:
        if o.day >= s and o.value not in before[(o.model, o.kind)]:
            day, n = first_new.get((o.model, o.kind, o.value), (o.day, 0))
            first_new[(o.model, o.kind, o.value)] = (min(day, o.day), n + 1)
    out: list[tuple[str, str]] = []
    grouped: dict[tuple[str, str], list[str]] = defaultdict(list)
    for (model, kind, value), (day, n) in sorted(first_new.items(), key=lambda kv: (kv[1][0], kv[0])):
        old = sorted(before[(model, kind)])
        if kind == "name" and old:
            out.append(("WARNING", f"{model}: new served name/version **{value}** from {day}, {n} calls (before: {', '.join(old)})"))
        elif kind == "name":
            grouped[(model, "first record of its served name/version (new model, or not recorded before)")].append(
                f"{value} from {day}")
        elif len(old) == 1:
            out.append(("WARNING", f"{model}: new host **{value}** from {day}, {n} calls (before: only {old[0]})"))
        elif not old:
            grouped[(model, "hosts recorded for the first time")].append(f"{value} ({n})")
        else:
            grouped[(model, f"new hosts in its host lottery ({len(old)} hosts before)")].append(f"{value} from {day} ({n})")
    for (model, what), values in sorted(grouped.items()):
        out.append(("info", f"{model}: {what}: {', '.join(values)}"))
    return out


def render(obs: list[Obs], since: date) -> tuple[str, bool]:
    al = alerts(obs, since)
    warn = any(level == "WARNING" for level, _ in al)
    lines = [f"### Served models and hosts (B-94), new since {since}", ""]
    if not al:
        lines.append("No new served name, model version or host. Live scores before and after are like for like on this count.")
    for level, text in al:
        lines.append(f"- {'⚠️ ' if level == 'WARNING' else ''}{level}: {text}")
    if warn:
        lines += ["", "A WARNING marks a possible new era: compare live scores before/after it per era, not pooled, and say so "
                      "in any report that spans it."]
    return "\n".join(lines) + "\n", warn


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=Path, default=RUNS, help="folder of archived runs (default data/runs)")
    ap.add_argument("--since", type=date.fromisoformat, default=None, help="new = first seen on/after this day (default: 7 days ago)")
    ap.add_argument("--eras", type=Path, default=None, help="also write every served name/host with first/last day here (JSON)")
    args = ap.parse_args(argv)
    since = args.since or (datetime.now(timezone.utc).date() - timedelta(days=7))
    obs = observations(args.runs)
    text, warn = render(obs, since)
    sys.stdout.write(text)
    if args.eras:
        args.eras.write_text(json.dumps(eras(obs), indent=1) + "\n", encoding="utf-8")
    return 1 if warn else 0


if __name__ == "__main__":
    raise SystemExit(main())
