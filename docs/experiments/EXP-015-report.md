# EXP-015 number check: range-calibrated-2026-11-14 (B) vs dates-yes-live-2026-11-07 (A)

Config cheap, polarity check off, both arms replayed now on frozen research. Metric: Metaculus baseline score of the CDF the live builder would submit (higher is better), paired B − A, cluster bootstrap by post (10,000 reps, seed 1015, 95.0% CI).

**Verdict (fast rule b): INCONCLUSIVE (keep today's prompt; one rerun on questions resolved after the run date)**

| Questions | N paired | Clusters | Mean A | Mean B | Mean Δ (B − A) | 95.0% CI |
|---|---|---|---|---|---|---|
| all | 102 | 102 | +26.93 | +26.57 | -0.36 | [-5.36, +4.14] |
| numeric | 49 | 49 | +27.64 | +29.03 | +1.39 | [-5.59, +7.23] |
| discrete | 53 | 53 | +26.28 | +24.31 | -1.97 | [-9.21, +4.38] |

Median 10–90 width ratio B/A: 0.833 (n 102).

Arm A: mean probability on the true value 0.060, median 0.014; given < 2%: 58/102 (57%).
Arm B: mean probability on the true value 0.059, median 0.015; given < 2%: 60/102 (59%).

| Arm | Records | CDF built | No CDF | Parse failures (members) | Call failures (members) | Outside 10–90 (paired) | $ |
|---|---|---|---|---|---|---|---|
| A | 102 | 102 | 0 | 0 | 0 | 12/102 (12%) | $0.812 |
| B | 102 | 102 | 0 | 0 | 1 | 16/102 (16%) | $0.858 |

Spend: $1.6698 over 612 calls.

## Records not used

- not resolved yet (or no outcome row): 42

## Hosts (B-93)

Pinned: deepseek/deepseek-v4.1-flash → wafer; every call checked.

- arm A deepseek/deepseek-v4.1-flash (pinned wafer): Wafer 102
- arm A google/gemini-3.5-flash-lite: Google 102
- arm A openai/gpt-6-luna: OpenAI 102
- arm B deepseek/deepseek-v4.1-flash (pinned wafer): Wafer 101
- arm B google/gemini-3.5-flash-lite: Google 102
- arm B openai/gpt-6-luna: OpenAI 102

Manifest sha256 415905627405234d (102 records, manifest.txt).
