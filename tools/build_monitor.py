"""Bot monitor: download every GitHub Actions run's forecast records, keep them locally, and render one HTML page.

    poetry run python tools/build_monitor.py            # sync new artifacts, then build dashboard/monitor.html
    poetry run python tools/build_monitor.py --no-sync  # rebuild from the local cache only
    poetry run python tools/build_monitor.py --sync-only  # just archive new runs (what the daily Windows task runs)

Artifacts on GitHub expire after 90 days; the local cache under data/runs/ (gitignored here) does not, and data/ is itself a
git repo that is pushed to the private ChrRaaby/forecast_engine_data after every sync. Run this at least every
couple of months so nothing expires before it is downloaded; the script warns about artifacts close to expiry.
The page is published as a private Claude artifact (see CLAUDE.md, "Monitor").
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
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


def openssl_exe() -> str:
    for cand in (shutil.which("openssl"), r"C:\Program Files\Git\usr\bin\openssl.exe", r"C:\Program Files\Git\mingw64\bin\openssl.exe"):
        if cand and Path(cand).exists():
            return cand
    sys.exit("openssl not found (it ships with Git for Windows)")


def decrypt_records(dest: Path) -> None:
    """Artifacts hold records.tar.gz.enc (see the workflows). Decrypt with ARCHIVE_KEY and unpack into dest."""
    enc = dest / "records.tar.gz.enc"
    if not enc.exists():
        return  # older, unencrypted artifact
    key = os.getenv("ARCHIVE_KEY")
    if not key:
        sys.exit("ARCHIVE_KEY is not set in .env; cannot decrypt run records")
    plain = subprocess.run(
        [openssl_exe(), "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "200000", "-pass", "env:ARCHIVE_KEY", "-in", str(enc)],
        capture_output=True, check=True, env={**os.environ, "ARCHIVE_KEY": key},
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(plain), mode="r:gz") as tar:
        tar.extractall(dest, filter="data")
    enc.unlink()


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
        decrypt_records(dest)
        run = gh_json(["run", "view", run_id, "-R", repo, "--json", "workflowName,createdAt,event,conclusion,url"])
        # Run logs expire with the artifact, so keep them too.
        log = subprocess.run([gh_exe(), "run", "view", run_id, "-R", repo, "--log"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        (dest / "_run.log").write_text(log.stdout, encoding="utf-8")
        (dest / "_run.json").write_text(json.dumps(run, indent=2), encoding="utf-8")
        print(f"downloaded {a['name']} ({run['workflowName']}, {run['createdAt']})")
    return warnings


def push_archive() -> str:
    """Commit and push data/ to the private archive repo (ChrRaaby/forecast_engine_data), if it is one."""
    data = ROOT / "data"
    if not (data / ".git").exists():
        return "data/ is not a git repo; archive not pushed"
    git = ["git", "-C", str(data), "-c", "user.name=forecast_engine archiver", "-c", "user.email=archiver@localhost"]
    subprocess.run([*git, "add", "-A"], check=True)
    if subprocess.run([*git, "diff", "--cached", "--quiet"]).returncode == 0:
        return "archive up to date"
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    subprocess.run([*git, "commit", "-q", "-m", f"Archive sync {stamp}"], check=True)
    subprocess.run([*git, "push", "-q", "origin", "main"], check=True)
    return "archive pushed"


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
            name = qfile.parent.name
            mode = "test" if "test_questions" in name else "wide" if "-wide" in name else "tournament"
            members = []
            for f in r["forecasters"]:
                c = f["call"]
                members.append({
                    "model": f["model"], "pred": None if f.get("excluded") else f["prediction"],
                    "error": f.get("excluded") or c.get("error") or f.get("parse_error"),
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
                "asknews_calls": sum((c.get("extra") or {}).get("asknews_calls", 0) for c in research_calls),
                "cost": r["cost_usd"], "cost_complete": r.get("cost_complete", True),
                "config": r["config_version"], "errors": r.get("errors", []), "flags": r.get("flags", []),
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
    ap.add_argument("--sync-only", action="store_true", help="download new run artifacts and logs, don't build the page")
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")
    warnings = [] if args.no_sync else sync(args.repo)
    if not args.no_sync:
        try:
            print(push_archive())
        except subprocess.CalledProcessError as e:
            warnings.append(f"pushing the data archive failed: {e}")
    if args.sync_only:
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with (CACHE / "_sync.log").open("a", encoding="utf-8") as f:
            archived = len(list(CACHE.glob("*/_run.json")))
            f.write(f"{stamp} ok {archived} runs archived" + "".join(f" | WARNING {w}" for w in warnings) + "\n")
        for w in warnings:
            print(f"WARNING: {w}")
        return 0
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
