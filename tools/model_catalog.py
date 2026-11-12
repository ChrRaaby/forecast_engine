"""Model catalogue (B-96): an up-to-date picture of the models we could use, from the providers' own machine lists.

    poetry run python tools/model_catalog.py                               # print the catalogue (Markdown), save a snapshot
    poetry run python tools/model_catalog.py --out catalogue.md --json catalogue.json
    poetry run python tools/model_catalog.py --models-json or.json --no-save   # offline, from a saved OpenRouter list

Why: a pricing-page read missed Gemini 3.8 Flash (listed on OpenRouter since 2026-09-02) and Gemini 4 Argon / Carbon /
Hydrogen were found only when Christian asked. Pricing pages leave models out; the machine lists don't.

What it prints (nothing is changed in the bot):
1. **Our models** - every model id the configs use (frontier live roster, cheap shadow, other shadows and experiment arms,
   helpers), with role, OpenRouter listing date, price and the cost of one typical forecast call.
2. **Per vendor**, the text models listed since our oldest model from that vendor (at least 120 days back) (newest first, at most --per-vendor):
   price, listing date, and the cost of one typical call as a multiple of the vendor's model in the frontier roster.
   Vendors we don't use yet (WATCH_VENDORS) are listed too.
3. **Changes since the last snapshot**: new models from watched vendors, price changes, models gone from the list.
4. **Warnings**: one of our models missing from the list or carrying a retirement date; known future price changes
   (KNOWN_PRICE_CHANGES, kept by hand) within --horizon days.

A snapshot of the watched vendors' models is saved to --snapshot-dir (default data/model_catalog/, the private data repo)
as YYYY-MM-DD.json; the next run diffs against the newest older snapshot. Uses only the free OpenRouter list endpoint and,
if GEMINI_API_KEY is set, Google's model list (no dates there, so only new names are reported).

Exit code 1 when there is a WARNING about one of our own models (missing, or retiring within --horizon days), else 0.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OPENROUTER_URL = "https://openrouter.ai/api/v1/models"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models"

# Vendors listed even where no config uses them yet (OpenRouter id prefixes).
WATCH_VENDORS = ("openai", "anthropic", "google", "x-ai", "qwen", "deepseek", "moonshotai", "z-ai", "mistralai", "meta-llama")

# One typical forecast call, for comparing prices as money rather than per-token rates. Token counts are the
# FORECAST assumption in tools/cost_model.py (12k in, 6k out incl. reasoning); real counts vary by model.
CALL_TOKENS_IN = 12_000
CALL_TOKENS_OUT = 6_000

# Ids that are the same model under another billing route, or not a general text model.
SKIP_SUFFIXES = (":batch", ":free", ":floor", ":nitro", ":extended", ":online", ":thinking")
SKIP_WORDS = ("embed", "tts", "audio", "image", "vision", "nano-banana", "omni", "guard", "moderation", "legal")

# Price changes announced but not yet in the lists. Kept by hand; each with its source and the day it was read.
# (model id, effective date, note)
KNOWN_PRICE_CHANGES: tuple[tuple[str, str, str], ...] = (
    ("google/gemini-3.8-flash", "2027-01-01",
     "Google: $0.75/$3.75 until 2026-12-31, then double (ai.google.dev, read 2026-11-12; tools/exp013_armc_pilot.py)"),
    ("google/gemini-4-argon", "2026-11-30",
     "Google: intro $2/$10, then $4/$20 (blog.google Argon post); end date 2026-11-30 from a third-party summary "
     "(teamorouter.com, read 2026-11-12), not confirmed by Google"),
    ("google/gemini-4-carbon", "2026-11-30",
     "Intro $2/$10, then $4/$20; end date from a third-party summary (teamorouter.com, read 2026-11-12), not confirmed"),
)


@dataclass
class Model:
    id: str
    name: str
    listed: date
    usd_in: float  # per 1M tokens
    usd_out: float
    expires: str | None = None

    @property
    def vendor(self) -> str:
        return self.id.split("/", 1)[0]

    @property
    def call_usd(self) -> float:
        return (CALL_TOKENS_IN * self.usd_in + CALL_TOKENS_OUT * self.usd_out) / 1e6


@dataclass
class Catalogue:
    models: dict[str, Model]
    ours: dict[str, list[str]]  # model id -> roles
    frontier: list[str]
    previous: dict | None = None
    previous_date: str | None = None
    gemini_names: list[str] | None = None
    warnings: list[str] = field(default_factory=list)


def parse_openrouter(data: dict) -> dict[str, Model]:
    """OpenRouter /models JSON -> {id: Model}, text-output models only, billing variants dropped."""
    out: dict[str, Model] = {}
    for m in data.get("data", []):
        mid = m.get("id", "")
        if not mid or mid.endswith(SKIP_SUFFIXES) or ":" in mid.split("/", 1)[-1]:
            continue
        if m.get("architecture", {}).get("output_modalities", ["text"]) != ["text"]:
            continue
        p = m.get("pricing", {})
        try:
            usd_in, usd_out = float(p.get("prompt", 0)) * 1e6, float(p.get("completion", 0)) * 1e6
        except (TypeError, ValueError):
            continue
        if usd_in < 0 or usd_out < 0:  # "-1" marks a router, not a model
            continue
        out[mid] = Model(mid, m.get("name", mid), datetime.fromtimestamp(m.get("created", 0), tz=timezone.utc).date(),
                         round(usd_in, 4), round(usd_out, 4), m.get("expiration_date") or None)
    return out


def our_models() -> tuple[dict[str, list[str]], list[str]]:
    """Every model id the configs use -> its roles; plus the frontier roster in order. Gemini-direct ids get 'google/'."""
    sys.path.insert(0, str(ROOT))
    from forecast_engine import config as cfg  # noqa: E402
    from evals.leakage_screen import ScreenConfig  # noqa: E402

    roles: dict[str, list[str]] = {}

    def add(mid: str | None, role: str) -> None:
        if mid and role not in roles.setdefault(mid, []):
            roles[mid].append(role)

    frontier = [s.model for s in cfg.TOURNAMENT_CONFIG.forecasters]
    for m in frontier:
        add(m, "frontier roster (live)")
    for s in cfg.DEFAULT_CONFIG.forecasters:
        add(s.model, "cheap shadow")
    add(cfg.DEFAULT_CONFIG.polarity_check_model, "polarity check (live)")
    add("google/" + cfg.DEFAULT_CONFIG.research.gemini_model, "research writer (live)")
    # Shadows, experiment arms (also ones built but switched off), and every *_model field inside their research settings.
    arm_sets = {"shadow": cfg.SHADOW_CONFIGS, "EXP-008": getattr(cfg, "EXP008_ARMS", {}),
                "EXP-013": getattr(cfg, "EXP013_ARMS", {})}
    for label, arms in arm_sets.items():
        for name, c in arms.items():
            for s in c.forecasters:
                if s.model not in roles:
                    add(s.model, f"{label} {name}")
            for key, value in _model_fields(c.research):
                gemini_direct = key in ("gemini_model", "brief_writer.model")
                add(("google/" + value) if gemini_direct else value, f"{label} {ROLE_NAMES.get(key, key)}")
    add(ScreenConfig().model, "leakage screen (must predate questions)")
    return roles, frontier


ROLE_NAMES = {"gemini_model": "research writer", "followup.critic_model": "follow-up critic",
              "source_block.extractor_model": "source-block extractor", "brief_writer.model": "brief writer",
              "news.keyword_model": "news keywords"}


def _model_fields(obj, prefix: str = "") -> list[tuple[str, str]]:
    """(dotted field name, value) for every string field named model / *_model inside a dataclass, recursively."""
    from dataclasses import fields, is_dataclass

    out: list[tuple[str, str]] = []
    if not is_dataclass(obj):
        return out
    for f in fields(obj):
        v = getattr(obj, f.name)
        key = prefix + f.name
        if isinstance(v, str) and (f.name == "model" or f.name.endswith("_model")):
            out.append((key, v))
        elif is_dataclass(v):
            out += _model_fields(v, key + ".")
    return out


def load_previous(snapshot_dir: Path, today: date) -> tuple[dict | None, str | None]:
    if not snapshot_dir.is_dir():
        return None, None
    older = sorted(p for p in snapshot_dir.glob("*.json") if p.stem < today.isoformat())
    if not older:
        return None, None
    return json.loads(older[-1].read_text(encoding="utf-8")), older[-1].stem


def snapshot(cat: Catalogue) -> dict:
    return {"models": {m.id: {"listed": m.listed.isoformat(), "in": m.usd_in, "out": m.usd_out, "expires": m.expires}
                       for m in cat.models.values() if m.vendor in watched_vendors(cat)},
            "gemini_names": cat.gemini_names}


def watched_vendors(cat: Catalogue) -> list[str]:
    ours = [mid.split("/", 1)[0] for mid in cat.ours]
    return list(dict.fromkeys([*ours, *WATCH_VENDORS]))


def is_general(mid: str) -> bool:
    return not any(w in mid.split("/", 1)[-1] for w in SKIP_WORDS)


def money(x: float) -> str:
    return f"${x:.3f}" if x < 0.1 else f"${x:.2f}"


def rate(m: Model) -> str:
    return f"${m.usd_in:g} / ${m.usd_out:g}"


def render(cat: Catalogue, today: date, per_vendor: int, horizon: int) -> str:
    L: list[str] = [f"# Model catalogue, {today.isoformat()}", "",
                    f"Source: OpenRouter model list ({len(cat.models)} text models). Prices in USD per 1M tokens in / out. "
                    f"\"Call\" = one typical forecast call ({CALL_TOKENS_IN // 1000}k in, {CALL_TOKENS_OUT // 1000}k out tokens; "
                    "real token use differs by model, reasoning models often use more).", ""]

    # 1. Our models
    L += ["## Our models", "", "| Model | Role | Listed | Price | Call |", "|---|---|---|---|---|"]
    for mid, roles in cat.ours.items():
        m = cat.models.get(mid)
        if m is None:
            note = "not on OpenRouter (Gemini direct)" if mid.startswith("google/") and "research" in " ".join(roles) else "MISSING"
            L.append(f"| {mid} | {', '.join(roles)} | {note} | | |")
            if note == "MISSING":
                cat.warnings.append(f"WARNING  {mid} ({', '.join(roles)}) is not in OpenRouter's list")
            continue
        L.append(f"| {mid} | {', '.join(roles)} | {m.listed} | {rate(m)} | {money(m.call_usd)} |")
        if m.expires:
            days = (date.fromisoformat(m.expires[:10]) - today).days
            level = "WARNING " if days <= horizon else "info    "
            cat.warnings.append(f"{level} {mid} ({', '.join(roles)}) retires on {m.expires[:10]} (in {days} days)")
    L.append("")

    # 2. Per vendor
    L += ["## Newer models per vendor", "",
          "Text models listed since our oldest model from that vendor (at least the last 120 days), newest first. \"vs ours\" compares one call with the "
          "vendor's model in the frontier roster. **Bold** = in use.", ""]
    frontier_by_vendor = {mid.split("/", 1)[0]: cat.models[mid] for mid in cat.frontier if mid in cat.models}
    for vendor in watched_vendors(cat):
        ours_here = [cat.models[mid] for mid in cat.ours if mid in cat.models and cat.models[mid].vendor == vendor]
        # From our oldest model of this vendor, but never less than 120 days back, so a stronger model listed a few days
        # before ours (e.g. Opus 5.5 a week before Sonnet 5.5) still shows.
        since = min([m.listed for m in ours_here] + [today - timedelta(days=120)])
        rows = sorted((m for m in cat.models.values() if m.vendor == vendor and m.listed >= since
                       and (is_general(m.id) or m.id in cat.ours)), key=lambda m: (m.listed, m.id), reverse=True)
        if not rows:
            continue
        shown = rows[:per_vendor] + [m for m in rows[per_vendor:] if m.id in cat.ours]
        ref = frontier_by_vendor.get(vendor)
        head = f"### {vendor}" + (f" (frontier slot: {ref.id})" if ref else " (not in the frontier roster)" if ours_here
                                  else " (not used yet)")
        L += [head, "", "| Model | Listed | Price | Call | vs ours |", "|---|---|---|---|---|"]
        for m in shown:
            name = f"**{m.id}**" if m.id in cat.ours else m.id
            vs = f"x{m.call_usd / ref.call_usd:.2g}" if ref and ref.call_usd > 0 else ""
            L.append(f"| {name} | {m.listed} | {rate(m)} | {money(m.call_usd)} | {vs} |")
        if len(rows) > per_vendor:
            L.append(f"| ... {len(rows) - per_vendor} more since {since} | | | | |")
        L.append("")

    # 3. Changes since the last snapshot
    L += ["## Changes since the last snapshot", ""]
    if cat.previous is None:
        L += ["No earlier snapshot; this run is the baseline.", ""]
    else:
        prev = cat.previous.get("models", {})
        vendors = set(watched_vendors(cat))
        now = {mid: m for mid, m in cat.models.items() if m.vendor in vendors}
        new = sorted((m for mid, m in now.items() if mid not in prev), key=lambda m: m.listed, reverse=True)
        gone = sorted(mid for mid in prev if mid not in now)
        moved = [(m, prev[mid]) for mid, m in now.items() if mid in prev
                 and (abs(prev[mid]["in"] - m.usd_in) > 1e-9 or abs(prev[mid]["out"] - m.usd_out) > 1e-9)]
        L.append(f"Compared with {cat.previous_date}.")
        L.append("")
        L += [f"- NEW  {m.id} (listed {m.listed}, {rate(m)})" for m in new] or ["- No new models."]
        for m, p in moved:
            L.append(f"- PRICE  {m.id}: ${p['in']:g} / ${p['out']:g} -> {rate(m)}" + ("  (ours)" if m.id in cat.ours else ""))
            if m.id in cat.ours:
                cat.warnings.append(f"info     price change on our model {m.id}: ${p['in']:g}/${p['out']:g} -> {rate(m)}")
        for mid in gone:
            L.append(f"- GONE  {mid}" + ("  (ours!)" if mid in cat.ours else ""))
        if cat.gemini_names is not None and cat.previous.get("gemini_names") is not None:
            new_g = sorted(set(cat.gemini_names) - set(cat.previous["gemini_names"]))
            L += [f"- NEW on Google's own list  {n}" for n in new_g]
        L.append("")

    # 4. Known future price changes
    soon = [(mid, d, note) for mid, d, note in KNOWN_PRICE_CHANGES if 0 <= (date.fromisoformat(d) - today).days <= horizon]
    if soon:
        L += ["## Announced price changes (kept by hand)", ""]
        for mid, d, note in soon:
            tag = " (ours)" if mid in cat.ours else ""
            L.append(f"- {d}  {mid}{tag}: {note}")
            if mid in cat.ours:
                cat.warnings.append(f"info     {mid} price changes on {d}")
        L.append("")

    if cat.warnings:
        L += ["## Warnings", ""] + [f"- {w}" for w in cat.warnings] + [""]
    return "\n".join(L)


def fetch_gemini_names() -> list[str] | None:
    key = os.getenv("GEMINI_API_KEY")
    if not key or key == "REPLACE_ME":
        return None
    import requests
    resp = requests.get(GEMINI_URL, headers={"x-goog-api-key": key}, params={"pageSize": 1000}, timeout=30)
    resp.raise_for_status()
    return sorted(m["name"].removeprefix("models/") for m in resp.json().get("models", []))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--models-json", type=Path, help="saved OpenRouter /models JSON instead of fetching (offline)")
    ap.add_argument("--snapshot-dir", type=Path, default=ROOT / "data" / "model_catalog")
    ap.add_argument("--no-save", action="store_true", help="don't write today's snapshot")
    ap.add_argument("--out", type=Path, help="write the Markdown here as well as printing it")
    ap.add_argument("--json", type=Path, help="write today's snapshot JSON here too (e.g. for a published page)")
    ap.add_argument("--per-vendor", type=int, default=10)
    ap.add_argument("--horizon", type=int, default=90, help="days ahead for retirements and announced price changes")
    ap.add_argument("--today", type=date.fromisoformat, default=None, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    today = args.today or datetime.now(timezone.utc).date()

    if args.models_json:
        raw = json.loads(args.models_json.read_text(encoding="utf-8"))
    else:
        import requests
        raw = requests.get(OPENROUTER_URL, timeout=30).json()
    ours, frontier = our_models()
    previous, previous_date = load_previous(args.snapshot_dir, today)
    cat = Catalogue(parse_openrouter(raw), ours, frontier, previous, previous_date,
                    None if args.models_json else fetch_gemini_names())
    text = render(cat, today, args.per_vendor, args.horizon)
    sys.stdout.reconfigure(encoding="utf-8")
    print(text)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    snap = snapshot(cat)
    if args.json:
        args.json.write_text(json.dumps(snap, indent=1), encoding="utf-8")
    if not args.no_save:
        args.snapshot_dir.mkdir(parents=True, exist_ok=True)
        (args.snapshot_dir / f"{today.isoformat()}.json").write_text(json.dumps(snap, indent=1), encoding="utf-8")
    return 1 if any(w.startswith("WARNING") for w in cat.warnings) else 0


if __name__ == "__main__":
    sys.exit(main())
