"""B-96: model catalogue from the OpenRouter model list. Offline; synthetic lists only."""
import importlib.util
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _tool():
    spec = importlib.util.spec_from_file_location("model_catalog", ROOT / "tools" / "model_catalog.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["model_catalog"] = mod  # its dataclasses look the module up here
    spec.loader.exec_module(mod)
    return mod


def _m(mid, day, usd_in, usd_out, out=("text",), expires=None):
    ts = int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp())
    return {"id": mid, "name": mid, "created": ts, "architecture": {"output_modalities": list(out)},
            "pricing": {"prompt": str(usd_in / 1e6), "completion": str(usd_out / 1e6)}, "expiration_date": expires}


LIST = {"data": [
    _m("google/gemini-3.1-pro-preview", "2026-02-19", 2, 12),
    _m("google/gemini-4-carbon", "2026-11-10", 2, 10),
    _m("google/gemini-4-carbon:batch", "2026-11-10", 1, 5),
    _m("google/gemini-nano-banana-2.1", "2026-10-06", 1.5, 7.5),
    _m("google/gemini-image-x", "2026-10-07", 1, 1, out=("image", "text")),
    _m("anthropic/claude-sonnet-5.5", "2026-09-28", 2, 10),
    _m("anthropic/claude-opus-5.5", "2026-09-22", 4, 20),
    _m("openai/gpt-5-mini", "2025-08-07", 0.25, 2, expires="2026-12-11"),
    _m("moonshotai/kimi-k3.1-pro", "2026-10-23", 0.25, 8),
    _m("openrouter/auto", "2025-01-01", -1, -1),
]}
OURS = {"google/gemini-3.1-pro-preview": ["frontier roster (live)"], "anthropic/claude-sonnet-5.5": ["frontier roster (live)"],
        "openai/gpt-5-mini": ["polarity check (live)"]}
FRONTIER = ["google/gemini-3.1-pro-preview", "anthropic/claude-sonnet-5.5"]
TODAY = date(2026, 11, 12)


def _cat(mod, previous=None):
    return mod.Catalogue(mod.parse_openrouter(LIST), dict(OURS), list(FRONTIER), previous, "2026-11-05" if previous else None)


def test_parse_drops_variants_non_text_and_routers():
    mod = _tool()
    models = mod.parse_openrouter(LIST)
    assert "google/gemini-4-carbon" in models and "google/gemini-4-carbon:batch" not in models
    assert "google/gemini-image-x" not in models and "openrouter/auto" not in models
    carbon = models["google/gemini-4-carbon"]
    assert (carbon.usd_in, carbon.usd_out, carbon.listed) == (2, 10, date(2026, 11, 10))
    assert abs(carbon.call_usd - (12_000 * 2 + 6_000 * 10) / 1e6) < 1e-12


def test_render_lists_newer_models_per_vendor_with_cost_ratio():
    mod = _tool()
    text = mod.render(_cat(mod), TODAY, per_vendor=10, horizon=90)
    assert "### google (frontier slot: google/gemini-3.1-pro-preview)" in text
    assert "| google/gemini-4-carbon | 2026-11-10 | $2 / $10 | $0.084 | x0.88 |" in text
    assert "nano-banana" not in text  # image model filtered by name
    # Opus 5.5 was listed six days before our Sonnet 5.5: still shown (120-day floor), at twice the cost per call.
    assert "| anthropic/claude-opus-5.5 | 2026-09-22 | $4 / $20 | $0.17 | x2 |" in text
    assert "### moonshotai (not used yet)" in text
    assert "No earlier snapshot" in text


def test_retirement_of_our_model_is_a_warning():
    mod = _tool()
    cat = _cat(mod)
    mod.render(cat, TODAY, per_vendor=10, horizon=90)
    assert any(w.startswith("WARNING") and "openai/gpt-5-mini" in w and "2026-12-11" in w for w in cat.warnings)


def test_missing_model_is_a_warning_but_gemini_direct_is_not():
    mod = _tool()
    cat = _cat(mod)
    cat.ours["x-ai/grok-9"] = ["frontier roster (live)"]
    cat.ours["google/gemini-direct-only"] = ["research writer (live)"]
    mod.render(cat, TODAY, per_vendor=10, horizon=90)
    assert any("x-ai/grok-9" in w and w.startswith("WARNING") for w in cat.warnings)
    assert not any("gemini-direct-only" in w for w in cat.warnings)


def test_snapshot_diff_reports_new_price_change_and_gone():
    mod = _tool()
    prev = {"models": {"google/gemini-3.1-pro-preview": {"listed": "2026-02-19", "in": 2, "out": 10, "expires": None},
                       "anthropic/claude-sonnet-5.5": {"listed": "2026-09-28", "in": 2, "out": 10, "expires": None},
                       "openai/gpt-5-mini": {"listed": "2025-08-07", "in": 0.25, "out": 2, "expires": None},
                       "anthropic/claude-opus-5.5": {"listed": "2026-09-22", "in": 4, "out": 20, "expires": None},
                       "moonshotai/kimi-k3.1-pro": {"listed": "2026-10-23", "in": 0.25, "out": 8, "expires": None},
                       "google/gemini-nano-banana-2.1": {"listed": "2026-10-06", "in": 1.5, "out": 7.5, "expires": None},
                       "x-ai/grok-old": {"listed": "2026-01-01", "in": 2, "out": 6, "expires": None}}}
    cat = _cat(mod, prev)
    text = mod.render(cat, TODAY, per_vendor=10, horizon=90)
    assert "- NEW  google/gemini-4-carbon (listed 2026-11-10, $2 / $10)" in text
    assert "- PRICE  google/gemini-3.1-pro-preview: $2 / $10 -> $2 / $12  (ours)" in text
    assert "- GONE  x-ai/grok-old" in text
    assert any("price change on our model" in w for w in cat.warnings)


def test_known_price_changes_inside_horizon_only():
    mod = _tool()
    text = mod.render(_cat(mod), TODAY, per_vendor=10, horizon=30)
    assert "2026-11-30  google/gemini-4-carbon" in text
    assert "2027-01-01  google/gemini-3.8-flash" not in text  # 50 days ahead, outside a 30-day horizon


def test_main_offline_saves_snapshot_and_diffs_next_run(tmp_path, monkeypatch, capsys):
    mod = _tool()
    monkeypatch.setattr(mod, "our_models", lambda: (dict(OURS), list(FRONTIER)))
    src = tmp_path / "or.json"
    src.write_text(json.dumps(LIST), encoding="utf-8")
    snaps = tmp_path / "snaps"
    code = mod.main(["--models-json", str(src), "--snapshot-dir", str(snaps), "--today", "2026-11-05"])
    assert code == 1  # gpt-5-mini retires within 90 days
    assert (snaps / "2026-11-05.json").exists()
    mod.main(["--models-json", str(src), "--snapshot-dir", str(snaps), "--today", "2026-11-12", "--no-save"])
    out = capsys.readouterr().out
    assert "Compared with 2026-11-05." in out and "No new models." in out
    assert not (snaps / "2026-11-12.json").exists()


def test_our_models_reads_the_real_configs():
    mod = _tool()
    roles, frontier = mod.our_models()
    assert len(frontier) == 5 and all(m in roles for m in frontier)
    assert any("polarity check" in r for r in roles.get("openai/gpt-5-mini", []))
    assert any(r.startswith("research writer") for r in roles["google/gemini-3.5-flash-lite"])
