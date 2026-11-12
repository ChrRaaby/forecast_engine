"""B-94: served-model and host change alerts from archived records. Offline; synthetic only."""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _tool():
    spec = importlib.util.spec_from_file_location("check_served_models", ROOT / "tools" / "check_served_models.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_served_models"] = mod  # its dataclass looks the module up here
    spec.loader.exec_module(mod)
    return mod


def _rec(runs, run, qid, day, calls):
    d = runs / "1" / "data" / "forecasts" / run
    d.mkdir(parents=True, exist_ok=True)
    rec = {"as_of": f"{day}T10:00:00+00:00", "question": {"question_id": qid},
           "forecasters": [{"model": m, "call": {"model": m, "output": "x", "extra": extra}, "check": None} for m, extra in calls],
           "research": {"calls": [{"model": "gemini-3.5-flash-lite", "provider": "gemini", "output": "brief",
                                   "extra": {"model_version": "gemini-3.5-flash-lite-001"}}]}}
    (d / f"q{qid}.json").write_text(json.dumps(rec), encoding="utf-8")


def _archive(tmp_path, late_sonnet_host="Claude Platform on AWS", late_sol="openai/gpt-6.1-sol"):
    runs = tmp_path / "runs"
    for i, day in enumerate(("2026-11-01", "2026-11-02")):
        _rec(runs, f"2026110{i + 1}T1000Z-tournament", 100 + i, day, [
            ("openai/gpt-6.1-sol", {"served_model": "openai/gpt-6.1-sol", "upstream_provider": "OpenAI"}),
            ("anthropic/claude-sonnet-5.5", {"served_model": "anthropic/claude-sonnet-5.5", "upstream_provider": "Claude Platform on AWS"}),
            ("deepseek/deepseek-v4.1-flash", {"served_model": "deepseek/deepseek-v4.1-flash", "upstream_provider": ["Wafer", "Alibaba"][i]}),
        ])
    _rec(runs, "20261110T1000Z-tournament", 200, "2026-11-10", [
        ("openai/gpt-6.1-sol", {"served_model": late_sol, "upstream_provider": "OpenAI"}),
        ("anthropic/claude-sonnet-5.5", {"served_model": "anthropic/claude-sonnet-5.5", "upstream_provider": late_sonnet_host}),
        ("deepseek/deepseek-v4.1-flash", {"served_model": "deepseek/deepseek-v4.1-flash", "upstream_provider": "Together"}),
        ("openai/gpt-5-mini", {"served_model": "openai/gpt-5-mini", "upstream_provider": "OpenAI"}),
    ])
    _rec(runs, "20261001T1000Z-test_questions", 300, "2026-11-10", [("openai/gpt-6.1-sol", {"served_model": "test-only"})])
    return runs


def test_quiet_week_has_only_info(tmp_path, capsys):
    t = _tool()
    runs = _archive(tmp_path)
    assert t.main(["--runs", str(runs), "--since", "2026-11-05"]) == 0
    out = capsys.readouterr().out
    assert "WARNING" not in out and "test-only" not in out
    assert "deepseek/deepseek-v4.1-flash: new hosts in its host lottery (2 hosts before): Together" in out
    assert "openai/gpt-5-mini: first record" in out  # a new model is not a change of an existing one


def test_new_served_name_and_new_host_of_a_single_host_model_warn(tmp_path, capsys):
    t = _tool()
    runs = _archive(tmp_path, late_sonnet_host="Anthropic", late_sol="openai/gpt-6.1-sol-20261110")
    eras = tmp_path / "eras.json"
    assert t.main(["--runs", str(runs), "--since", "2026-11-05", "--eras", str(eras)]) == 1
    out = capsys.readouterr().out
    assert "new served name/version **openai/gpt-6.1-sol-20261110** from 2026-11-10" in out
    assert "anthropic/claude-sonnet-5.5: new host **Anthropic** from 2026-11-10, 1 calls (before: only Claude Platform on AWS)" in out
    e = json.loads(eras.read_text(encoding="utf-8"))
    assert [x["value"] for x in e["openai/gpt-6.1-sol"]["name"]] == ["openai/gpt-6.1-sol", "openai/gpt-6.1-sol-20261110"]
    assert e["gemini-3.5-flash-lite"]["name"][0] == {"value": "gemini-3.5-flash-lite-001", "first": "2026-11-01",
                                                     "last": "2026-11-10", "n": 3}
