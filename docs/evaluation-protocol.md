# Evaluation protocol

_Status: v0.1 draft, 2026-09-29. Owner: Christian. Changes to this protocol are made deliberately and logged at the bottom._

## 0. The rule
**No change reaches the live bot without evidence produced under this protocol.** "It looked better on a few questions"
is not evidence. An inconclusive result is a valid result, and the default is then to keep the simpler, cheaper configuration.

We invest in evaluation because it's the only thing that turns tinkering into learning. The field's own evidence (R-22,
R-25) says strict-cutoff backtests predict live results and porous ones mislead. Most bot builders never do this properly.

## 1. What backtests can and cannot answer
| Question | Backtest? | Why |
|---|---|---|
| Does a scaffolding change help (research step, prompt, aggregation, capping, calibration)? | **Yes**, with a fixed *evaluation model* | Compare configs on identical questions and, where possible, identical research |
| Does a retrieval change help (sources, filtering, agentic search)? | **Yes**, with as-of retrieval | Research is re-run under the as-of guard |
| Which *newest* model should the live bot use? | **No** | The newest models were trained on data that overlaps recent outcomes. Decided by live results (MiniBench/AIB) and public benchmarks |
| Absolute level ("are we competitive?") | **Partly** | Report edge vs. the community prediction as-of; the final arbiter is live peer score |

Consequence: backtests use **evaluation models** whose release date is safely before the question window. We assume
scaffolding effects transfer to newer models; that assumption is checked by comparing backtest and live results per config era (§9).

## 2. Threat model
Each threat has a control and an **automated check**. A control without a check doesn't count.

| # | Threat | Example | Control | Automated check |
|---|---|---|---|---|
| T1 | Model knows the outcome (knowledge cutoff is porous) | Model "remembers" an election result | The as-of date must be after the **release date** of every model in the config (not the stated cutoff) | Eligibility filter refuses ineligible (question, model) pairs; unit test with fixture dates |
| T2 | Retrieval returns future information | An article updated after the as-of date; a mis-dated page | Backtests only use date-bounded archives (AskNews `historical=True`, `end_timestamp=as_of`); **no live web search**; drop items with a missing or later publication date | Guard raises on any item dated after as-of; test injects a future-dated document and asserts it's blocked |
| T3 | Residual leakage in assembled research | A background paragraph mentions the outcome | LLM leakage screen on every research bundle; flagged questions excluded and counted | Leakage rate reported per run; warning if > 10% |
| T4 | Question text leaks | Later clarifications or resolution notes | Use the question text as of the as-of date; strip comments and resolution notes | Snapshot hash stored in the manifest; test that no resolution fields reach the prompt |
| T5 | Scaffold/prompt leaks the present | "Today is 2026-09-29" in a backtest prompt | One `Clock` provides "today" = as-of everywhere; no hard-coded dates or present-day facts in prompts | Test: render every prompt with as-of fixture, grep for other dates |
| T6 | Logical leakage through selection | Only including questions that resolved early, or selecting on outcome | Question sets are *whole tournament windows*; only questions whose scheduled resolution has passed; no outcome-based filtering | Manifest builder records inclusion/exclusion reasons; test that selection code never reads resolution |
| T7 | Piggybacking on the crowd | Research contains the Metaculus community forecast or a market price after the as-of date | No community prediction in backtest inputs; market prices only as-of-timestamped | Research guard blocks known forecast-aggregator domains unless as-of data |
| T8 | Overfitting the dev set (forking paths) | 30 prompt tweaks, keep the best | Locked HOLDOUT set; experiment card written *before* running; count experiments per DEV set | Holdout runs require `--holdout --reason`, logged in `docs/experiments/holdout-log.md` |
| T9 | Noise mistaken for signal | +0.005 Brier on 60 questions | Paired design, bootstrap CIs, repeats, a minimum-detectable-effect (MDE) statement in every report | Report generator refuses to print a "win" when the CI includes 0 |
| T10 | Scoring bugs | Wrong sign, log(0), numeric CDF off by one bin | Scorer is pure functions with golden tests against Metaculus' published examples + property tests (propriety) | CI |
| T11 | Provider drift / non-determinism | The same model changes behaviour between runs | Record model ID, provider, params, timestamps; **re-run the baseline inside every experiment** instead of comparing to old results | Runner refuses cross-run comparisons without a shared baseline |
| T12 | Metric gaming | Clamping improves log score under label noise | Report log score, Brier and calibration together; the primary metric is pre-declared | Report template |
| T13 | Harness itself is wrong | Two identical configs "differ significantly" | A/A test, leakage-injection test, trivial baselines (always 50%, base rate) | Run on every harness change |

## 3. Question sets
- **Source:** resolved questions from Metaculus bot tournaments (FutureEval seasons, MiniBench) via the API. They're the same
  distribution as the live target. Market questions (Polymarket/Manifold) are a secondary pool only, because of domain skew.
- **Manifest:** each set is a versioned file in `evals/sets/` listing question id, type, as-of timestamp (question open time),
  text snapshot hash, scheduled and actual resolution, resolution value, and exclusion reasons. The file hash identifies the set.
  Sets are immutable once used, so changes create a new version.
- **Splits:**
  - **DEV:** iterate freely.
  - **HOLDOUT:** locked. Touched only at milestone gates (e.g. the PoC gate), at most 3 touches per season, every touch logged.
  - **LIVE:** the tournament itself, the final arbiter.
- **Eligibility:** as-of > release date of every model in the config (T1); resolved and not annulled; scheduled resolution passed (T6).
- **Stratification:** report by question type (binary / MC / numeric) and domain. Split DEV/HOLDOUT stratified by type and time.
  Related questions (same series or event) go in the same split and form one cluster for statistics.

## 4. Metrics
- **Primary (pre-declared per experiment; default):** binary **log score**, reported as the Metaculus baseline score, because live ranking is log-based.
- **Always reported:** Brier score; calibration (reliability diagram, 10 bins, ECE); sharpness; MC log score; numeric
  log score (Metaculus continuous) and CRPS; **edge vs. community prediction as-of** where available; failure/abstain rate;
  **$ per question**; latency.
- **Cost is a first-class metric.** An improvement that breaks the $0.50/q cap is not an improvement for this project.

## 5. Statistics
- **Paired design:** every config in an experiment runs on the same questions, in the same time window, with the baseline re-run (T11).
- **Uncertainty:** 95% CI of the mean per-question difference via cluster bootstrap (resample question clusters, 10,000 reps).
  Also report the win rate and the median difference.
- **Repeats:** LLM outputs are stochastic. k = 1 for screening, k = 3 for decisions. Score each repeat on its own and average
  the k scores per question: that estimates the score of one live run, which is what the bot submits (one call per member).
  Averaging the forecasts first would score a k-run average the bot never submits, and because log and Brier scores are curved
  it gives the config that varies more between runs a bonus (≈ ½·Var(p)/p² per question in log score). Average the forecasts
  first only for a config that itself submits an average of k runs. Report within-config variance, and the other averaging as a
  descriptive line (`evals/report.py --repeat-mode`).
- **Power, stated honestly:** MDE ≈ 2.8 × SD(diff) / √N (α = 0.05, 80% power). Illustration with an assumed SD of 0.06 in Brier
  differences: N = 100 → MDE ≈ 0.017; N = 150 → 0.014; N = 400 → 0.008. The published Platt-scaling gain (0.016) is
  borderline detectable on our set sizes. **Many plausible tweaks will be undetectable**, so we prioritise changes with large
  expected effects and say "inconclusive" when warranted. The real SD is measured in EXP-001, and the MDE is recomputed. With
  clustered questions the √N formula is optimistic; also report the cluster-aware MDE ≈ 2.8 × the bootstrap SE.
- **Kill thresholds:** a "too small to be worth it" rule uses a fixed smallest effect of interest written into the card before
  the run (a value judgement about cost and complexity), not a fraction of an MDE measured later.
- **Multiplicity:** one primary metric per experiment. Secondary metrics are descriptive, not decision-making.

## 6. Decision rules
1. **Experiment card first.** Before running, copy `docs/experiments/TEMPLATE.md` to `docs/experiments/EXP-NNN.md` and fill in:
   hypothesis, change, primary metric, expected effect, set + N, k, cost estimate, decision rule.
2. **Adopt to live** if, on DEV: the primary-metric CI excludes 0 in favour of the change, there's no meaningful regression in
   calibration or failure rate, and cost stays within the cap.
3. **Inconclusive** → keep the simpler/cheaper config. It's allowed to re-test once with more N if the change has a strong prior.
4. **Milestone gates** (PoC gate, M3 exit) are evaluated **once** on HOLDOUT with the decision rule written beforehand.
5. **Every live config change** gets a new `CONFIG_VERSION`. Live results are compared per era.

## 7. Implementation requirements (the "bullet-proof" part)
- **Separation:** evaluation code lives in its own package (`evals/`). The bot exposes one pure entry point:
  `forecast(question_snapshot, as_of, research_bundle, config) -> ForecastRecord`. The same code path serves live and backtest.
- **As-of enforcement in code:** a `Clock`/`as_of` object is required by every retrieval and prompt-building function;
  retrieval returns only items with a verified date ≤ as_of, otherwise it raises. No function reads the system clock in backtest mode.
- **Research cache:** content-addressed by (question id, as_of, retrieval-config hash). Frozen bundles let reasoning-only
  changes be compared on identical research. Research changes are evaluated as their own experiments.
- **Run records (immutable, append-only):** run id, git SHA (refuse to run with uncommitted changes), config hash, model IDs,
  provider, params, question-set hash, per-question inputs/outputs/tokens/cost/timestamps. Stored as JSONL/Parquet and queried with DuckDB.
- **Scorer:** pure functions, golden tests vs. Metaculus' published scoring examples, property tests (the expected score is maximised
  at the true probability), edge cases (p near 0/1, numeric out-of-bounds).
- **Runner guards:** cost estimate before the run and abort above budget; holdout flag + reason + log; refusal of ineligible
  (question, model) pairs; network-free unit tests in CI.
- **Reports:** generated, never hand-edited. They go to `docs/experiments/EXP-NNN.md` (results section), and a summary line goes to
  the tracker's Results log.
- **Harness validation before first use (and after any harness change):** reproducibility (re-scoring a finished run gives identical
  numbers), an A/A test (same config twice → CI includes 0), leakage injection (a planted future document is blocked), and trivial
  baselines (always-50%, base rate) that score as expected.

## 8. Budget
Rigour costs money, and the PoC has $100. The levers:
- Research cache: pay for research once per question, then reuse it across reasoning experiments.
- Batch APIs (−50%) for backtest forecasting calls.
- The local 4090 for harness development and screening (never for final decisions about API models).
- Screen at k = 1, decide at k = 3, and only for promising changes.
- Free credits (B-01). The AskNews grant (1,000 calls/month; archive search = 5 calls) caps backtest research at ~175 archive
  queries/month after live use. Build research in monthly batches (research/03 addendum b).

Rough example: 150 DEV questions × 3 configs × k = 1 × ~$0.05 ≈ $23, plus one-off research of ~$5–15. We plan experiments against this budget.

## 9. Backtest ↔ live agreement
For each config era, compare the backtest-predicted difference to the realised live difference once ≥ 50 live questions have resolved.
Systematic disagreement means the harness is wrong (leakage or distribution mismatch) and is treated as a bug.

## 10. Open questions
- Which evaluation models (cheap, good, released before ~May 2026), and their release dates.
- Question supply: how many resolved Summer 2026 FutureEval + MiniBench questions are eligible? (Census in B-27.)
- Does the Metaculus API expose question-text history and community-prediction history as-of?
- AskNews archive reliability (dating, coverage). Measure the leakage-screen rate.
- Clustering rule for related questions.

## Sources
- Paleka et al., *Pitfalls in Evaluating Language Model Forecasters* (ICLR 2026): https://arxiv.org/abs/2506.00723
- AI Forecasting in 2026: What 11 Analyses Say: https://forum.effectivealtruism.org/posts/Spyz3wESZu2eeqhDj/ai-forecasting-in-2026-what-11-analyses-say
- AskNews news API (historical archive from 2023, start/end timestamps): https://docs.asknews.app/en/news
- nostreambot leakage screen + research cache: research/04

## Change log
- 2026-09-29: v0.1 drafted (Claude, at Christian's request).
- 2026-10-08: §5 repeats: score each repeat, then average the scores (was: average forecasts first), because the live bot
  submits one run per question; cluster-aware MDE; fixed kill thresholds. Agreed with Christian while building B-31, before
  any scored experiment run.
