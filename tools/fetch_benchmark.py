"""Fetch the Metaculus bot-benchmarking project (B-39) into the PRIVATE data repo.

Raw posts go to data/benchmark/ (data/ is its own private git repo, never the public one).
Uses METACULUS_EVAL_TOKEN (ChristianR's personal token, local reads only). Prints counts only.
  poetry run python tools/fetch_benchmark.py [--project 32979] [--details]
"""
import argparse, datetime as dt, json, os, sys, time
from pathlib import Path
import requests
from dotenv import load_dotenv

API = "https://www.metaculus.com/api"
OUT = Path(__file__).resolve().parent.parent / "data" / "benchmark"


def get(sess, url, **kw):
    for i in range(5):
        r = sess.get(url, timeout=60, **kw)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 ** i); continue
        r.raise_for_status()
        return r.json()
    r.raise_for_status()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", type=int, default=32979)
    ap.add_argument("--details", action="store_true", help="also fetch /posts/{id}/ per post")
    a = ap.parse_args()
    load_dotenv()
    sess = requests.Session()
    sess.headers["Authorization"] = f"Token {os.environ['METACULUS_EVAL_TOKEN']}"
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%MZ")
    posts, url = [], f"{API}/posts/?tournaments={a.project}&limit=100"
    while url:
        d = get(sess, url)
        if not d["results"]:
            break
        posts += d["results"]; url = d.get("next")
        print(f"\r{len(posts)} posts", end="", file=sys.stderr)
    print(file=sys.stderr)
    if a.details:
        for i, p in enumerate(posts):
            p["_detail"] = get(sess, f"{API}/posts/{p['id']}/")
            print(f"\rdetail {i+1}/{len(posts)}", end="", file=sys.stderr)
        print(file=sys.stderr)
    f = OUT / f"posts_{a.project}_{stamp}.jsonl"
    f.write_text("\n".join(json.dumps(p, ensure_ascii=False) for p in posts) + "\n", encoding="utf-8")
    print(f"{len(posts)} posts -> {f}")


if __name__ == "__main__":
    main()
