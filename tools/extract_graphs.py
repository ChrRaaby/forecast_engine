"""Extract reasoning graphs (B-46) from stored binary forecast records. Offline; doesn't change forecasts.

    poetry run python tools/extract_graphs.py --estimate                 # what it would cost, no calls
    poetry run python tools/extract_graphs.py --limit 10                 # widest member spread first
    poetry run python tools/extract_graphs.py --question 46107 46122     # specific questions (every record of them)

Records under data/runs and data/forecasts are write-once archives, so graphs go to a parallel cache that mirrors their paths:
data/runs/<run>/.../q46107.json -> data/graphs/runs/<run>/.../q46107.graph.json. Records with a graph from the current extractor
prompt version are skipped (--force redoes them; --redo-errors redoes graphs where a member failed).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from evals.graph_extract import ExtractorConfig, binary_members, estimate_cost, extract_record, extractor_prompt  # noqa: E402

DATA = ROOT / "data"
CACHE = DATA / "graphs"


def graph_path(record_path: Path) -> Path:
    rel = record_path.resolve().relative_to(DATA.resolve())
    return CACHE / rel.with_name(rel.stem + ".graph.json")


def find_records(roots: list[Path]) -> list[Path]:
    out = []
    for root in roots:
        out += [p for p in root.rglob("q*.json") if not p.name.endswith(".graph.json") and p.stem[1:].isdigit()]
    return sorted(out)


def spread(record: dict) -> float:
    preds = [m["prediction"] for m in binary_members(record)]
    return max(preds) - min(preds) if len(preds) > 1 else 0.0


def is_done(path: Path, cfg: ExtractorConfig, redo_errors: bool) -> bool:
    if not path.exists():
        return False
    g = json.loads(path.read_text(encoding="utf-8"))
    if g.get("extractor", {}).get("prompt_version") != cfg.prompt_version:
        return False
    return not (redo_errors and g.get("errors"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--roots", nargs="*", type=Path, default=[DATA / "runs", DATA / "forecasts"])
    ap.add_argument("--question", nargs="*", type=int, help="only these question ids")
    ap.add_argument("--limit", type=int, help="at most this many records")
    ap.add_argument("--order", choices=["spread", "path"], default="spread", help="widest member spread first (default)")
    ap.add_argument("--estimate", action="store_true", help="print the cost estimate and stop")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--redo-errors", action="store_true")
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")
    cfg = ExtractorConfig()

    todo: list[tuple[float, Path, dict]] = []
    for p in find_records(args.roots):
        rec = json.loads(p.read_text(encoding="utf-8"))
        if not binary_members(rec):
            continue
        if args.question and rec["question"]["question_id"] not in args.question:
            continue
        if not args.force and is_done(graph_path(p), cfg, args.redo_errors):
            continue
        todo.append((spread(rec), p, rec))
    if args.order == "spread":
        todo.sort(key=lambda t: -t[0])
    if args.limit is not None:
        todo = todo[: args.limit]

    est = sum(estimate_cost(extractor_prompt(r["question"]["question_text"], m["call"]["output"], r["research"]["items"],
                                             cfg.max_item_chars), cfg)
              for _, _, r in todo for m in binary_members(r))
    n_calls = sum(len(binary_members(r)) for _, _, r in todo)
    print(f"{len(todo)} records, {n_calls} extractor calls, estimated ${est:.3f} ({cfg.model}, {cfg.prompt_version})")
    if args.estimate or not todo:
        return 0

    total = 0.0
    for i, (sp, p, rec) in enumerate(todo, 1):
        rel = p.resolve().relative_to(DATA.resolve()).as_posix()
        out = extract_record(rec, rel, cfg)
        dest = graph_path(p)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
        total += out["cost_usd"]
        dropped = sum((m["graph"] or {}).get("checks", {}).get("n_dropped", 0) for m in out["members"])
        print(f"[{i}/{len(todo)}] q{out['question_id']} spread {sp:.2f}  ${out['cost_usd']:.4f}  dropped {dropped}"
              f"  errors {len(out['errors'])}  -> {dest.relative_to(ROOT).as_posix()}")
        for e in out["errors"]:
            print(f"    {e}")
    print(f"done: ${total:.4f} (estimated from tokens at list price)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
