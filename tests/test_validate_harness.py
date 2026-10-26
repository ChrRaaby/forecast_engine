"""B-32: harness validation (evals/validate_harness.py) and the real A/A replay tool (tools/run_aa_replay.py).

Synthetic data and a fake LLM only; offline (conftest blocks the network). Each check must pass on the real code and fail when
the thing it guards is broken, so we know it can fail.
"""
import importlib.util
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from evals import replay, report, scoring
from evals import validate_harness as vh
from evals.report_demo import synthetic_rows, write_csv
from forecast_engine.schema import LlmCall

ROOT = Path(__file__).resolve().parents[1]


def statuses(results):
    return [r.status for r in results]


def failed(results):
    return [f"{r.check}: {r.headline}" for r in results if r.status == vh.FAIL]


# ---------------------------------------------------------------- the four checks pass on the real code
def test_reproducibility_check(tmp_path):
    res = vh.check_reproducibility_offline(tmp_path)
    assert not failed(res) and statuses(res) == [vh.PASS] * 4


def test_aa_check():
    res = vh.check_aa_offline(trials=60, family_trials=30)
    assert not failed(res) and statuses(res) == [vh.PASS] * 3


def test_leakage_check(tmp_path):
    res = vh.check_leakage(tmp_path)
    assert not failed(res) and statuses(res) == [vh.PASS] * 5


def test_trivial_baseline_check():
    res = vh.check_trivial_offline(reps=500)
    assert not failed(res) and statuses(res) == [vh.PASS] * 3


def test_offline_cli_exits_0(capsys):
    assert vh.main(["--offline"]) == 0
    out = capsys.readouterr().out
    assert "0 failed, 0 skipped" in out and "HARNESS VALIDATED" in out
    for n in ("1 reproducibility", "2 A/A", "3 leakage", "4 trivial baselines"):
        assert f"PASS  {n}" in out
    assert "FAIL" not in out


# ---------------------------------------------------------------- ... and fail when what they guard is broken
@pytest.mark.parametrize("name", ["binary_log_score", "binary_brier", "binary_baseline_score"])
def test_broken_scorer_fails_the_trivial_baseline_check(monkeypatch, name):
    real = getattr(scoring, name)
    monkeypatch.setattr(scoring, name, lambda p, o, *a, **k: -real(p, o, *a, **k))
    assert failed(vh.check_trivial_offline(reps=200))


def test_broken_report_metric_fails_the_trivial_baseline_check(monkeypatch):
    """The report holds its own reference to the scorer; a sign flip there must fail too (truth no longer beats always-50%)."""
    flipped = report.Metric("log", "log score", True, lambda p, o: -scoring.binary_log_score(p, o))
    monkeypatch.setitem(report.METRICS, "log", flipped)
    res = vh.check_trivial_offline(reps=200)
    assert any("truth arm" in f for f in failed(res))


def test_too_narrow_cis_fail_the_aa_check(monkeypatch):
    real = report.cluster_bootstrap_ci

    def narrow(*a, **k):
        lo, hi, se = real(*a, **k)
        mid = (lo + hi) / 2
        return mid - (hi - lo) / 10, mid + (hi - lo) / 10, se / 5

    monkeypatch.setattr(report, "cluster_bootstrap_ci", narrow)
    res = vh.check_aa_offline(trials=40, family_trials=20)
    assert any("CI coverage" in f for f in failed(res))


@pytest.mark.parametrize("guard", ["assert_record_as_of", "assert_record_intact"])
def test_missing_replay_guard_fails_the_leakage_check(monkeypatch, tmp_path, guard):
    monkeypatch.setattr(replay, guard, lambda record: None)
    assert any("replay guard" in f for f in failed(vh.check_leakage(tmp_path)))


def test_a_seed_that_is_ignored_fails_reproducibility(monkeypatch, tmp_path):
    real = report.bootstrap_means
    monkeypatch.setattr(report, "bootstrap_means", lambda d, c, reps, seed, chunk=2000: real(d, c, reps, 1, chunk))
    assert any("seed change" in f for f in failed(vh.check_reproducibility_offline(tmp_path)))


# ---------------------------------------------------------------- pieces
def test_replay_refuses_future_research_before_any_model_call():
    import asyncio

    from evals.asof import LeakageError
    from forecast_engine.config import DEFAULT_CONFIG

    rec = vh.synthetic_record()
    rec["research"]["items"].append({"source": "s", "text": "t", "url": None, "title": None,
                                     "published_at": (vh.FIXTURE_AS_OF + timedelta(seconds=1)).isoformat()})
    llm = vh.FakeLlm()
    with pytest.raises(LeakageError):
        asyncio.run(replay.replay_record(rec, DEFAULT_CONFIG, llm))
    assert llm.calls == 0
    naive = vh.synthetic_record()
    naive["research"]["items"][0]["published_at"] = "2025-03-14T13:00:00"  # no zone: read as UTC, so 1 h late
    with pytest.raises(LeakageError):
        replay.assert_record_as_of(naive)
    replay.assert_record_as_of(vh.synthetic_record())  # undated items are allowed (live research, captured at as_of)


def test_binomial_band():
    assert vh.binom_band(200, 0.95) == (179, 198)
    lo, hi = vh.binom_band(100, 0.5)
    assert lo < 50 < hi and lo + hi == 100


def test_dates_after():
    as_of = datetime(2025, 3, 14, tzinfo=timezone.utc)
    assert vh.dates_after("Today is 2025-03-14. Since 2024-12-01, in 2025.", as_of) == []
    assert vh.dates_after("by 2025-03-15", as_of) == ["2025-03-15"]
    assert vh.dates_after("in 2026", as_of) == ["2026"]


def _aa_table(tmp_path, noise_a2=0.8, seed=6):
    # An A/A table's CI misses 0 one time in 20 by chance (seed 5 is such a table); seed 6 is an ordinary one.
    rows = synthetic_rows(60, 30, arm_noise={"A": 0.8, "A2": noise_a2}, arm_scale={"A": 1.3, "A2": 1.3}, k=1, seed=seed)
    path = tmp_path / "aa.csv"
    write_csv(rows, path)
    return path


def test_real_mode_on_a_synthetic_table(tmp_path, capsys):
    path = _aa_table(tmp_path)
    runs = tmp_path / "runs"
    (runs / "20261005T000000Z-tournament").mkdir(parents=True)
    (runs / "20261005T000000Z-tournament" / "q9001.json").write_text(json.dumps(vh.synthetic_record()), encoding="utf-8")
    code = vh.main(["--real", str(path), "--runs-dir", str(runs)])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "real A/A A2 vs A" in out and "1/1 archived live records reproduce" in out
    assert "date guard on the archive: 0/1 live records would be refused" in out and "INFO  4 trivial baselines" in out
    assert "Synthetic article" not in out  # research text is never printed


def test_real_mode_skips_what_it_cannot_run(tmp_path, capsys):
    path = _aa_table(tmp_path)
    code = vh.main(["--real", str(path), "--runs-dir", str(tmp_path / "missing"), "--aa-arms", "A", "B"])
    out = capsys.readouterr().out
    assert code == 2 and "SKIP  1 reproducibility: replay prompts" in out and "SKIP  2 A/A" in out and "0 failed" in out


def test_real_aa_fails_on_a_real_difference(tmp_path):
    rows = report.read_table(_aa_table(tmp_path, noise_a2=3.0))  # A2 is far noisier: not an A/A table at all
    assert statuses(vh.check_aa_real(rows, ("A", "A2"))) == [vh.FAIL]


# ---------------------------------------------------------------- tools/run_aa_replay.py with a fake LLM
def _tool():
    spec = importlib.util.spec_from_file_location("run_aa_replay", ROOT / "tools" / "run_aa_replay.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class RandomLlm:
    def __init__(self, seed=0):
        self.rng, self.calls = random.Random(seed), 0

    async def complete(self, *, model, prompt, purpose, max_tokens, reasoning_effort=None):
        self.calls += 1
        return LlmCall(purpose=purpose, provider="fake", model=model, params={}, prompt=prompt,
                       requested_at=datetime(2026, 10, 26, tzinfo=timezone.utc),
                       output=f"Probability: {self.rng.randint(15, 85)}%", cost_usd=0.001, tokens_in=100, tokens_out=50)


AS_OF = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)  # after the cheap roster's release dates


def _archive(tmp_path, n=12):
    runs, outcomes = tmp_path / "runs" / "1" / "20261005T120000Z-tournament", []
    runs.mkdir(parents=True)
    for i in range(n):
        qid = 50_000 + i
        as_of = AS_OF if i != 0 else datetime(2026, 9, 1, tzinfo=timezone.utc)  # q50000: before a model's release: skipped
        rec = vh.synthetic_record("binary", as_of, qid)
        if i == 1:  # q50001: research dated after as_of: refused by the replay guard
            rec["research"]["items"][0]["published_at"] = (as_of + timedelta(days=1)).isoformat()
        if i == 2:  # q50002: research edited after the live forecast: refused by the integrity check
            rec["research"]["items"][0]["text"] = "Edited later."
        (runs / f"q{qid}.json").write_text(json.dumps(rec), encoding="utf-8")
        outcomes.append({"question_id": qid, "status": "resolved", "resolution": "yes" if i % 3 else "no",
                         "actual_resolve_time": "2026-10-20T00:00:00Z"})
    outcomes.append({"question_id": 99, "status": "open", "resolution": None})
    out = tmp_path / "outcomes.jsonl"
    out.write_text("".join(json.dumps(o) + "\n" for o in outcomes), encoding="utf-8")
    ledger = tmp_path / "ledger.md"
    ledger.write_text("| Date | x |\n|---|---|\n| 2026-10-07 | EXP-005 | r | w | 60 | $0.666 | $0.666 | $179.33 |\n",
                      encoding="utf-8")
    return tmp_path / "runs", out, ledger


def _run(tool, tmp_path, argv, llm):
    runs, out, ledger = _archive(tmp_path)
    code = tool.run(tool.parser().parse_args(argv), llm, runs_dir=runs, outcomes_path=out, out_root=tmp_path / "exp",
                    ledger=ledger)
    return code, ledger


def test_aa_replay_writes_both_arms_and_a_report(tmp_path, capsys):
    tool, llm = _tool(), RandomLlm()
    code, ledger = _run(tool, tmp_path, ["--n", "8", "--max-cost", "5"], llm)
    out = capsys.readouterr().out
    assert code == 0, out
    assert "skipped 1: a model in the config was released too late" in out and "skipped 1: research dated after" in out
    assert "skipped 1: stored inputs don't rebuild the live prompt" in out
    assert "beyond --n 8" in out and "estimated cost" in out
    table = next((tmp_path / "exp").glob("*/runs.csv"))
    rows = report.read_table(table)
    assert {r.arm for r in rows} == {"A", "A2"} and len({r.question_id for r in rows}) == 8
    assert llm.calls == 2 * 8 * 3  # polarity check off: one call per member per arm
    assert (table.parent / "report.md").exists() and len(list((table.parent / "records" / "A2").glob("q*.json"))) == 8
    assert "| B-32 |" in ledger.read_text(encoding="utf-8").splitlines()[-1]
    # the table is one --real accepts (whether 8 random fake forecasts pass the A/A is chance, so not asserted)
    assert statuses(vh.check_aa_real(rows, ("A", "A2"))) in ([vh.PASS], [vh.FAIL])


def test_aa_replay_dry_run_and_cost_guard_make_no_calls(tmp_path, capsys):
    tool = _tool()
    llm = RandomLlm()
    assert _run(tool, tmp_path / "a", ["--dry-run"], llm)[0] == 0
    assert _run(tool, tmp_path / "b", ["--max-cost", "0.01"], llm)[0] == 2  # 9 records × 2 arms × 3 calls × $0.001 floor × 1.5 > $0.01
    assert llm.calls == 0
    out = capsys.readouterr().out
    assert "dry run: no model was called" in out and "REFUSED" in out
    assert not (tmp_path / "a" / "exp").exists() and not (tmp_path / "b" / "exp").exists()


def test_aa_replay_hard_cap_stops_calls(tmp_path):
    tool = _tool()
    capped = tool.CappedLlm(RandomLlm(), cap=0.01, reserve={"m": 0.001})
    import asyncio

    calls = [asyncio.run(capped.complete(model="m", prompt="p", purpose="forecast", max_tokens=1)) for _ in range(20)]
    assert capped.spent <= 0.01 and capped.refused > 0 and any(c.error and "budget cap" in c.error for c in calls)
