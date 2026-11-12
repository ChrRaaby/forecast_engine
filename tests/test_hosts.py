"""B-93: one OpenRouter host per open-weight model in experiment runs. Offline; synthetic only."""
import asyncio
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from evals import hosts
from forecast_engine.llm import OpenRouterClient

ROOT = Path(__file__).resolve().parents[1]
DS = "deepseek/deepseek-v4.1-flash"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


PC = _load("_prompt_check_tests_hosts", ROOT / "tests" / "test_prompt_check.py")
E2 = PC.E2


class FixedClock:
    def now(self):
        return datetime(2026, 11, 12, tzinfo=timezone.utc)


def _client_capturing(pins):
    client = OpenRouterClient(FixedClock(), api_key="test", pinned_hosts=pins)
    seen = []
    resp = SimpleNamespace(id="g", model=DS, model_extra={"provider": "DeepInfra"},
                           choices=[SimpleNamespace(message=SimpleNamespace(content="Probability: 30%"), finish_reason="stop")],
                           usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, model_extra={"cost": 0.001}))

    async def create(**kw):
        seen.append(kw)
        return resp

    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return client, seen


def test_pinned_model_is_sent_with_one_host_and_no_fallbacks():
    client, seen = _client_capturing({DS: "deepinfra"})
    call = asyncio.run(client.complete(model=DS, prompt="p", purpose="forecast", max_tokens=10))
    assert seen[0]["extra_body"]["provider"] == {"order": ["deepinfra"], "allow_fallbacks": False}
    assert call.extra["pinned_host"] == "deepinfra" and call.extra["upstream_provider"] == "DeepInfra"
    call = asyncio.run(client.complete(model="openai/gpt-6-luna", prompt="p", purpose="forecast", max_tokens=10))
    assert "provider" not in seen[1]["extra_body"] and "pinned_host" not in call.extra  # other models untouched


def test_no_pins_changes_nothing():  # the live bot's client
    client, seen = _client_capturing(None)
    call = asyncio.run(client.complete(model=DS, prompt="p", purpose="forecast", max_tokens=10))
    assert "provider" not in seen[0]["extra_body"] and "pinned_host" not in call.extra


def test_parse_pins():
    assert hosts.parse_pins([f"{DS}=deepinfra"]) == {DS: "deepinfra"}
    assert hosts.parse_pins(None) == {}
    for bad in (["nohost"], [f"{DS}="], [f"{DS}=a", f"{DS}=b"]):
        with pytest.raises(ValueError):
            hosts.parse_pins(bad)


def _rec(tmp_path, name, served, pinned):
    p = tmp_path / f"{name}.json"
    p.write_text(json.dumps({"question": {"question_id": 1}, "forecasters": [
        {"model": DS, "call": {"output": "x", "extra": {"upstream_provider": served, **({"pinned_host": pinned} if pinned else {})}}},
        {"model": "m2", "call": {"output": "x", "extra": {"upstream_provider": "OpenAI"}}},
        {"model": DS, "call": {"output": "", "error": "timeout", "extra": {}}},  # failed: no host, not counted
    ]}), encoding="utf-8")
    return p


def test_check_pins(tmp_path):
    ok = _rec(tmp_path, "ok", "DeepInfra", "deepinfra")
    other = _rec(tmp_path, "other", "Wafer", "deepinfra")
    unpinned = _rec(tmp_path, "unpinned", "DeepInfra", None)
    problems, summary = hosts.check_pins({"A": [ok], "B": [ok]}, {DS: "deepinfra"})
    assert problems == [] and any("pinned deepinfra" in s and "DeepInfra 1" in s for s in summary)
    problems, _ = hosts.check_pins({"A": [ok], "B": [other]}, {DS: "deepinfra"})
    assert len(problems) == 1 and "served by Wafer" in problems[0]
    problems, _ = hosts.check_pins({"A": [unpinned]}, {DS: "deepinfra"})
    assert len(problems) == 1 and "not requested with the pin" in problems[0]
    assert hosts.check_pins({"A": [other]}, {})[0] == []  # nothing pinned: summary only


class HostLlm(E2.RandomLlm):
    """A pinned client as the tool would build it: DeepSeek calls carry the pin; `served` names the host that answered."""

    def __init__(self, seed, pin, served):
        super().__init__(seed)
        self.pin, self.served = pin, served

    async def complete(self, **kw):
        call = await super().complete(**kw)
        if kw["model"] == DS:
            call.extra["pinned_host"] = self.pin
            call.extra["upstream_provider"] = self.served
        return call


def test_prompt_check_writes_host_section_when_the_pin_held(tmp_path, capsys):
    stored = PC._stored(tmp_path)
    llm = HostLlm(3, "deepinfra", "DeepInfra")
    assert PC._check(tmp_path, stored, ["--exp", "EXP-T", "--variant", PC.OLD, "--pin-hosts", f"{DS}=deepinfra"], llm) == 0
    md = next((tmp_path / "out" / "EXP-T").glob("*/report.md")).read_text(encoding="utf-8")
    assert "## Hosts (B-93)" in md and f"{DS} → deepinfra" in md


def test_prompt_check_refuses_the_report_when_a_call_ran_elsewhere(tmp_path, capsys):
    stored = PC._stored(tmp_path)
    llm = HostLlm(4, "deepinfra", "Wafer")
    assert PC._check(tmp_path, stored, ["--exp", "EXP-T", "--variant", PC.OLD, "--pin-hosts", f"{DS}=deepinfra"], llm) == 3
    run = next((tmp_path / "out" / "EXP-T").glob("*"))
    assert (run / "host_check.txt").exists() and not (run / "report.md").exists()
    assert "HOST CHECK FAILED" in capsys.readouterr().out
    ledger = stored[2].read_text(encoding="utf-8")
    assert "hosts pinned" in ledger  # the spend is still booked


def test_prompt_check_refuses_pins_with_an_unpinned_stored_baseline_before_spending(tmp_path, capsys):
    stored = PC._stored(tmp_path)
    llm = HostLlm(5, "deepinfra", "DeepInfra")
    argv = ["--exp", "EXP-T", "--variant", PC.OLD, "--reuse-baseline", str(stored[3]), "--baseline-arm", "B",
            "--pin-hosts", f"{DS}=deepinfra"]
    assert PC._check(tmp_path, stored, argv, llm) == 2 and llm.calls == 0
    assert "not run with the same host pins" in capsys.readouterr().out
    assert PC._check(tmp_path, stored, ["--exp", "EXP-T", "--variant", PC.OLD, "--pin-hosts", "nope/model=x"], llm) == 2


VH = _load("_vh_tests_hosts", ROOT / "tests" / "test_validate_harness.py")


class AaHostLlm(VH.RandomLlm):
    def __init__(self, served):
        super().__init__(7)
        self.served = served

    async def complete(self, **kw):
        call = await super().complete(**kw)
        if kw["model"] == DS:
            call.extra.update(pinned_host="deepinfra", upstream_provider=self.served)
        return call


def test_aa_replay_checks_the_pin(tmp_path, capsys):
    tool = VH._tool()
    code, _ = VH._run(tool, tmp_path / "ok", ["--n", "4", "--pin-hosts", f"{DS}=deepinfra"], AaHostLlm("DeepInfra"))
    assert code == 0 and "## Hosts (B-93)" in next((tmp_path / "ok" / "exp").glob("*/report.md")).read_text(encoding="utf-8")
    code, ledger = VH._run(tool, tmp_path / "bad", ["--n", "4", "--pin-hosts", f"{DS}=deepinfra"], AaHostLlm("Wafer"))
    assert code == 3 and not list((tmp_path / "bad" / "exp").glob("*/report.md"))
    assert "hosts pinned" in ledger.read_text(encoding="utf-8")
