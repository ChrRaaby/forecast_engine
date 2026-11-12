"""B-95: tools/run_research_noise.py with a fake LLM and fake research. Offline; synthetic only."""
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from evals import validate_harness as vh
from evals.replay import bundle_from_dict

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 12, 22, tzinfo=timezone.utc)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


VH = _load("_vh_tests_noise", ROOT / "tests" / "test_validate_harness.py")


def _tool():
    return _load("run_research_noise", ROOT / "tools" / "run_research_noise.py")


def _archive(tmp_path):
    runs = tmp_path / "runs" / "1" / "20261110T120000Z-tournament"
    shadow = tmp_path / "runs" / "1" / "20261110T120000Z-tournament-shadow-cheap"
    runs.mkdir(parents=True)
    shadow.mkdir(parents=True)
    rows = []
    for i in range(8):
        qid = 60_000 + i
        rec = vh.synthetic_record("binary", datetime(2026, 11, 10, 12, tzinfo=timezone.utc), qid)
        (runs / f"q{qid}.json").write_text(json.dumps(rec), encoding="utf-8")
        (shadow / f"q{qid}.json").write_text(json.dumps(rec), encoding="utf-8")
        when = "2026-11-14T00:00:00Z" if i == 0 else "2026-12-20T00:00:00Z"  # q60000 resolves too soon
        status = "resolved" if i == 1 else "closed"  # q60001 already resolved
        rows.append({"question_id": qid, "status": status, "resolution": "yes" if i == 1 else None,
                     "scheduled_resolve_time": when})
    out = tmp_path / "outcomes.jsonl"
    out.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    ledger = tmp_path / "ledger.md"
    ledger.write_text("| Date | x |\n|---|---|\n| 2026-10-07 | EXP-005 | r | w | 60 | $0.666 | $0.666 | $179.33 |\n",
                      encoding="utf-8")
    return tmp_path / "runs", out, ledger, rec


def test_picks_unresolved_far_questions_and_runs_three_forecasts(tmp_path, capsys):
    tool = _tool()
    runs, out, ledger, rec = _archive(tmp_path)
    fetched = []

    async def research(q):
        fetched.append(q.question_id)
        return bundle_from_dict(rec["research"])

    llm = VH.RandomLlm(5)
    args = tool.parser().parse_args(["--n", "10"])
    code = tool.run(args, llm, research, runs_dir=runs, outcomes_path=out, out_root=tmp_path / "exp", ledger=ledger, now=NOW)
    text = capsys.readouterr().out
    assert code == 0, text
    assert "6 of 6 eligible" in text  # shadow records, a resolved and a soon-resolving question are left out
    assert sorted(fetched) == sorted(2 * [60_002 + i for i in range(6)])  # research fetched twice per question
    assert llm.calls == 3 * 6 * 3
    run = next((tmp_path / "exp").glob("*"))
    assert {p.name for p in (run / "records").iterdir()} == {"S1", "S2", "R2"}
    rows = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    assert len(rows) == 6 and all(r["url_overlap"] in (None, 1.0) for r in rows)  # identical fake research
    assert "same research, answers again" in text and "| B-95 |" in ledger.read_text(encoding="utf-8")


def test_dry_run_and_cost_guard_call_nothing(tmp_path, capsys):
    tool = _tool()
    runs, out, ledger, _ = _archive(tmp_path)
    llm = VH.RandomLlm(6)

    async def research(q):
        raise AssertionError("no research in a dry run")

    for argv, code in ((["--dry-run"], 0), (["--max-cost", "0.001"], 2)):
        assert tool.run(tool.parser().parse_args(argv), llm, research, runs_dir=runs, outcomes_path=out,
                        out_root=tmp_path / "exp", ledger=ledger, now=NOW) == code
    assert llm.calls == 0 and not (tmp_path / "exp").exists()


def test_summary_separates_sampling_from_research_noise():
    tool = _tool()
    rows = [{"S1": 0.30, "S2": 0.32, "R2": 0.40, "url_overlap": 0.5}, {"S1": 0.60, "S2": 0.58, "R2": 0.50, "url_overlap": 0.7},
            {"S1": 0.20, "S2": 0.21, "R2": None}]
    lines = tool.summarise(rows, reps=200)
    assert lines[0].startswith("N 2 questions") and "mean |Δp| 2.0 pts" in lines[1] and "mean |Δp| 10.0 pts" in lines[2]
    assert "+8.0 pts" in lines[3] and "60% of URLs" in lines[4]
