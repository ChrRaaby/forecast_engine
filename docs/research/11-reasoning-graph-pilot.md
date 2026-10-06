# 11: Reasoning-graph pilot (B-46), 10 binary records (2026-10-06)

_For Christian's review before any backfill. Offline only: nothing here touches the live bot._

**Question:** does a cheap extractor (Gemini 3.5 Flash-Lite, JSON output) turn each member's written rationale into a usable graph
(base rate → drivers → evidence → scenarios → final), and do the honesty checks in code hold up? What does the graph show that the
numbers don't?

**Setup.** `tools/extract_graphs.py --limit 10` on the 10 stored binary records with the widest member spread (9 live records from
data/runs, one local dry run, q43330, which has no research). 30 extractor calls, prompt `graph-extract-v2`, temperature 0, thinking
"low". Code: `forecast_engine/graph.py` (schema, quote check, tracing, features), `evals/graph_extract.py` (prompt, Gemini call).
Graphs are cached under `data/graphs/` (mirrors `data/runs/`; records stay untouched). Selection is biased to disagreement on
purpose, so the rates below are not representative of all questions.

**Cost.** $0.094 for the 30 calls ($0.0031 per member, $0.0094 per question; tokens at list price, no thinking tokens were used),
plus $0.012 for a first trial run on q46107 with prompt v1: $0.105 in all. That is
about 9× the backlog's "~$0.001/q" guess. The tool's pre-run estimate ($0.22) is deliberately conservative (it assumes 2,500 output
tokens; actual ~1,000). A backfill of all 66 stored binary records would cost about $0.60; running it after every live forecast would add
about $0.01 per binary question.

## Per question

GPT = gpt-6-luna, Gem = gemini-3.5-flash-lite, DS = deepseek-v4.1-flash. "Untraced" = evidence the member cites that code could not
match to any research item (n untraced / n evidence, all members). "Dropped" = nodes removed by the quote check. "Mismatch" = members
whose drivers and evidence clearly point one way while their number sits clearly on the other side of 50%.

| Question | Members | Base rates | Untraced | Dropped | Mismatch | Cost |
|---|---|---|---|---|---|---|
| q8725 Virginia COVID summer vs winter peak | GPT 0.42 / Gem 0.05 / DS 0.90 | none / none / none | 0/2 | 0 | – | $0.0059 |
| q46107 Bolivia decree 5716 abrogated | GPT 0.18 / Gem 0.82 / DS 0.07 | none / none / none | 6/7 | 0 | **Gem** | $0.0114 |
| q46082 Peru ERM candidate homicides > 2 | GPT 0.35 / Gem 0.92 / DS 0.27 | "baseline favors No" (no number) / none / none | 0/5 | 0 | – | $0.0096 |
| q46122 Texas appeal of prison AC injunction | GPT 0.25 / Gem 0.82 / DS 0.20 | none / none / none | 2/4 | 0 | – | $0.0096 |
| q46080 Texas DPS voter-registration lawsuit | GPT 0.20 / Gem 0.78 / DS 0.17 | none / none / none | 1/4 | 0 | **Gem** | $0.0099 |
| q46113 Rada extends martial law Oct 15–17 | GPT 0.55 / Gem 0.85 / DS 0.40 | none / none / none | 2/3 | 0 | – | $0.0087 |
| q46083 Belfast Court of Appeal judgment | GPT 0.65 / Gem 0.35 / DS 0.40 | none / none / none | 2/5 | 0 | – | $0.0106 |
| q43330 US president net worth ≥ 4× (dry run, no research) | GPT 0.35 / Gem 0.62 / DS 0.33 | none / none / "quadrennial wealth quadrupling" (no number) | 5/5 | 0 | – | $0.0073 |
| q46109 MSCI makes Strategy (MSTR) ineligible | GPT 0.40 / Gem 0.60 / DS 0.64 | none / none / none | 2/6 | 0 | – | $0.0110 |
| q46076 India SC ≥ 3 women judges | GPT 0.45 / Gem 0.42 / DS 0.65 | none / none / none | 1/6 | 0 | – | $0.0097 |
| **Total** | 30 members | 2 stated, 0 with a number | 21/47 | 0 | 2 | **$0.094** |

Top drivers (strongest two per member; ↑/↓ = pushes YES up/down, 1–3 = strength):

- **q8725.** GPT: ↑2 summer waves can be substantial; ↓2 winter peaks have often been stronger. Gem: ↓3 winter seasonality in
  temperate regions; ↓3 summer peaks very rarely exceed winter peaks. DS: ↑3 "less winter dominance in recent years, summer 2025 was a
  significant wave". Same facts, opposite readings, and none of them traced to research (the bundle has one item and no surveillance data).
- **q46107.** All three: ↓3 the government says the decree stays / IMF program; ↓2 state of exception. Gem's drivers are all ↓, yet it
  answered 82%.
- **q46082.** GPT: ↓3 victims not confirmed as candidates; ↑2 three homicides under investigation. Gem: ↑3 "Ministerio Público reports
  three candidate/electoral homicides". DS: ↑2 violent electoral environment. The crux (are the three victims *candidates*?) is
  visible as two opposite readings of the same item.
- **q46122.** GPT/DS: ↓2 Texas has until late November to file. Gem: ↑3 "the AG's office typically moves rapidly" (background, untraced).
- **q46080.** GPT: ↓3 status quo; ↓2 short window. Gem: ↓3 10-day window makes a filing unlikely, then 78%. DS: ↓2 backlog cleared; ↑2
  salient controversy.
- **q46113.** All: ↑ unbroken 90-day renewal precedent; GPT/DS ↓2 crowded agenda, vote may fall on another date. Gem weights the
  timing risk at 1.
- **q46083.** GPT: ↑2 urgent, court has ruled quickly before. Gem: ↓3 such judgments are usually reserved for weeks. DS: both at 2.
- **q43330.** GPT: ↓3 assets stay near current value; Gem/DS: ↑3 volatile DJT/crypto holdings.
- **q46109.** GPT: ↓2 MSCI backed off before. Gem: ↑3 index providers generally proceed. DS: ↑3 MSCI's own simulation named Strategy;
  ↓3 pushback and softening risk.
- **q46076.** GPT: ↑3 Sept 28 Collegium recommendation; ↓2 no notification yet. Gem: the two scenarios restated as drivers. DS: ↑2
  normal but uncertain timeline.

## Findings (facts from the pilot)

1. **The direction check found both known YES/NO inversions and nothing else.** Two of 30 members are flagged: Gemini on q46107 (all
   drivers ↓, answer 82%) and q46080 (all ↓, 78%). These are exactly the two hits of the polarity audit (research/09 §1), found here with
   no extra model call. The other 28 members, including four whose push and number disagree only weakly, are not flagged. The thresholds
   (net push ≥ 3, number ≥ 15 points from 50%) were set on these same 30 members, so this is an in-sample result. It's a candidate
   cross-check for the polarity guard (F-11), not a validated detector.
2. **Nobody states a base rate.** 2 of 30 members name something base-rate-like ("the baseline favors No"), and none gives a number. The
   template prompt asks for the status quo, not a reference class (research/10). So `base_rate`, `shift_from_base` and B-57's "how far
   did the forecast move from its anchor" are empty on today's records. This is evidence for EXP-006, not a defect of the extractor.
3. **The graphs are small and shaped by the template.** On average 2.2 drivers and 1.6 evidence nodes per member. Every member has
   exactly 2 scenarios, the prompt's (c) and (d), and none of them has a probability. Several drivers are the scenarios restated (q46076
   Gem, q43330 GPT). The graph shows what the prompt asks for, and the prompt asks for little.
4. **Tracing has to be done in code, and it's coarse.** The extractor almost never attributes a source: its `research_item` was null for
   46 of 47 evidence nodes, and members cited a source by name once. Code matched 26/47 by word overlap; 21 are untraced (5 of them in the
   dry run with no research). Of the 26 matches, 20 land on the long Gemini-grounded summary, because a long item shares words with
   almost anything. So "untraced" is a usable signal ("this claim isn't in the research": background knowledge or invention), but "which
   item" is weak until Gemini research keeps its grounding URLs (research/09 follow-up 3).
5. **The quote check rarely fires.** 0 nodes dropped in the 30 calls above. The first trial run on q46107 (prompt v1, since replaced)
   dropped 3 of DeepSeek's nodes: the extractor had written "The scenario resulting in NO" for the member's "(c) Scenario resulting in
   NO", and paraphrased one driver. So the check works, and at temperature 0 the extractor mostly copies faithfully. No malformed JSON
   and no API errors in 33 calls.
6. **Where members disagree, the graph shows why.** q46082 and q8725 show the disagreement as opposite readings of the same fact. On
   q46122 and q46083 it is a background belief that only one member holds ("the AG moves fast", "judgments are reserved for weeks"),
   untraced to research. That is the X-ray view's use: a reader sees the crux in one line instead of reading three rationales.

## Judgement
- **Worth keeping as inspection**, mainly for findings 1 and 6. The direction check is the most useful output so far, and it comes free
  with the graph.
- **Not yet useful for EXP-005 step 0 on its own.** With no base rates and ~2 evidence nodes per member, the features have little
  variance. The features worth testing once outcomes arrive are untraced share, net push / mismatch and driver overlap between members
  (not built yet; needs a semantic match, probably one more cheap call per question).
- **The live graph-first arm will differ from these inspection graphs by construction.** Inspection graphs describe the template's
  (a)–(d) answer. A graph-first prompt asks for base rate, drivers and evidence explicitly. Comparing the two is still meaningful
  because both use the same schema, but expect the graph-first graphs to be much richer.

## Suggested next steps (need Christian's OK)
1. Backfill the remaining stored binary records (~56 records, ~$0.55).
2. Add the direction check to F-11: compare it with the polarity guard's verdicts on the first `m1-baseline-2026-10-05` records.
3. Keep Gemini grounding URLs in research items (research/09 follow-up 3), so tracing can name the article.
4. Then the monitor views (side-by-side graphs, Question X-ray), as planned in B-46.
