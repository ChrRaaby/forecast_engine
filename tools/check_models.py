"""Check every model id in forecast_engine/config.py against the providers' live model lists (ADR-0003).

    poetry run python tools/check_models.py

Prints each OpenRouter forecaster and the backtest leakage-screen model (evals/leakage_screen.py) with price and OpenRouter
listing date, and checks the Gemini research and graph-extractor (evals/graph_extract.py) models if GEMINI_API_KEY is set.
Exits non-zero if any id is unknown. Uses only free list endpoints.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from forecast_engine.config import DEFAULT_CONFIG, SHADOW_CONFIGS, TOURNAMENT_CONFIG  # noqa: E402
from evals.graph_extract import ExtractorConfig  # noqa: E402
from evals.leakage_screen import ScreenConfig  # noqa: E402


def main() -> int:
    load_dotenv()
    ok = True
    models = {m["id"]: m for m in requests.get("https://openrouter.ai/api/v1/models", timeout=30).json()["data"]}
    screen = ScreenConfig()
    specs = ([(s.model, "forecaster") for s in DEFAULT_CONFIG.forecasters]
             + [(s.model, "tournament forecaster") for s in TOURNAMENT_CONFIG.forecasters]
             + [(s.model, f"shadow {name}") for name, c in SHADOW_CONFIGS.items() for s in c.forecasters
                if c is not DEFAULT_CONFIG]
             + [(DEFAULT_CONFIG.polarity_check_model, "polarity check"), (screen.model, "leakage screen")])
    for model_id, role in specs:
        m = models.get(model_id)
        if m is None:
            print(f"MISSING  openrouter  {model_id} ({role})")
            ok = False
            continue
        p = m["pricing"]
        listed = datetime.fromtimestamp(m["created"], tz=timezone.utc).date()
        print(f"ok       openrouter  {model_id:40s} in ${float(p['prompt']) * 1e6:.3f}/M  out ${float(p['completion']) * 1e6:.3f}/M"
              f"  listed {listed}  ({role})")
        if role == "leakage screen" and listed > screen.model_release_date + timedelta(days=30):
            print(f"WARNING  OpenRouter listed {model_id} on {listed}, well after the assumed release date "
                  f"{screen.model_release_date}; verify the release date against the vendor before trusting the screen")

    geminis = [(DEFAULT_CONFIG.research.gemini_model, "research"), (ExtractorConfig().model, "graph extractor")]
    key = os.getenv("GEMINI_API_KEY")
    if not key or key == "REPLACE_ME":
        print(f"skipped  gemini      {[g for g, _ in geminis]} (GEMINI_API_KEY not set)")
    else:
        resp = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            headers={"x-goog-api-key": key},
            params={"pageSize": 1000},
            timeout=30,
        )
        resp.raise_for_status()
        names = {m["name"].removeprefix("models/") for m in resp.json().get("models", [])}
        for gemini, role in geminis:
            if gemini in names:
                print(f"ok       gemini      {gemini} ({role})")
            else:
                flash = sorted(n for n in names if "flash" in n)
                print(f"MISSING  gemini      {gemini} ({role}); available flash models: {flash}")
                ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
