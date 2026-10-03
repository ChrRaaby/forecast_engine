"""Render docs/backlog.md + docs/playbook-tracker.md into one static dashboard page.

Run: python tools/build_dashboard.py  ->  dashboard/index.html
The markdown files are the source of truth; this page is a read-only view of them.
"""
import html
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "dashboard" / "index.html"


def parse(md_path):
    """Return (last_updated, sections) where sections = [(heading, [rows as dicts])]."""
    text = md_path.read_text(encoding="utf-8")
    m = re.search(r"Last updated: (\d{4}-\d{2}-\d{2})", text)
    updated = m.group(1) if m else ""
    sections, heading, table = [], None, []
    for line in text.splitlines() + [""]:
        if line.startswith("## "):
            heading = line[3:].strip()
            continue
        if line.startswith("|"):
            table.append([c.strip() for c in line.strip().strip("|").split("|")])
            continue
        if table:
            hdr, rows = table[0], [r for r in table[2:] if any(r)]
            sections.append((heading, [dict(zip(hdr, r)) for r in rows]))
            table = []
    return updated, sections


def inline(s):
    s = html.escape(s)
    s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\w*])\*([^*]+)\*(?![\w*])", r"<em>\1</em>", s)
    s = re.sub(r"(https?://[^\s<)]+)", r'<a href="\1">link</a>', s)
    s = s.replace("⚠️", '<span class="warn" title="Tension or contested evidence">!</span>')
    return s


def slug(s):
    return re.sub(r"[^a-z]+", "-", s.lower()).strip("-")


def chip(kind, value):
    v = value.strip()
    return f'<span class="chip {kind}-{slug(v.split("(")[0]) or "none"}">{html.escape(v)}</span>' if v else ""


def model_chip(value):
    """Which Claude model should do the item, and where it runs (backlog rule, 2026-10-03)."""
    v = value.strip()
    if not v:
        return ""
    tier = "frontier" if v.lower().startswith("opus") else "dev" if v.lower().startswith("sonnet") else "light"
    return f'<span class="chip md-{tier}" title="Claude model and where it runs">{html.escape(v)}</span>'


def backlog_html(sections):
    out = []
    for heading, rows in sections:
        if not rows or not heading:
            continue
        name = heading.split(":")[0].strip()
        sub = heading.split(":", 1)[1].strip() if ":" in heading else ""
        items = []
        for r in rows:
            meta = " · ".join(x for x in [r.get("Owner", ""), r.get("Size", "") and f'size {r["Size"]}'] if x)
            status = r.get("Status") or ("Done" if name == "Done" else "Parked" if name == "Parked" else "")
            why = r.get("Why") or r.get("Why parked") or r.get("Result", "")
            extra = r.get("Revisit when") or r.get("Date", "")
            items.append(f"""
      <li class="item" id="{html.escape(r.get("ID", ""))}" data-comment-target data-label="{html.escape(r.get("ID", "") + " " + r.get("Item", "")[:80])}">
        <span class="rank">{html.escape(r.get("Rank", ""))}</span>
        <div class="body">
          <div class="line"><span class="id">{html.escape(r.get("ID", ""))}</span><span class="what">{inline(r.get("Item", ""))}</span></div>
          <div class="sub">{inline(why)}{(' · ' + inline(extra)) if extra else ''}{(' · ' + html.escape(meta)) if meta else ''}</div>
        </div>
        <div class="side">{chip("st", status)}{model_chip(r.get("Model", ""))}<button type="button" class="cbtn" hidden aria-label="Comment on {html.escape(r.get("ID", ""))}">Comment</button></div>
      </li>""")
        out.append(f"""
    <section class="lane" id="lane-{slug(name)}">
      <h3>{html.escape(name)} <span class="count">{len(rows)}</span></h3>
      {f'<p class="lane-sub">{html.escape(sub)}</p>' if sub else ''}
      <ol class="items">{''.join(items)}</ol>
    </section>""")
    return "".join(out)


def tracker_html(sections):
    out, allrows = [], []
    for heading, rows in sections:
        if not rows or heading == "Results log":
            continue
        allrows += rows
        first = list(rows[0].keys())[1]
        body = "".join(f"""
        <tr id="{html.escape(r["ID"])}" data-comment-target data-label="{html.escape(r["ID"] + " " + r[first][:80])}">
          <td class="id">{html.escape(r["ID"])}<button type="button" class="cbtn" hidden aria-label="Comment on {html.escape(r["ID"])}">Comment</button></td>
          <td class="rec">{inline(r[first])}</td>
          <td>{chip("ev", r.get("Evidence", ""))}</td>
          <td>{chip("sn", r.get("Stance", ""))}</td>
          <td class="mono">{inline(r.get("Built by", ""))}</td>
          <td>{chip("st", r.get("Status", ""))}</td>
          <td class="assess">{inline(r.get("Assessment / results", ""))}</td>
        </tr>""" for r in rows)
        out.append(f"""
    <h3>{html.escape(heading)} <span class="count">{len(rows)}</span></h3>
    <div class="scroll"><table>
      <thead><tr><th>ID</th><th>{html.escape(first)}</th><th>Evidence</th><th>Stance</th><th>Built by</th><th>Status</th><th>Assessment / results</th></tr></thead>
      <tbody>{body}</tbody>
    </table></div>""")
    results = next((rows for h, rows in sections if h == "Results log"), [])
    real = [r for r in results if r.get("Date")]
    if real:
        cols = list(real[0].keys())
        res = "<div class='scroll'><table><thead><tr>" + "".join(f"<th>{html.escape(c)}</th>" for c in cols) + "</tr></thead><tbody>" + "".join(
            "<tr>" + "".join(f"<td>{inline(r[c])}</td>" for c in cols) + "</tr>" for r in real) + "</tbody></table></div>"
    else:
        res = '<p class="empty">No measured results yet. The first numbers arrive with M1 (cost per question) and M2 (backtest Brier score).</p>'
    out.append(f"<h3>Results log</h3>{res}")
    return "".join(out), allrows


def status_bar(rows):
    order = ["Done", "Partial", "Planned", "Not started", "N/A"]
    counts = {k: sum(1 for r in rows if r.get("Status", "").strip() == k) for k in order}
    total = sum(counts.values()) or 1
    segs = "".join(f'<span class="seg st-{slug(k)}" style="flex:{v}" title="{k}: {v}"></span>' for k, v in counts.items() if v)
    legend = "".join(f'<li><span class="dot st-{slug(k)}"></span>{k} <b>{v}</b></li>' for k, v in counts.items())
    return f'<div class="bar" role="img" aria-label="{", ".join(f"{k} {v}" for k, v in counts.items())} of {total}">{segs}</div><ul class="legend">{legend}</ul>'


def main():
    b_upd, b_secs = parse(ROOT / "docs" / "backlog.md")
    t_upd, t_secs = parse(ROOT / "docs" / "playbook-tracker.md")
    t_body, t_rows = tracker_html(t_secs)
    lanes = {h.split(":")[0].strip(): rows for h, rows in b_secs if h}
    open_now = [r for r in lanes.get("Now", []) if r.get("Status") != "Done"]
    nxt = open_now[0] if open_now else None
    adopt = sum(1 for r in t_rows if r.get("Stance", "").startswith("Adopt"))
    avoid = sum(1 for r in t_rows if r.get("Stance") == "Avoid")
    test = sum(1 for r in t_rows if r.get("Stance") == "Test")
    warn = sum(1 for r in t_rows if "⚠️" in r.get("Assessment / results", ""))
    page = TEMPLATE.format(
        updated=max(b_upd, t_upd), built=date.today().isoformat(),
        now_n=len(open_now), next_n=len(lanes.get("Next", [])), later_n=len(lanes.get("Later", [])),
        next_item=(f'<span class="id">{html.escape(nxt["ID"])}</span> {inline(nxt["Item"])}' if nxt else "Nothing open in Now"),
        n_recs=len(t_rows), adopt=adopt, test=test, avoid=avoid, warn=warn,
        bar=status_bar(t_rows), backlog=backlog_html(b_secs), tracker=t_body,
    )
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(page, encoding="utf-8")
    print(f"wrote {OUT}")


TEMPLATE = """<title>forecast_engine Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
/* Layout: a status header, then two scannable blocks (backlog lanes, playbook table). Cool slate neutrals, one steel-blue accent, semantic status colours. */
:root {{
  --bg:#f5f7f9; --surface:#ffffff; --fg:#17202b; --muted:#5b6674; --line:#dde3ea; --accent:#2e5f8f; --accent-soft:#e3edf7;
  --ok:#2f7d4f; --ok-soft:#e1f1e7; --warn:#a4620f; --warn-soft:#fbeedb; --bad:#b03a3a; --bad-soft:#f8e3e3; --idle:#8a95a3; --idle-soft:#eceff3;
  --display:"Bricolage Grotesque", "Segoe UI", system-ui, sans-serif; --body:"IBM Plex Sans", "Segoe UI", system-ui, sans-serif; --mono:"IBM Plex Mono", ui-monospace, Consolas, monospace;
}}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --bg:#10151c; --surface:#171e27; --fg:#e4e9ef; --muted:#9aa6b4; --line:#2a3440; --accent:#7fb1e3; --accent-soft:#1d2d3f;
  --ok:#6cc690; --ok-soft:#173226; --warn:#e8a653; --warn-soft:#3a2a14; --bad:#ec8080; --bad-soft:#3b1d1d; --idle:#7c8896; --idle-soft:#222b36; color-scheme:dark; }} }}
:root[data-theme="dark"] {{
  --bg:#10151c; --surface:#171e27; --fg:#e4e9ef; --muted:#9aa6b4; --line:#2a3440; --accent:#7fb1e3; --accent-soft:#1d2d3f;
  --ok:#6cc690; --ok-soft:#173226; --warn:#e8a653; --warn-soft:#3a2a14; --bad:#ec8080; --bad-soft:#3b1d1d; --idle:#7c8896; --idle-soft:#222b36; color-scheme:dark; }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 var(--body); }}
.wrap {{ max-width:1180px; margin:0 auto; padding-inline:clamp(16px,4vw,40px); padding-block:28px 64px; display:grid; gap:36px; }}
header {{ display:grid; gap:6px; }}
.eyebrow {{ font:500 12px var(--mono); letter-spacing:.08em; text-transform:uppercase; color:var(--muted); }}
h1 {{ font:700 clamp(28px,4vw,40px)/1.1 var(--display); margin:0; letter-spacing:-.01em; text-wrap:balance; }}
.meta {{ color:var(--muted); font-size:13px; }}
nav {{ display:flex; gap:8px; flex-wrap:wrap; }}
nav a {{ font:500 13px var(--body); color:var(--accent); background:var(--accent-soft); padding:5px 12px; border-radius:999px; text-decoration:none; }}
nav a:focus-visible, a:focus-visible {{ outline:2px solid var(--accent); outline-offset:2px; }}
.summary {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); gap:16px; }}
.panel {{ background:var(--surface); border:1px solid var(--line); border-radius:10px; padding:18px 20px; display:grid; gap:10px; align-content:start; min-width:0; }}
.panel h2 {{ font:600 13px var(--body); letter-spacing:.05em; text-transform:uppercase; color:var(--muted); margin:0; }}
.nums {{ display:flex; gap:22px; flex-wrap:wrap; font-variant-numeric:tabular-nums; }}
.nums div {{ display:grid; }} .nums b {{ font:700 28px/1 var(--display); }} .nums span {{ font-size:12px; color:var(--muted); }}
.next {{ font-size:15px; }}
.bar {{ display:flex; height:10px; border-radius:5px; overflow:hidden; background:var(--idle-soft); gap:2px; }}
.seg {{ display:block; }}
.legend {{ list-style:none; margin:0; padding:0; display:flex; flex-wrap:wrap; gap:6px 14px; font-size:12px; color:var(--muted); }}
.legend b {{ color:var(--fg); font-variant-numeric:tabular-nums; }}
.dot {{ display:inline-block; width:8px; height:8px; border-radius:2px; margin-right:5px; }}
section.block {{ display:grid; gap:14px; }}
section.block > h2 {{ font:700 24px var(--display); margin:0; }}
.lanes {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(340px,1fr)); gap:16px; align-items:start; }}
.lane {{ background:var(--surface); border:1px solid var(--line); border-radius:10px; padding:16px 18px; min-width:0; }}
.lane h3, section.block h3 {{ font:600 16px var(--display); margin:0; display:flex; align-items:center; gap:8px; }}
.lane-sub {{ margin:2px 0 0; color:var(--muted); font-size:13px; }}
.count {{ font:500 12px var(--mono); color:var(--muted); background:var(--idle-soft); border-radius:999px; padding:1px 8px; }}
.items {{ list-style:none; margin:12px 0 0; padding:0; display:grid; }}
.item {{ display:grid; grid-template-columns:22px 1fr auto; gap:10px; padding:10px 0; border-top:1px solid var(--line); align-items:start; }}
.rank {{ font:500 13px var(--mono); color:var(--muted); padding-top:1px; }}
.body {{ min-width:0; display:grid; gap:2px; }}
.line {{ display:flex; gap:8px; align-items:baseline; }}
.id {{ font:500 12px var(--mono); color:var(--accent); white-space:nowrap; }}
.what {{ font-weight:500; }}
.sub {{ font-size:12.5px; color:var(--muted); }}
.chip {{ font:500 11.5px var(--body); padding:2px 9px; border-radius:999px; white-space:nowrap; background:var(--idle-soft); color:var(--muted); }}
.md-frontier {{ background:var(--warn-soft); color:var(--warn); font-weight:600; }} .md-dev {{ background:var(--accent-soft); color:var(--accent); }} .md-light {{ background:var(--idle-soft); color:var(--muted); }}
.st-done {{ background:var(--ok-soft); color:var(--ok); }} .st-partial, .st-doing {{ background:var(--warn-soft); color:var(--warn); }}
.st-planned, .st-todo {{ background:var(--accent-soft); color:var(--accent); }} .st-blocked {{ background:var(--bad-soft); color:var(--bad); }}
.seg.st-done, .dot.st-done {{ background:var(--ok); }} .seg.st-partial, .dot.st-partial {{ background:var(--warn); }}
.seg.st-planned, .dot.st-planned {{ background:var(--accent); }} .seg.st-not-started, .dot.st-not-started {{ background:var(--idle); }} .seg.st-n-a, .dot.st-n-a {{ background:var(--line); }}
.ev-strong {{ background:var(--ok-soft); color:var(--ok); }} .ev-moderate {{ background:var(--accent-soft); color:var(--accent); }} .ev-negative {{ background:var(--bad-soft); color:var(--bad); }}
.sn-adopt, .sn-adopt-later {{ background:var(--ok-soft); color:var(--ok); }} .sn-test {{ background:var(--warn-soft); color:var(--warn); }} .sn-avoid {{ background:var(--bad-soft); color:var(--bad); }}
.scroll {{ overflow-x:auto; background:var(--surface); border:1px solid var(--line); border-radius:10px; }}
table {{ border-collapse:collapse; width:100%; min-width:900px; font-size:13.5px; }}
th {{ text-align:left; font:600 11.5px var(--body); letter-spacing:.05em; text-transform:uppercase; color:var(--muted); padding:10px 12px; border-bottom:1px solid var(--line); white-space:nowrap; }}
td {{ padding:10px 12px; border-top:1px solid var(--line); vertical-align:top; }}
tbody tr:first-child td {{ border-top:0; }}
td.rec {{ font-weight:500; max-width:300px; }} td.assess {{ color:var(--muted); max-width:380px; }} td.mono {{ font:12px var(--mono); white-space:nowrap; }}
code {{ font:12px var(--mono); background:var(--idle-soft); padding:1px 4px; border-radius:4px; }}
a {{ color:var(--accent); }}
.warn {{ display:inline-grid; place-items:center; width:16px; height:16px; border-radius:50%; background:var(--warn); color:var(--surface); font:700 11px var(--body); margin-right:4px; vertical-align:1px; }}
.empty {{ color:var(--muted); background:var(--surface); border:1px dashed var(--line); border-radius:10px; padding:14px 18px; margin:0; }}
footer {{ color:var(--muted); font-size:12px; }}
.side {{ display:grid; gap:6px; justify-items:end; }}
.cbtn {{ font:500 11.5px var(--body); color:var(--accent); background:transparent; border:1px solid var(--line); border-radius:6px; padding:2px 8px; cursor:pointer; }}
.cbtn:hover {{ background:var(--accent-soft); border-color:var(--accent); }}
.cbtn:focus-visible {{ outline:2px solid var(--accent); outline-offset:2px; }}
td.id .cbtn {{ display:block; margin-top:6px; }}
.input-note {{ font-size:13px; color:var(--muted); display:flex; gap:10px; align-items:center; flex-wrap:wrap; }}
.input-note .cbtn {{ font-size:13px; padding:4px 12px; }}
@media (max-width:520px) {{ .lanes {{ grid-template-columns:1fr; }} .item {{ grid-template-columns:18px 1fr; }} .item .side {{ grid-column:2; justify-items:start; grid-auto-flow:column; justify-content:start; }} }}
@media (prefers-reduced-motion: reduce) {{ * {{ transition:none !important; }} }}
</style>
<div class="wrap">
  <header>
    <span class="eyebrow">CrystalBallMcGee-bot · Metaculus FutureEval</span>
    <h1>forecast_engine</h1>
    <span class="meta">Source files last updated {updated} · page built {built} · read-only view of <code>docs/backlog.md</code> and <code>docs/playbook-tracker.md</code></span>
    <nav aria-label="Sections"><a href="#backlog">Backlog</a><a href="#playbook">Playbook tracker</a><a href="https://claude.ai/artifact/JPhLSguYoWVJzs3AHNJDLS">Bot monitor</a></nav>
    <p class="input-note" id="general-input" data-comment-target data-label="General input"><span>Comment on any item to give input. Claude picks comments up when you ask it to process the dashboard input, updates the repo files and replies in the thread.</span><button type="button" class="cbtn" hidden>General comment</button></p>
  </header>
  <div class="summary">
    <div class="panel"><h2>Backlog</h2>
      <div class="nums"><div><b>{now_n}</b><span>open in Now</span></div><div><b>{next_n}</b><span>Next</span></div><div><b>{later_n}</b><span>Later</span></div></div>
      <div class="next">Up next: {next_item}</div></div>
    <div class="panel"><h2>Playbook coverage</h2>
      <div class="nums"><div><b>{n_recs}</b><span>findings</span></div><div><b>{adopt}</b><span>adopt</span></div><div><b>{test}</b><span>test</span></div><div><b>{avoid}</b><span>avoid</span></div><div><b>{warn}</b><span>flagged</span></div></div>
      {bar}</div>
  </div>
  <section class="block" id="backlog"><h2>Backlog</h2><div class="lanes">{backlog}</div></section>
  <section class="block" id="playbook"><h2>Playbook tracker</h2>{tracker}</section>
  <footer>Edit the markdown files, not this page. Regenerate with <code>python tools/build_dashboard.py</code>.</footer>
</div>
<script>
(async () => {{
  const comments = window.claude && window.claude.use ? await window.claude.use("comments") : null;
  if (!comments) return;
  const buttons = document.querySelectorAll(".cbtn");
  buttons.forEach(b => {{
    b.hidden = false;
    b.addEventListener("click", async (e) => {{
      e.stopPropagation();
      const target = b.closest("[data-comment-target]");
      try {{ await comments.openComposer({{ element: target }}); }}
      catch (err) {{ if (err && err.code === "unavailable") buttons.forEach(x => x.hidden = true); }}
    }});
  }});
}})();
</script>
"""

if __name__ == "__main__":
    main()
