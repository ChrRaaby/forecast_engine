"""Check every model id in forecast_engine/config.py against the providers' live model lists (ADR-0003).

    poetry run python tools/check_models.py

Prints each OpenRouter forecaster with its current price, and checks the Gemini research model if GEMINI_API_KEY is set.
Exits non-zero if any id is unknown. Uses only free list endpoints.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from forecast_engine.config import DEFAULT_CONFIG  # noqa: E402


def main() -> int:
    load_dotenv()
    ok = True
    models = {m["id"]: m for m in requests.get("https://openrouter.ai/api/v1/models", timeout=30).json()["data"]}
    for spec in DEFAULT_CONFIG.forecasters:
        m = models.get(spec.model)
        if m is None:
            print(f"MISSING  openrouter  {spec.model}")
            ok = False
            continue
        p = m["pricing"]
        print(f"ok       openrouter  {spec.model:40s} in ${float(p['prompt']) * 1e6:.3f}/M  out ${float(p['completion']) * 1e6:.3f}/M")

    gemini = DEFAULT_CONFIG.research.gemini_model
    key = os.getenv("GEMINI_API_KEY")
    if not key or key == "REPLACE_ME":
        print(f"skipped  gemini      {gemini} (GEMINI_API_KEY not set)")
    else:
        resp = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            headers={"x-goog-api-key": key},
            params={"pageSize": 1000},
            timeout=30,
        )
        resp.raise_for_status()
        names = {m["name"].removeprefix("models/") for m in resp.json().get("models", [])}
        if gemini in names:
            print(f"ok       gemini      {gemini}")
        else:
            flash = sorted(n for n in names if "flash" in n)
            print(f"MISSING  gemini      {gemini}; available flash models: {flash}")
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
