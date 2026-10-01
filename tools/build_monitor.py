"""Bot monitor: download every GitHub Actions run's forecast records, keep them locally, and render one HTML page.

    poetry run python tools/build_monitor.py            # sync new artifacts, then build dashboard/monitor.html
    poetry run python tools/build_monitor.py --no-sync  # rebuild from the local cache only

Artifacts on GitHub expire after 90 days; the local cache under data/runs/ (gitignored) does not. Run this at least every
couple of months so nothing expires before it is downloaded; the script warns about artifacts close to expiry.
The page is published as a private Claude artifact (see CLAUDE.md, "Monitor").
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "runs"
TEMPLATE = Path(__file__).with_name("monitor_template.html")
OUT = ROOT / "dashboard" / "monitor.html"
DEFAULT_REPO = "ChrRaaby/forecast_engine"
EXPIRY_WARN_DAYS = 14
MAX_TEXT = 12_000  # characters kept per research text / model output on the page


def gh_exe() -> str:
    found = shutil.which("gh") or r"C:\Program Files\GitHub CLI\gh.exe"
    if not Path(found).exists() and not shutil.which("gh"):
        sys.exit("GitHub CLI not found; install it or pass --no-sync")
    return found


def gh_json(args: list[str]):
    out = subprocess.run([gh_exe(), *args], capture_output=True, text=True, encoding="utf-8", check=True).stdout
    return json.loads(out)


def sync(repo: str) -> list[str]:
    """Download artifacts not yet in the cache. Returns warnings."""
    warnings: list[str] = []
    CACHE.mkdir(parents=True, exist_ok=True)
    page, artifacts = 1, []
    while True:
        batch = gh_json(["api", f"repos/{repo}/actions/artifacts?per_page=100&page={page}"])["artifacts"]
        artifacts += batch
        if len(batch) < 100:
            break
        page += 1
    now = datetime.now(timezone.utc)
    for a in artifacts:
        if not a["name"].startswith("forecasts-"):
            continue
        run_id = str(a["workflow_run"]["id"])
        dest = CACHE / run_id
        if (dest / "_run.json").exists():
            continue
        if a.get("expired"):
            warnings.append(f"artifact {a['name']} expired before it was downloaded; its records are lost")
            continue
        expires = datetime.fromisoformat(a["expires_at"].replace("Z", "+00:00"))
        if expires - now < timedelta(days=EXPIRY_WARN_DAYS):
            warnings.append(f"artifact {a['name']} expires {expires:%Y-%m-%d}; downloading now")
        dest.mkdir(parents=True, exist_ok=True)
        subprocess.run([gh_exe(), "run", "download", run_id, "-R", repo, "-n", a["name"], "-D", str(dest)], check=True)
        run = gh_json(["run", "view", run_id, "-R", repo, "--json", "workflowName,createdAt,event,conclusion,url"])
        (dest / "_run.json").write_text(json.dumps(run, indent=2), encoding="utf-8")
        print(f"downloaded {a['name']} ({run['workflowName']}, {run['createdAt']})")
    return warnings


def _clip(text: str | None) -> str:
    text = text or ""
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "\n\n[... trimmed for the monitor; full text in the run artifact]"


def load_rows() -> tuple[list[dict], list[dict]]:
    rows, runs = [], []
    for run_dir in sorted(CACHE.glob("*")):
        meta_path = run_dir / "_run.json"
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        n, cost = 0, 0.0
        for qfile in sorted(run_dir.rglob("q*.json")):
            r = json.loads(qfile.read_text(encoding="utf-8"))
            q = r["question"]
            mode = "test" if "test_questions" in qfile.parent.name else "tournament"
            members = []
            for f in r["forecasters"]:
                c = f["call"]
                members.append({
                    "model": f["model"], "pred": f["prediction"], "error": c.get("error") or f.get("parse_error"),
                    "cost": c.get("cost_usd"), "tin": c.get("tokens_in"), "tout": c.get("tokens_out"),
                    "output": _clip(c.get("output")),
                })
            research_calls = r["research"]["calls"]
            items = r["research"]["items"]
            rows.append({
                "qid": q["question_id"], "post": q["post_id"], "title": q["question_text"], "url": q["page_url"],
                "type": q["question_type"], "options": q.get("options") or [],
                "tournaments": q.get("tournaments") or [], "mode": mode,
                "run": run_dir.name, "run_url": meta.get("url"), "workflow": meta.get("workflowName"),
                "at": r["as_of"], "status": r.get("status", "ok"), "published": r.get("published", False),
                "aggregate": r["aggregate"], "members": members,
                "research": _clip("\n\n".join(i["text"] for i in items)),
                "providers": r["research"]["providers"],
                "research_errors": r["research"].get("errors", []),
                "research_cost": sum(c.get("cost_usd") or 0 for c in research_calls),
                "cost": r["cost_usd"], "cost_complete": r.get("cost_complete", True),
                "config": r["config_version"], "errors": r.get("errors", []),
            })
            n += 1
            cost += r["cost_usd"]
        runs.append({"run": run_dir.name, "at": meta.get("createdAt"), "workflow": meta.get("workflowName"),
                     "conclusion": meta.get("conclusion"), "url": meta.get("url"), "questions": n, "cost": cost})
    rows.sort(key=lambda x: x["at"], reverse=True)
    return rows, runs


def openrouter_usage() -> dict | None:
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        return None
    try:
        d = requests.get("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"}, timeout=20).json()["data"]
        return {"usage": d.get("usage"), "limit": d.get("limit")}
    except Exception as e:  # monitor still builds without it
        print(f"warning: could not read OpenRouter usage: {e}")
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--no-sync", action="store_true")
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")
    warnings = [] if args.no_sync else sync(args.repo)
    rows, runs = load_rows()
    data = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "repo": args.repo,
        "openrouter": openrouter_usage(),
        "warnings": warnings,
        "rows": rows,
        "runs": runs,
    }
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", blob)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    for w in warnings:
        print(f"WARNING: {w}")
    print(f"wrote {OUT} ({len(rows)} forecasts from {len(runs)} runs, {OUT.stat().st_size / 1e6:.2f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
