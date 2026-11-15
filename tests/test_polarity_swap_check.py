"""B-96: polarity checker replacement check. Offline; synthetic records only."""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _tool():
    spec = importlib.util.spec_from_file_location("polarity_swap_check", ROOT / "tools" / "polarity_swap_check.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["polarity_swap_check"] = mod
    spec.loader.exec_module(mod)
    return mod


def _rec(runs, run, post, members):
    d = runs / run / "data" / "forecasts" / "20261101T1000Z-tournament"
    d.mkdir(parents=True, exist_ok=True)
    q = {"question_id": post, "post_id": post, "question_type": "binary", "question_text": f"Will {post} happen?",
         "resolution_criteria": "Resolves YES if it happens."}
    (d / f"q{post}.json").write_text(json.dumps({"question": q, "forecasters": members}), encoding="utf-8")


def _check(prompt, verdict):
    return {"prompt": prompt, "output": json.dumps({"refers_to": verdict, "why": "x"})}


def test_collect_dedupes_and_adds_known_flips(tmp_path):
    mod = _tool()
    runs = tmp_path / "runs"
    _rec(runs, "1", 100, [{"model": "a", "check": _check("p1", "YES")}, {"model": "b", "check": _check("p2", "NO")}])
    _rec(runs, "2", 100, [{"model": "a", "check": _check("p1", "YES")}])  # same prompt again: one case
    _rec(runs, "3", 45898, [{"model": "google/gemini-3.5-flash-lite", "check": None,
                             "call": {"output": "... so NO is likely.\nProbability: 82%"}}])
    cases = mod.collect(runs)
    assert len(cases) == 3
    flip = [c for c in cases if c["known_flip"]]
    assert len(flip) == 1 and flip[0]["expect_flag"] and "Probability: 82%" in flip[0]["prompt"]
    assert sum(c["expect_flag"] for c in cases) == 2


def test_judge_pass_rule():
    mod = _tool()
    base = [{"id": str(i), "stored": "YES", "expect_flag": False, "known_flip": False} for i in range(200)]
    must = [{"id": "n", "stored": "NO", "expect_flag": True, "known_flip": False},
            {"id": "f", "stored": None, "expect_flag": True, "known_flip": True}]
    good = [{**r, "verdict": "YES"} for r in base] + [{**r, "verdict": "NO"} for r in must]
    good[0]["verdict"] = "NO"  # 1 false flag of 200 YES cases: within 1%
    missed = [{**r, "verdict": "YES"} for r in base] + [{**must[0], "verdict": "NO"}, {**must[1], "verdict": "YES"}]
    noisy = [{**r, "verdict": "NO" if i < 5 else "YES"} for i, r in enumerate(base)] + [{**r, "verdict": "NO"} for r in must]
    v = mod.judge([], {"good": good, "missed": missed, "noisy": noisy})
    assert v["good"]["passes"] and v["good"]["false_flags"] == 1
    assert not v["missed"]["passes"] and v["missed"]["missed"] == 1
    assert not v["noisy"]["passes"] and v["noisy"]["false_flags"] == 5
