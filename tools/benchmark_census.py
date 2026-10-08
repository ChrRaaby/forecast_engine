"""Counts-only census of the fetched benchmark posts (B-39). No outcomes or performance."""
import json, sys, collections as C
from pathlib import Path
d = Path(__file__).resolve().parent.parent / "data" / "benchmark"
f = sorted(d.glob("posts_*.jsonl"))[-1]
posts = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
qs = []
for p in posts:
    for q in ([p["question"]] if p.get("question") else []) + [x for x in (p.get("group_of_questions") or {}).get("questions", [])] + [x for k in ("conditional",) if p.get(k) for x in (p[k].get("question_yes"), p[k].get("question_no")) if x]:
        qs.append((p, q))
print(f.name, "posts", len(posts), "questions", len(qs))
print("post kinds", C.Counter("question" if p.get("question") else "group" if p.get("group_of_questions") else "conditional" if p.get("conditional") else "other" for p in posts))
print("type", C.Counter(q["type"] for _, q in qs))
print("status", C.Counter(q["status"] for _, q in qs))
print("resolution (resolved vs not)", C.Counter("resolved" if q.get("resolution") not in (None, "") else "unresolved" for _, q in qs))
for k in ("open_time", "actual_close_time", "scheduled_close_time", "actual_resolve_time", "scheduled_resolve_time"):
    v = sorted(q[k] for _, q in qs if q.get(k)); print(k, len(v), v[0][:10] if v else "", v[-1][:10] if v else "")
print("authors", C.Counter(p.get("author_username") for p, _ in qs).most_common(5))
print("aggregations populated", sum(1 for _, q in qs if (q.get("aggregations") or {}).get("recency_weighted", {}).get("history")), "latest", sum(1 for _, q in qs if (q.get("aggregations") or {}).get("recency_weighted", {}).get("latest")))
import glob
cen = [json.loads(l) for l in open(d.parent / "census" / "questions.jsonl", encoding="utf-8")]
cq = {r["question_id"] for r in cen}
print("question overlap with B-27 census", len({q["id"] for _, q in qs} & cq))
ours = set()
for g in glob.glob(str(d.parent / "forecasts" / "*" / "forecasts.jsonl")):
    ours |= {json.loads(l).get("question_id") for l in open(g, encoding="utf-8") if l.strip()}
print("question overlap with our forecast records", len({q["id"] for _, q in qs} & ours))
det = [q for p in posts for q in ([p["_detail"]["question"]] if p["_detail"].get("question") else (p["_detail"].get("group_of_questions") or {}).get("questions", []))]
print("detail questions with latest CP", sum(1 for q in det if ((q.get("aggregations") or {}).get("recency_weighted") or {}).get("latest")), "of", len(det))
