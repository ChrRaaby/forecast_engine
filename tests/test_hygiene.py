"""Static checks for rules that are easy to break silently."""
import re
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "forecast_engine"


def test_only_clock_reads_system_time():
    offenders = []
    for path in PKG.glob("*.py"):
        if path.name == "clock.py":
            continue
        src = path.read_text(encoding="utf-8")
        if re.search(r"datetime\.now\(|datetime\.utcnow\(|date\.today\(|time\.time\(", src):
            offenders.append(path.name)
    assert offenders == [], f"read the Clock instead of the system clock in: {offenders}"


def test_model_ids_live_only_in_config():
    from forecast_engine.config import DEFAULT_CONFIG

    ids = [f.model for f in DEFAULT_CONFIG.forecasters] + [DEFAULT_CONFIG.research.gemini_model]
    for path in list(PKG.glob("*.py")) + [PKG.parent / "main.py"]:
        if path.name == "config.py":
            continue
        src = path.read_text(encoding="utf-8")
        for model_id in ids:
            assert model_id not in src, f"{model_id} hard-coded in {path.name}; keep model ids in config.py"
