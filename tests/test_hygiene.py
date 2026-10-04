"""Static checks for rules that are easy to break silently."""
import re
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "forecast_engine"
EVALS = PKG.parent / "evals"


def test_only_clock_reads_system_time():
    offenders = []
    for path in PKG.glob("*.py"):
        if path.name == "clock.py":
            continue
        src = path.read_text(encoding="utf-8")
        if re.search(r"datetime\.now\(|datetime\.utcnow\(|date\.today\(|time\.time\(", src):
            offenders.append(path.name)
    assert offenders == [], f"read the Clock instead of the system clock in: {offenders}"


def test_evals_read_time_only_through_a_clock():
    """Backtests must never see the real date by accident (protocol T5, §7); evals code uses FixedClock or SystemClock."""
    offenders = [
        p.name for p in EVALS.glob("*.py")
        if re.search(r"datetime\.now\(|datetime\.utcnow\(|date\.today\(|time\.time\(", p.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"read time through a Clock in: {offenders}"


def test_evals_never_import_the_live_adapter():
    """ADR-0005: the harness imports the core, never main.py or the template adapter."""
    for p in EVALS.glob("*.py"):
        src = p.read_text(encoding="utf-8")
        assert not re.search(r"^\s*(from|import)\s+(main|bot_helpers|forecasting_tools)\b", src, re.M), p.name


def test_model_ids_live_only_in_config():
    from forecast_engine.config import DEFAULT_CONFIG

    ids = [f.model for f in DEFAULT_CONFIG.forecasters] + [DEFAULT_CONFIG.research.gemini_model]
    for path in list(PKG.glob("*.py")) + [PKG.parent / "main.py"]:
        if path.name == "config.py":
            continue
        src = path.read_text(encoding="utf-8")
        for model_id in ids:
            assert model_id not in src, f"{model_id} hard-coded in {path.name}; keep model ids in config.py"


def test_entry_points_compile_on_the_ci_python():
    """2026-10-03: an f-string valid on Python 3.12 (PC) but not 3.11 (GitHub Actions) broke every live run for ~10 hours.
    CI runs the tests on 3.11, so compiling the entry points here catches that class of error before it reaches the bot."""
    import py_compile

    root = PKG.parent
    for path in [root / "main.py", root / "bot_helpers.py", *root.glob("tools/*.py")]:
        py_compile.compile(str(path), doraise=True)
