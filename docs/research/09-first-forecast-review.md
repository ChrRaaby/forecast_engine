# 09 · First review of real forecasts (B-08)

_2026-10-05. Reviewer: Claude (Sonnet 5.5). Material: all 82 forecast records synced to `data/runs/` (5 + 3 `test_questions` runs on 2026-10-01, then
`wide` and `tournament` runs 2026-10-03 to 2026-10-05; configs `m1-baseline-2026-09-30c`, `-10-01`, `-10-03b`; 40 binary, 13 multiple choice,
16 numeric, 13 discrete). Nothing has resolved yet, so this review checks **form and reasoning, not accuracy**. No AskNews article text is quoted here._

## Verdict
**The three failure modes B-08 was written for are clean:** no unit or range errors, no wrong "today", no question treated as resolved when it was open.
Two real problems turned up, both already absorbed by the median but worth fixing before the frontier switch (ADR-0009, B-16):
Gemini's polarity slips (known, guard live but not yet seen working on a record) and unreliable dates in some AskNews summaries. **B-08 is clean enough
to unblock B-16, with the two conditions at the bottom.**

## Checks and results (facts, from the records)
| Check | Method | Result |
|---|---|---|
| "Today" in the prompt | regex `Today is YYYY-MM-DD` on every member prompt vs. record `as_of` | 82/82 match |
| Open vs. resolved | `scheduled_resolution_time` < `as_of`; resolved-language in member outputs | 0 closed questions forecast. 3 phrasings ("already concluded", "outcome essentially determined", "little time remains") all read as legitimate (voting done but not announced; retrospective question; 3 months vs. "little time" is just loose) |
| Units and ranges, 29 numeric/discrete | medians vs. the research's current level and the question bounds | No unit slips; the Rial/Toman trap (45524) was converted correctly. Medians sit within the plausible band of the stated current level in every case I could check against research (BIST, wheat, ETF flows, Zcash, Brent, VIX) |
| Parse failures | `parse_error` per member | 1 of 246 member answers: Gemini on 45442 (multiple choice with options labelled `0`, `1`, `2`, `3 or more`): "no probability found for option '0'". Median of the other two used |
| Excluded members | `n_forecasters_ok` | 1 record with 2/3 (the one above). Polarity guard not in any record yet (all are config 10-03b or older) |
| Clipped numerics | record `errors` | 1: 45524 (Toman). See below |
| Cost | `cost_usd` | $0.65 for 82 records, median $0.0076, max $0.0185 (46108, DeepSeek wrote ~5k tokens). No outliers worth action. All records `cost_complete` |
| Latency | member `latency_s` | DeepSeek median 20 s, two calls over 120 s, worst 458 s (38115). Not a correctness issue; worth a per-call timeout |

## Findings
**1. Gemini Flash-Lite polarity is a worse problem than "2/111".** The audit's two hits are questions 46080 and 46107 (posts 45898, 45925). Gemini's text says
"status quo favours No" and then gives 78% / 82%, while GPT and DeepSeek give 17-20% / 7-18%. That is 2 of the 37 binary answers Gemini gave in the audit
(5%), not 2 of 111. The median absorbed both, and the guard (`polarity.py`) now excludes such members. Two things remain unverified: the guard has not run on
a live record yet, and with a member excluded the aggregate is the mean of two, so one more slip would pass through.

**2. Gemini also misreads facts, which the median hides.** 46082 (Peru candidate homicides): Gemini 0.92, others 0.27-0.35. It counted "three homicides" in an
official election-day summary as three *candidate* homicides, which the question requires. A similar spread (0.05 / 0.42 / 0.90) on 8725 (Virginia COVID summer vs.
winter peaks) is a retrospective question that needs surveillance data none of the research provides, so all three are guessing. Both are cases where the
median is doing the work of a quality filter.

**3. AskNews summaries sometimes carry wrong dates or stale content.** 7 of 82 research bundles contain a weekday that does not match its date
(e.g. "Friday, October 4, 2026" was a Sunday; one says "Wednesday, October 6" when the record was made on October 5). One wheat bundle (46081)
describes the 2024/25 marketing year under a 2026 date. Models mostly ignored it (the wheat median sits above the bundle's price level), but a
less robust ensemble could anchor on it. Source: `asknews_latest` in every case.

**4. A single ungrounded number can steer a whole forecast (45524, USD/Toman).** The question background implies about 202,000 Toman on 24 Aug (2.02 million Rial),
the creator's range is 150,000-250,000, and Gemini's search reported 271,700 with no URL. All three members adopted 271,700 and put 60% of their mass above the
creator's range (clipped by the Metaculus limit). That may be right, but it rests on one unsourced figure and is a +35% move in six weeks. Gemini grounded
research stores no URLs (23/23 bundles `url: None`), so this cannot be audited after the fact.

**5. Low-quality sources reach the prompt.** The Zcash bundle includes an article built on a chatbot's own probability estimate, and one bundle contains
a promotional piece for a token presale. Not harmful at this scale, but it is circular for a forecaster to read another model's forecast as evidence.

**6. Disagreement is concentrated in a few questions.** Spread above 0.4 between members on 4 of 40 binary questions (46080, 46082, 46107, 8725),
three of them Gemini. Everywhere else the three models agree closely, so the ensemble gains little diversity yet (cf. B-47).

## Proposed follow-ups (not done; need Christian's OK)
1. **B-51 · research date sanity check** (Sonnet 5.5 · PC, S): flag a bundle in the record when an item has a weekday/date mismatch, a date after `as_of`, or a
   marketing/fiscal year that is not the current one; drop or mark the item. Cheap, no model call.
2. **F-11 · confirm the polarity guard on live records** (Haiku 4.5 · PC): after the first ~10 binary records under `m1-baseline-2026-10-05`, check
   `polarity` verdicts and how often a member is excluded; compare against this note's 5% Gemini rate.
3. **Ground-source retention for Gemini research** (fold into B-46 or B-51): store the grounding URLs so a single-figure claim like 45524 is auditable.
4. **DeepSeek per-call timeout** (30 s of work): one 458 s call blocks a run slot.
5. **Frontier switch (B-16):** the frontier roster will not fix items 3-5 (they are research problems), so the roster work can start in parallel.

## Conditions for treating B-08 as the gate passed
- The polarity guard is seen working on at least a few live records (F-11), or Gemini is held out of the binary median until it is.
- Christian has seen findings 3-4 and is content that B-51 can follow the switch rather than precede it.
