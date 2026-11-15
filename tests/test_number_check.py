"""EXP-015: tools/run_number_check.py, the prompt check's number mode (numeric and discrete records). Offline; synthetic only.

The fake models answer arm B's prompt (the range-calibrated variant) with a narrower range around the true value, so arm B must
score better; scoring goes through the live CDF builder (tools/exp010.py's LiveCdf -> main.to_prediction)."""
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from evals import validate_harness as vh
from forecast_engine.config import DEFAULT_CONFIG
from forecast_engine.prompt_variants import range_calibrated_2026_11_14 as variant
from forecast_engine.schema import LlmCall

ROOT = Path(__file__).resolve().parents[1]
AS_OF = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
VARIANT = variant.VERSION
MEMBERS = len(DEFAULT_CONFIG.forecasters)


def _tool():
    name = "run_number_check"
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # the tool defines dataclasses, which look their module up here
    spec.loader.exec_module(mod)
    return mod


class NumberLlm:
    """Percentiles around 50: wide (10..90) for the live prompt, narrow (40..60) for the range-calibrated variant."""

    def __init__(self, host: str | None = None):
        self.calls, self.prompts, self.host = 0, [], host

    async def complete(self, *, model, prompt, purpose, max_tokens, reasoning_effort=None):
        self.calls += 1
        self.prompts.append(prompt)
        narrow = variant.NEW_RANGE_SENTENCE in prompt
        vals = (40, 44, 48, 52, 56, 60) if narrow else (10, 20, 40, 60, 80, 90)
        out = "\n".join(f"Percentile {p}: {v}" for p, v in zip((10, 20, 40, 60, 80, 90), vals))
        extra = {"upstream_provider": self.host, "pinned_host": self.host} if self.host else {}
        return LlmCall(purpose=purpose, provider="fake", model=model, params={}, prompt=prompt,
                       requested_at=datetime(2026, 11, 15, tzinfo=timezone.utc), output=out, cost_usd=0.001,
                       tokens_in=100, tokens_out=50, extra=extra)


def _record(qid, qtype="numeric", axis=False):
    rec = vh.synthetic_record("numeric", AS_OF, qid)
    rec["question"]["question_type"] = qtype  # numeric and discrete share the prompt, so the integrity check still passes
    rec.update(status="ok", published=True)
    if axis:
        rec["submitted"] = {"type": qtype, "axis": {"lower_bound": 0.0, "upper_bound": 100.0, "open_lower_bound": False,
                                                    "open_upper_bound": False, "zero_point": None,
                                                    "cdf_size": 201 if qtype == "numeric" else 101}}
    return rec


def _archive(tmp_path, n=12):
    """n published numeric/discrete records (half with submitted.axis, half via the scaling file), plus: one without any axis,
    one annulled, one unresolved, one shadow record, one wide-mode record and one test-questions record (never picked)."""
    runs = tmp_path / "runs" / "1" / "20261005T120000Z-tournament"
    runs.mkdir(parents=True)
    outcomes, scaling = [], []
    for i in range(n + 3):
        qid = 70_000 + i
        qtype = "numeric" if i % 2 == 0 else "discrete"
        rec = _record(qid, qtype, axis=i % 4 < 2)
        (runs / f"q{qid}.json").write_text(json.dumps(rec), encoding="utf-8")
        if i < n and not rec.get("submitted"):
            scaling.append({"question_id": qid, "range_min": 0.0, "range_max": 100.0, "type": qtype, "zero_point": None,
                            "inbound_outcome_count": 200 if qtype == "numeric" else 100})
        if i == n:  # no axis at all
            rec.pop("submitted", None)
            (runs / f"q{qid}.json").write_text(json.dumps(rec), encoding="utf-8")
        status, res = ("resolved", "50") if i <= n else ("resolved", "annulled") if i == n + 1 else ("open", None)
        outcomes.append({"question_id": qid, "status": status, "resolution": res,
                         "actual_resolve_time": "2026-10-20T00:00:00Z" if res else None})
    shadow = tmp_path / "runs" / "1" / "20261005T120000Z-tournament-shadow-cheap"
    shadow.mkdir()
    (shadow / "q70000.json").write_text(json.dumps({**_record(70_000, axis=True), "status": "shadow"}), encoding="utf-8")
    wide = tmp_path / "runs" / "1" / "20261005T120000Z-wide"  # wide mode: published, but not a tournament run (left out)
    wide.mkdir()
    (wide / "q79998.json").write_text(json.dumps(_record(79_998, axis=True)), encoding="utf-8")
    outcomes.append({"question_id": 79_998, "status": "resolved", "resolution": "50", "actual_resolve_time": "2026-10-20T00:00:00Z"})
    test_q = tmp_path / "runs" / "1" / "20261005T120000Z-test_questions"
    test_q.mkdir()
    (test_q / "q79999.json").write_text(json.dumps(_record(79_999, axis=True)), encoding="utf-8")
    outcomes.append({"question_id": 79_999, "status": "resolved", "resolution": "50", "actual_resolve_time": "2026-10-20T00:00:00Z"})
    out = tmp_path / "outcomes.jsonl"
    out.write_text("".join(json.dumps(o) + "\n" for o in outcomes), encoding="utf-8")
    sc = tmp_path / "scaling.jsonl"
    sc.write_text("".join(json.dumps(s) + "\n" for s in scaling), encoding="utf-8")
    ledger = tmp_path / "ledger.md"
    ledger.write_text("| Date | x |\n|---|---|\n| 2026-10-07 | EXP-005 | r | w | 60 | $0.666 | $0.666 | $179.33 |\n",
                      encoding="utf-8")
    return tmp_path / "runs", out, sc, ledger


def _run(tmp_path, argv, llm, archive=None):
    runs, outcomes, sc, ledger = archive or _archive(tmp_path)
    tool = _tool()
    code = tool.run(tool.parser().parse_args(["--exp", "EXP-T", "--variant", VARIANT, "--reps", "500", *argv]), llm,
                    runs_dir=runs, outcomes_path=outcomes, scaling_path=sc, out_root=tmp_path / "out", ledger=ledger)
    return code, ledger


def test_dry_run_picks_published_resolved_number_records_and_calls_nothing(tmp_path, capsys):
    llm = NumberLlm()
    code, _ = _run(tmp_path, ["--dry-run"], llm)
    out = capsys.readouterr().out
    assert code == 0, out
    assert llm.calls == 0
    assert "12 resolved number records picked (6 numeric, 6 discrete; 12 posts)" in out
    assert "axis from submitted.axis: 6, from the scaling file: 6" in out
    assert "skipped 1: no axis" in out and "skipped 1: annulled" in out and "skipped 1: not resolved yet" in out
    assert "24 record replays" in out and f"{24 * MEMBERS} model calls" in out


def test_scored_run_pairs_arms_and_scores_through_the_live_builder(tmp_path, capsys):
    llm = NumberLlm()
    code, ledger = _run(tmp_path, [], llm)
    out = capsys.readouterr().out
    assert code == 0, out
    assert llm.calls == 24 * MEMBERS
    prompts_b = [p for p in llm.prompts if variant.NEW_RANGE_SENTENCE in p]
    assert len(prompts_b) == 12 * MEMBERS and all(variant.OLD_RANGE_SENTENCE not in p for p in prompts_b)
    run_dir = next((tmp_path / "out" / "EXP-T").glob("*-screen"))
    import csv
    rows = list(csv.DictReader(open(run_dir / "scores.csv", encoding="utf-8")))
    assert len(rows) == 24 and all(r["ok"] == "1" for r in rows)
    a = {r["question_id"]: r for r in rows if r["arm"] == "A"}
    b = {r["question_id"]: r for r in rows if r["arm"] == "B"}
    assert all(float(b[q]["baseline"]) > float(a[q]["baseline"]) for q in a)  # narrow around the truth wins
    assert all(float(b[q]["width"]) < float(a[q]["width"]) for q in a)
    md = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "Verdict (fast rule b): GO" in md and "| numeric | 6 |" in md and "| discrete | 6 |" in md
    assert "Median 10–90 width ratio B/A: 0.250" in md
    assert len(list((run_dir / "records" / "B").glob("q*.json"))) == 12
    assert "| EXP-T |" in ledger.read_text(encoding="utf-8").splitlines()[-1]


def test_a_failed_conversion_is_counted_not_scored(tmp_path, capsys):
    class Broken:
        def build(self, axis, points):
            raise ValueError("boom")

    runs, outcomes, sc, ledger = _archive(tmp_path)
    tool = _tool()
    code = tool.run(tool.parser().parse_args(["--exp", "EXP-T", "--variant", VARIANT, "--reps", "200"]), NumberLlm(),
                    runs_dir=runs, outcomes_path=outcomes, scaling_path=sc, out_root=tmp_path / "out", ledger=ledger,
                    builder=Broken())
    assert code == 0
    md = next((tmp_path / "out" / "EXP-T").glob("*-screen")).joinpath("report.md").read_text(encoding="utf-8")
    assert "no paired questions" in md and "| A | 12 | 0 | 12 |" in md


def test_estimate_above_max_cost_is_refused(tmp_path, capsys):
    llm = NumberLlm()
    code, _ = _run(tmp_path, ["--max-cost", "0.0001"], llm)
    assert code == 2 and llm.calls == 0 and "REFUSED" in capsys.readouterr().out


def test_unknown_variant_is_refused(tmp_path, capsys):
    runs, outcomes, sc, ledger = _archive(tmp_path, n=2)
    tool = _tool()
    assert tool.run(tool.parser().parse_args(["--exp", "E", "--variant", "nope"]), NumberLlm(), runs_dir=runs,
                    outcomes_path=outcomes, scaling_path=sc, out_root=tmp_path / "o", ledger=ledger) == 2


def test_pinned_host_mismatch_refuses_the_report(tmp_path, capsys):
    llm = NumberLlm()  # names no host: every pinned call fails the check
    code, ledger = _run(tmp_path, ["--pin-hosts", "deepseek/deepseek-v4.1-flash=wafer"], llm)
    assert code == 3 and "HOST CHECK FAILED" in capsys.readouterr().out
    assert "| EXP-T |" in ledger.read_text(encoding="utf-8").splitlines()[-1]  # the spend is still booked


def test_pinned_host_match_writes_the_report(tmp_path, capsys):
    code, _ = _run(tmp_path, ["--pin-hosts", "deepseek/deepseek-v4.1-flash=wafer"], NumberLlm(host="Wafer"))
    assert code == 0, capsys.readouterr().out


@pytest.mark.parametrize("diff,lo,hi,word", [(0.5, -1.0, 2.0, "GO"), (0.5, -3.0, 4.0, "INCONCLUSIVE"),
                                             (-0.5, -1.5, 0.5, "INCONCLUSIVE"), (-2.0, -3.0, -0.5, "KILL")])
def test_fast_rule_b(diff, lo, hi, word):
    t = _tool()
    assert t.verdict(t.Paired("all", 50, 50, 0.0, diff, diff, lo, hi)).startswith(word)


def test_bootstrap_is_seeded_and_clusters_by_post():
    t = _tool()
    rows = []
    for q in range(20):
        for arm, s in (("A", 1.0), ("B", 1.0 + (q % 5) * 0.5)):
            rows.append({"question_id": q, "cluster_id": f"post:{q // 2}", "question_type": "numeric", "arm": arm, "ok": 1,
                         "baseline": s})
    p1, p2 = t.paired(rows, "all", reps=1000, seed=1015), t.paired(rows, "all", reps=1000, seed=1015)
    assert p1 == p2 and p1.n == 20 and p1.clusters == 10 and p1.mean_diff == pytest.approx(1.0)
    assert p1.ci_low < 1.0 < p1.ci_high
