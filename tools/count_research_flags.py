"""Count B-51a research date flags over stored records (read-only, offline, $0). Output has counts and matched date phrases only,
never article text.

    poetry run python tools/count_research_flags.py [--runs-dir <path>/data/runs] [--examples 10]
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.replay import RUNS, live_records  # noqa: E402
from evals.research_cache import bundle_from_dict  # noqa: E402
from forecast_engine.research_checks import check_bundle  # noqa: E402

FLAGS = ("weekday_mismatch", "published_after_as_of", "future_weekday_date", "stale_season_year", "check_error")


def count(recs, n_examples: int = 10) -> dict:
    res = {"records": 0, "future_with_right_weekday": 0, "questions": set(), "bundles_flagged": 0, "items": 0, "items_flagged": 0,
           "by_flag": Counter(), "by_provider": defaultdict(Counter), "items_by_provider": Counter(),
           "by_config": defaultdict(Counter), "records_by_config": Counter(), "examples": defaultdict(list), "flagged_questions": defaultdict(set)}
    for _path, rec in recs:
        res["records"] += 1
        qid = (rec.get("question") or {}).get("question_id")
        res["questions"].add(qid)
        cv = rec.get("config_version", "?")
        res["records_by_config"][cv] += 1
        bundle = bundle_from_dict(rec["research"])
        res["items"] += len(bundle.items)
        for it in bundle.items:
            res["items_by_provider"][it.source] += 1
        flags = check_bundle(bundle)
        if flags:
            res["bundles_flagged"] += 1
            res["by_config"][cv]["bundles"] += 1
        for key in {f["item_index"] for f in flags}:
            res["items_flagged"] += 1
            res["by_provider"][bundle.items[key].source if 0 <= key < len(bundle.items) else "?"]["items"] += 1
        mismatched = {(f["item_index"], f["detail"]) for f in flags if f["flag"] == "weekday_mismatch"}
        for f in flags:
            if f["flag"] == "future_weekday_date" and (f["item_index"], f["detail"]) not in mismatched:
                res["future_with_right_weekday"] += 1
            res["by_flag"][f["flag"]] += 1
            res["by_provider"][f["source"]][f["flag"]] += 1
            res["by_config"][cv][f["flag"]] += 1
            res["flagged_questions"][f["flag"]].add(qid)
            if len(res["examples"][f["flag"]]) < n_examples:
                res["examples"][f["flag"]].append((qid, f["detail"]))
    return res


def report(res: dict) -> str:
    out = [f"records scanned: {res['records']} ({len(res['questions'])} unique questions), research items: {res['items']}",
           f"bundles with >=1 flag: {res['bundles_flagged']} ({res['bundles_flagged'] / max(res['records'], 1):.1%}); "
           f"items with >=1 flag: {res['items_flagged']} ({res['items_flagged'] / max(res['items'], 1):.1%})", "", "by flag (flag count / unique questions):"]
    for f in FLAGS:
        out.append(f"  {f}: {res['by_flag'][f]} / {len(res['flagged_questions'][f])}")
    out.append(f"  (future_weekday_date with a correct weekday, i.e. probably a real scheduled date: {res['future_with_right_weekday']})")
    out += ["", "by provider (items scanned; items flagged; flags):"]
    for p, n in sorted(res["items_by_provider"].items()):
        c = res["by_provider"][p]
        out.append(f"  {p}: {n}; {c['items']}; " + ", ".join(f"{f}={c[f]}" for f in FLAGS if c[f]))
    out += ["", "by config_version (records; bundles flagged; flags):"]
    for cv, n in sorted(res["records_by_config"].items()):
        c = res["by_config"][cv]
        out.append(f"  {cv}: {n}; {c['bundles']}; " + ", ".join(f"{f}={c[f]}" for f in FLAGS if c[f]))
    out += ["", "examples (question id: matched phrase):"]
    for f in FLAGS:
        for qid, d in res["examples"][f]:
            out.append(f"  {f}  {qid}: {d}")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--runs-dir", default=str(RUNS))
    ap.add_argument("--examples", type=int, default=10)
    a = ap.parse_args(argv)
    print(report(count(live_records(Path(a.runs_dir)), a.examples)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
