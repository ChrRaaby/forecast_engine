"""EXP-002 (B-09b): the dates + "resolves YES" prompt variant and its replay tool (tools/run_exp002_replay.py). Offline; synthetic only."""
import importlib.util
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from evals import prompt_archive as pa
from evals import report
from evals import validate_harness as vh
from forecast_engine import prompt_versions
from forecast_engine import prompts as live_prompts
from forecast_engine.config import DEFAULT_CONFIG, TOURNAMENT_CONFIG
from forecast_engine.prompt_variants import dates_yes_2026_11_07 as variant
from forecast_engine.schema import LlmCall

ROOT = Path(__file__).resolve().parents[1]
V = variant.VERSION
CASES = {name: (q, items, as_of) for name, q, items, as_of in pa.fixture_cases()}


# ---------------------------------------------------------------- the variant prompt
def test_variant_is_registered_archived_and_not_live():
    assert V in prompt_versions.VARIANTS and V in pa.ARCHIVE and V != prompt_versions.LIVE
    assert DEFAULT_CONFIG.prompt_version == TOURNAMENT_CONFIG.prompt_version == prompt_versions.LIVE
    assert pa.drift_problems() == []


def test_dated_binary_prompt_adds_exactly_the_date_line_and_the_yes_wording():
    q, items, as_of = CASES["binary, dated"]
    live = pa.live_prompt(q, items, as_of)
    new = pa.variant_prompt(V, q, items, as_of)
    assert "Today is 2025-03-14.\n        This question is still open. It is scheduled to resolve on 2025-06-30.\n" in new
    assert new.rstrip().endswith('"Probability: ZZ%", 0-100, where ZZ is the probability that this question resolves YES')
    # nothing else changed: undo the two edits and the live prompt comes back byte for byte
    undone = (new.replace("\n        This question is still open. It is scheduled to resolve on 2025-06-30.", "")
              .replace(", where ZZ is the probability that this question resolves YES", ""))
    assert undone == live


def test_undated_question_gets_no_date_line():
    q, items, as_of = CASES["binary"]
    assert q.scheduled_resolution_time is None
    new = pa.variant_prompt(V, q, items, as_of)
    assert "scheduled to resolve" not in new
    assert new.replace(", where ZZ is the probability that this question resolves YES", "") == pa.live_prompt(q, items, as_of)


def test_mc_and_numeric_get_only_the_date_line():
    for name in ("multiple choice, dated", "numeric, dated"):
        q, items, as_of = CASES[name]
        new = pa.variant_prompt(V, q, items, as_of)
        line = f"\n        This question is still open. It is scheduled to resolve on {q.scheduled_resolution_time:%Y-%m-%d}."
        assert line in new and "resolves YES" not in new
        assert new.replace(line, "") == pa.live_prompt(q, items, as_of)


def test_variant_answers_still_parse():
    from forecast_engine import parsing

    for text in ('Probability: 37%', 'Probability: 37%, where ZZ is the probability that this question resolves YES',
                 'Probability: 37% (the probability that this question resolves YES)'):
        assert abs(parsing.parse_binary(text, DEFAULT_CONFIG.binary_clip) - 0.37) < 1e-9


def test_live_prompt_module_is_untouched_by_the_variant():
    q, items, as_of = CASES["binary, dated"]
    assert "scheduled to resolve" not in live_prompts.forecast_prompt(q, "r", "2025-03-14")


# ---------------------------------------------------------------- tools/run_exp002_replay.py with a fake LLM
def _tool():
    spec = importlib.util.spec_from_file_location("run_exp002_replay", ROOT / "tools" / "run_exp002_replay.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class RandomLlm:
    """Forecasts get a random probability; polarity audits answer YES, except for arm A's 'NO' marker."""

    def __init__(self, seed=0):
        self.rng, self.calls, self.prompts = random.Random(seed), 0, []

    async def complete(self, *, model, prompt, purpose, max_tokens, reasoning_effort=None):
        self.calls += 1
        self.prompts.append((purpose, prompt))
        if purpose == "polarity_check":
            out = '{"refers_to": "YES", "why": "fake"}'
        else:
            out = f"Probability: {self.rng.randint(15, 85)}%"
        return LlmCall(purpose=purpose, provider="fake", model=model, params={}, prompt=prompt,
                       requested_at=datetime(2026, 10, 26, tzinfo=timezone.utc), output=out, cost_usd=0.001,
                       tokens_in=100, tokens_out=50)


AS_OF = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)


def _archive(tmp_path, n=10):
    runs, outcomes = tmp_path / "runs" / "1" / "20261005T120000Z-tournament", []
    runs.mkdir(parents=True)
    for i in range(n):
        qid = 60_000 + i
        rec = vh.synthetic_record("binary", AS_OF, qid)
        if i % 2 == 0:  # the live prompt ignores this date, so the stored record still passes the integrity check
            rec["question"]["scheduled_resolution_time"] = "2026-12-31T23:00:00+00:00"
        if i == 1:  # research dated after as_of: refused by the replay guard
            rec["research"]["items"][0]["published_at"] = (AS_OF + timedelta(days=1)).isoformat()
        (runs / f"q{qid}.json").write_text(json.dumps(rec), encoding="utf-8")
        resolved = "2026-10-20T00:00:00Z" if i < 6 else "2026-11-20T00:00:00Z"
        outcomes.append({"question_id": qid, "status": "resolved", "resolution": "yes" if i % 3 else "no",
                         "actual_resolve_time": resolved})
    out = tmp_path / "outcomes.jsonl"
    out.write_text("".join(json.dumps(o) + "\n" for o in outcomes), encoding="utf-8")
    ledger = tmp_path / "ledger.md"
    ledger.write_text("| Date | x |\n|---|---|\n| 2026-10-07 | EXP-005 | r | w | 60 | $0.666 | $0.666 | $179.33 |\n",
                      encoding="utf-8")
    return tmp_path / "runs", out, ledger


def _run(tool, tmp_path, argv, llm):
    runs, out, ledger = _archive(tmp_path)
    code = tool.run(tool.parser().parse_args(["--config", "cheap", *argv]), llm, runs_dir=runs, outcomes_path=out,
                    out_root=tmp_path / "exp", ledger=ledger)
    return code, ledger


def test_arms_differ_only_by_prompt_version():
    cfgs = _tool().arm_configs("frontier")
    assert cfgs["A"].prompt_version == prompt_versions.LIVE and cfgs["B"].prompt_version == V
    assert cfgs["A"].polarity_check_model is None and cfgs["B"].polarity_check_model is None
    from dataclasses import replace

    assert replace(cfgs["B"], prompt_version=cfgs["A"].prompt_version) == cfgs["A"]
    assert cfgs["A"].forecasters == TOURNAMENT_CONFIG.forecasters


def test_replay_writes_both_arms_audit_and_report(tmp_path, capsys):
    tool, llm = _tool(), RandomLlm()
    code, ledger = _run(tool, tmp_path, ["--max-cost", "5"], llm)
    out = capsys.readouterr().out
    assert code == 0, out
    assert "skipped 1: research dated after" in out and "5/9 have a scheduled resolution date" in out
    run_dir = next((tmp_path / "exp").glob("*-screen"))
    rows = report.read_table(run_dir / "runs.csv")
    assert {r.arm for r in rows} == {"A", "B"} and len({r.question_id for r in rows}) == 9
    members = len(DEFAULT_CONFIG.forecasters)
    forecast_prompts = [p for purpose, p in llm.prompts if purpose == "forecast"]
    assert len(forecast_prompts) == 2 * 9 * members  # no research, no follow-up search, no polarity check: forecasts only
    assert sum(1 for p in forecast_prompts if "scheduled to resolve on 2026-12-31" in p) == 5 * members  # arm B, dated records
    assert sum(1 for p in forecast_prompts if "resolves YES" in p) == 9 * members  # arm B only
    assert sum(1 for purpose, _ in llm.prompts if purpose == "polarity_check") == 2 * 9 * members  # the audit, after
    assert "arm A: 0/" in out and "arm B: 0/" in out
    assert (run_dir / "polarity.csv").exists() and "polarity audit" in (run_dir / "report.md").read_text(encoding="utf-8")
    assert "EXP-002 rule" in out
    b = json.loads(next((run_dir / "records" / "B").glob("q*.json")).read_text(encoding="utf-8"))
    assert b["prompt_version"] == V
    assert "| EXP-002 |" in ledger.read_text(encoding="utf-8").splitlines()[-1]


def test_rerun_keeps_only_questions_resolved_after_the_date(tmp_path, capsys):
    tool, llm = _tool(), RandomLlm()
    code, _ = _run(tool, tmp_path, ["--resolved-after", "2026-11-01", "--run-kind", "rerun", "--no-audit"], llm)
    out = capsys.readouterr().out
    assert code == 0, out
    rows = report.read_table(next((tmp_path / "exp").glob("*-rerun")) / "runs.csv")
    assert {int(r.question_id) for r in rows} == {60_006, 60_007, 60_008, 60_009}
    assert all(purpose == "forecast" for purpose, _ in llm.prompts)


def test_dry_run_and_cost_guard_make_no_calls(tmp_path, capsys):
    tool, llm = _tool(), RandomLlm()
    assert _run(tool, tmp_path / "a", ["--dry-run"], llm)[0] == 0
    assert _run(tool, tmp_path / "b", ["--max-cost", "0.01"], llm)[0] == 2
    assert llm.calls == 0
    out = capsys.readouterr().out
    assert "dry run: no model was called" in out and "REFUSED" in out
    assert not (tmp_path / "a" / "exp").exists() and not (tmp_path / "b" / "exp").exists()


def test_polarity_lines_count_no_and_other():
    rows = [{"arm": "A", "verdict": v} for v in ("YES", "NO", "OTHER", "FAILED")] + [{"arm": "B", "verdict": "YES"}]
    a, b = _tool().polarity_lines(rows)
    assert a.startswith("arm A: 2/4") and b.startswith("arm B: 0/1")
