"""B-79: tools/run_prompt_check.py, a prompt check that can reuse arm A from a stored run. Offline; synthetic only.

The stored run is made by tools/run_exp002_replay.py on a synthetic archive (cheap roster): arm A = the old prompt, arm B = the
EXP-002 variant, whose text is today's live prompt."""
import importlib.util
import json
import shutil
import sys
from pathlib import Path

from evals import report
from forecast_engine import prompt_versions
from forecast_engine.config import DEFAULT_CONFIG

ROOT = Path(__file__).resolve().parents[1]
OLD = "template-2026-09-26-old"
MEMBERS = len(DEFAULT_CONFIG.forecasters)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # the tool defines dataclasses, which look their module up here
    spec.loader.exec_module(mod)
    return mod


E2 = _load("_exp002_tests", ROOT / "tests" / "test_exp002_replay.py")  # synthetic archive + fake LLM, shared


def _tool():
    return _load("run_prompt_check", ROOT / "tools" / "run_prompt_check.py")


def _stored(tmp_path):
    """A synthetic archive and one EXP-002 screen run on it (9 usable questions). Returns (runs, outcomes, ledger, run dir)."""
    runs, outcomes, ledger = E2._archive(tmp_path)
    exp2 = E2._tool()
    code = exp2.run(exp2.parser().parse_args(["--config", "cheap", "--no-audit", "--max-cost", "5"]), E2.RandomLlm(1),
                    runs_dir=runs, outcomes_path=outcomes, out_root=tmp_path / "exp002", ledger=ledger)
    assert code == 0
    return runs, outcomes, ledger, next((tmp_path / "exp002").glob("*-screen"))


def _check(tmp_path, stored, argv, llm):
    runs, outcomes, ledger, _ = stored
    tool = _tool()
    return tool.run(tool.parser().parse_args(argv), llm, runs_dir=runs, outcomes_path=outcomes, out_root=tmp_path / "out",
                    ledger=ledger)


def _forecasts(llm):
    return [p for purpose, p in llm.prompts if purpose == "forecast"]


def test_same_prompt_text_compares_rendered_text_not_names():
    t = _tool()
    assert t.same_prompt_text("dates-yes-2026-11-07", prompt_versions.LIVE)  # EXP-002's variant is the text that went live
    assert t.same_prompt_text(OLD, "template-2026-09-26")
    assert not t.same_prompt_text(OLD, prompt_versions.LIVE)


def test_reuse_pays_only_for_the_new_arm(tmp_path, capsys):
    stored = _stored(tmp_path)
    capsys.readouterr()
    llm = E2.RandomLlm(2)
    code = _check(tmp_path, stored, ["--exp", "EXP-T", "--variant", OLD, "--reuse-baseline", str(stored[3]),
                                     "--baseline-arm", "B"], llm)
    out = capsys.readouterr().out
    assert code == 0, out
    assert "9 questions reused, 0 run fresh" in out
    fc = _forecasts(llm)
    assert len(fc) == 9 * MEMBERS  # arm B only
    assert not any("resolves YES" in p for p in fc)  # arm B is the old prompt here; the reused arm A is the live text
    run_dir = next((tmp_path / "out" / "EXP-T").glob("*-screen"))
    rows = report.read_table(run_dir / "runs.csv")
    assert {r.arm for r in rows} == {"A", "B"} and len({r.question_id for r in rows if r.arm == "A"}) == 9
    stored_b = [r for r in report.read_table(stored[3] / "runs.csv") if r.arm == "B"]
    new_a = [r for r in rows if r.arm == "A"]
    key = lambda r: (r.question_id, r.member)  # noqa: E731
    assert sorted((key(r), r.p) for r in new_a) == sorted((key(r), r.p) for r in stored_b)  # arm A = the stored answers
    md = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "not run at the same moment (T11)" in md and "Baseline reuse (B-79)" in md
    assert len(list((run_dir / "records" / "A").glob("q*.json"))) == 9  # kept for the next check
    assert json.loads((run_dir / "reuse.json").read_text())["reused_questions"]
    assert "| EXP-T |" in stored[2].read_text(encoding="utf-8").splitlines()[-1]


def test_both_arms_replay_the_same_live_record(tmp_path, capsys):
    """Arm B on a reused question replays the very record the stored arm A was made from."""
    stored = _stored(tmp_path)
    llm = E2.RandomLlm(3)
    assert _check(tmp_path, stored, ["--exp", "EXP-T", "--variant", OLD, "--reuse-baseline", str(stored[3]),
                                     "--baseline-arm", "B"], llm) == 0
    run_dir = next((tmp_path / "out" / "EXP-T").glob("*-screen"))
    for b in (run_dir / "records" / "B").glob("q*.json"):
        a = json.loads((run_dir / "records" / "A" / b.name).read_text(encoding="utf-8"))
        assert json.loads(b.read_text(encoding="utf-8"))["replayed_from"] == a["replayed_from"]


def test_missing_live_record_runs_that_question_fresh(tmp_path, capsys):
    stored = _stored(tmp_path)
    runs = stored[0]
    victim = json.loads(next((stored[3] / "records" / "B").glob("q*.json")).read_text(encoding="utf-8"))["replayed_from"]
    (runs / victim).unlink()
    capsys.readouterr()
    llm = E2.RandomLlm(4)
    assert _check(tmp_path, stored, ["--exp", "EXP-T", "--variant", OLD, "--reuse-baseline", str(stored[3]),
                                     "--baseline-arm", "B"], llm) == 0
    out = capsys.readouterr().out
    assert "1 stored questions not reusable" in out and "is not in the archive" in out
    # the deleted record's question has no other live record, so it drops out of the pick: 8 reused, 0 fresh
    assert "8 questions reused" in out and len(_forecasts(llm)) == 8 * MEMBERS


def test_reuse_refused_when_the_prompt_text_differs(tmp_path, capsys):
    stored = _stored(tmp_path)
    capsys.readouterr()
    llm = E2.RandomLlm(5)
    code = _check(tmp_path, stored, ["--exp", "EXP-T", "--variant", OLD, "--reuse-baseline", str(stored[3]),
                                     "--baseline-arm", "A"], llm)  # stored arm A is the OLD prompt, the baseline is live
    assert code == 2 and "REUSE REFUSED" in capsys.readouterr().out and llm.calls == 0


def test_reuse_refused_when_the_roster_differs(tmp_path, capsys):
    stored = _stored(tmp_path)
    capsys.readouterr()
    llm = E2.RandomLlm(6)
    code = _check(tmp_path, stored, ["--exp", "EXP-T", "--variant", OLD, "--config", "frontier", "--reuse-baseline",
                                     str(stored[3]), "--baseline-arm", "B"], llm)
    out = capsys.readouterr().out
    assert code == 2 and "config_hash" in out and llm.calls == 0


def test_rescore_reproduces_the_stored_report_for_free(tmp_path, capsys):
    stored = _stored(tmp_path)
    original = next(ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("B vs A:"))
    llm = E2.RandomLlm(7)
    code = _check(tmp_path, stored, ["--exp", "B-79", "--baseline-prompt", OLD, "--reuse-baseline", str(stored[3]),
                                     "--baseline-arm", "A", "--rescore-arm", "B"], llm)
    out = capsys.readouterr().out
    assert code == 0, out
    assert llm.calls == 0 and not (tmp_path / "out").exists()
    assert original in out.splitlines()  # the same N, Δ and CI as the run that made the arms


def test_drift_check_replays_the_baseline_on_the_reused_questions(tmp_path, capsys):
    stored = _stored(tmp_path)
    capsys.readouterr()
    llm = E2.RandomLlm(8)
    code = _check(tmp_path, stored, ["--exp", "B-79", "--reuse-baseline", str(stored[3]), "--baseline-arm", "B",
                                     "--drift-check"], llm)
    out = capsys.readouterr().out
    assert code == 0, out
    fc = _forecasts(llm)
    assert len(fc) == 9 * MEMBERS and all("resolves YES" in p for p in fc)  # the baseline (live) prompt, once per question
    assert "A2 vs A: N 9" in out and "mean |Δp|" in out
    run_dir = next((tmp_path / "out" / "B-79").glob("*-drift"))
    assert {r.arm for r in report.read_table(run_dir / "runs.csv")} == {"A", "A2"}


def test_next_check_can_reuse_the_new_run(tmp_path, capsys):
    stored = _stored(tmp_path)
    assert _check(tmp_path, stored, ["--exp", "EXP-T", "--variant", OLD, "--reuse-baseline", str(stored[3]),
                                     "--baseline-arm", "B"], E2.RandomLlm(9)) == 0
    first = next((tmp_path / "out" / "EXP-T").glob("*-screen"))
    moved = tmp_path / "first-check"
    shutil.move(str(first), moved)
    capsys.readouterr()
    llm = E2.RandomLlm(10)
    assert _check(tmp_path, stored, ["--exp", "EXP-T", "--variant", OLD, "--reuse-baseline", str(moved)], llm) == 0
    assert "9 questions reused" in capsys.readouterr().out and len(_forecasts(llm)) == 9 * MEMBERS


def test_without_reuse_both_arms_run_and_dry_run_calls_nothing(tmp_path, capsys):
    stored = _stored(tmp_path)
    llm = E2.RandomLlm(11)
    assert _check(tmp_path, stored, ["--exp", "EXP-T", "--variant", OLD, "--dry-run"], llm) == 0
    assert llm.calls == 0 and "dry run" in capsys.readouterr().out
    assert _check(tmp_path, stored, ["--exp", "EXP-T", "--variant", OLD], llm) == 0
    assert len(_forecasts(llm)) == 2 * 9 * MEMBERS


def test_bad_arguments_are_refused(tmp_path, capsys):
    stored = _stored(tmp_path)
    llm = E2.RandomLlm(12)
    assert _check(tmp_path, stored, ["--exp", "X"], llm) == 2  # no variant
    assert _check(tmp_path, stored, ["--exp", "X", "--drift-check"], llm) == 2  # drift needs a stored run
    assert _check(tmp_path, stored, ["--exp", "X", "--variant", "nope"], llm) == 2
    assert _check(tmp_path, stored, ["--exp", "X", "--variant", prompt_versions.LIVE], llm) == 2  # variant = baseline
    assert llm.calls == 0
