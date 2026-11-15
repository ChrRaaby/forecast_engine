# EXP-016 number check: per-value-discrete-2026-11-15 (B) vs dates-yes-live-2026-11-07 (A)

Config cheap, polarity check off, both arms replayed now on frozen research. Metric: Metaculus baseline score of the CDF the live builder would submit (higher is better), paired B − A, cluster bootstrap by post (10,000 reps, seed 1015, 95.0% CI).

**Verdict (fast rule b): INCONCLUSIVE (keep today's prompt; one rerun on questions resolved after the run date)**

| Questions | N paired | Clusters | Mean A | Mean B | Mean Δ (B − A) | 95.0% CI |
|---|---|---|---|---|---|---|
| all | 22 | 22 | +21.50 | +23.03 | +1.53 | [-5.83, +9.05] |
| numeric | 0 | 0 | +nan | +nan | +nan | [+nan, +nan] |
| discrete | 22 | 22 | +21.50 | +23.03 | +1.53 | [-5.83, +9.05] |

Median 10–90 width ratio B/A: 0.772 (n 22).

Arm A: mean probability on the true value 0.177, median 0.149; given < 2%: 0/22 (0%).
Arm B: mean probability on the true value 0.187, median 0.123; given < 2%: 0/22 (0%).

| Arm | Records | CDF built | No CDF | Parse failures (members) | Call failures (members) | Outside 10–90 (paired) | $ |
|---|---|---|---|---|---|---|---|
| A | 22 | 22 | 0 | 0 | 0 | 2/22 (9%) | $0.190 |
| B | 22 | 22 | 0 | 1 | 1 | 6/22 (27%) | $0.230 |

Spend: $0.4198 over 132 calls.

## Records not used

- more possible values than --max-values (or a continuous question): 31
- not resolved yet (or no outcome row): 19
- question type not in --types: 72

## Hosts (B-93)

Pinned: deepseek/deepseek-v4.1-flash → wafer; every call checked.

- arm A deepseek/deepseek-v4.1-flash (pinned wafer): Wafer 22
- arm A google/gemini-3.5-flash-lite: Google 22
- arm A openai/gpt-6-luna: OpenAI 22
- arm B deepseek/deepseek-v4.1-flash (pinned wafer): Wafer 21
- arm B google/gemini-3.5-flash-lite: Google 22
- arm B openai/gpt-6-luna: OpenAI 22

Manifest sha256 c71c9f654456c0e0 (22 records, manifest.txt).
