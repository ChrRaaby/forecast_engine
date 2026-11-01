"""EXP-008 cost and power arithmetic for the F-14 decisions (docs/research/14-exp008-decisions.md).

Pure arithmetic: no network, no keys, no records. Every input is a named field of `Inputs` (and a command-line flag of the same
name), with its source next to it. Run:

    python tools/exp008_cost.py                         # defaults (planning volume, ~50 tournament questions a week)
    python tools/exp008_cost.py --questions_per_week 80 # the measured upper end (see Inputs)
    python tools/exp008_cost.py --free_tier_unit prompt # if the billing console counts the free tier per grounded call

What it computes
1. Per arm and question: how many follow-up searches the arm makes, replaying the cap rule of
   `forecast_engine/research_followup.py` (`_run`: a search starts only if spend so far + expected cost of one more search stays
   within `max_cost_usd_per_q`; the expected cost is `search_cost_prior_usd` before the first search, then the dearest search so
   far). The number of Google search queries per search is random (measured distribution), so the script enumerates every path
   and returns exact expectations.
2. Per setting (a)/(b)/(c) (+ an extra option (d) that is not on the card): $/question per arm as the records and the cap count it
   ("booked", grounding fee at list price unless the setting books it at $0) and as Google would bill it inside the free tier,
   $/week and $/month for B + C + D, Google search queries per month for live research and for the experiment, the share of the
   free tier used, the day of the month it would run out, and the resulting Google bill.
3. Power: the minimum detectable effect (protocol §5 formula, Bonferroni for m candidate arms), the chance of each outcome of the
   card's decision rule for an assumed true effect, and the expected number of resolved binary questions by the analysis date.
"""
from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, fields, replace
from datetime import date, timedelta
from statistics import NormalDist

DAYS_PER_MONTH = 365.25 / 12
WEEKS_PER_MONTH = DAYS_PER_MONTH / 7


@dataclass(frozen=True)
class Inputs:
    # --- Google grounding -----------------------------------------------------------------------------------------------------
    # Search queries Google runs per grounded Gemini call: measured on 276 live research calls, 2026-10-03..26 (PR #13 comment):
    # 0 queries: 2 calls, 1: 55, 2: 202, 3: 17 (mean 1.85, median 2, max 3). Used for the live research's own use of the tier.
    queries_dist: tuple[tuple[int, int], ...] = ((0, 2), (1, 55), (2, 202), (3, 17))
    # The same for one follow-up search. None = the live distribution above (assumption: the narrower follow-up prompts may run
    # fewer queries; the follow-up records will show the real count once the arms run).
    followup_queries_dist: tuple[tuple[int, int], ...] | None = None
    # $ per search query after the free tier: $14 per 1,000 for Gemini 3.x (pricing page as quoted in config.py, 2026-10-26).
    grounding_usd_per_query: float = 0.014
    # Free allowance per month, shared across Gemini 3 models (pricing page, see the memo for the exact wording and its caveats).
    free_per_month: int = 5000
    # "query": the free allowance counts search queries (Google Cloud's wording). "prompt": it counts grounded calls (the AI
    # Studio pricing page's wording "5,000 prompts per month"). F-02 settles which one the console uses.
    free_tier_unit: str = "query"
    # Other grounded use on the same project/billing account (test runs, other projects). Unknown until F-02.
    other_queries_per_month: float = 0.0
    # --- One follow-up search (tokens; config.py: gemini-3.5-flash-lite at $0.30 / $2.50 per M tokens in/out) ---------------
    search_tokens_in: int = 300  # the search prompt (research_followup.SEARCH_TEMPLATE + question); card's estimate
    search_tokens_out: int = 1500  # answer + thinking; card's estimate ("~1.5k out")
    gemini_usd_per_m_in: float = 0.30
    gemini_usd_per_m_out: float = 2.50
    search_cost_prior_usd: float = 0.032  # FollowupConfig default
    # --- Critic (B and C only): openai/gpt-5-mini, ~4-8k tokens in, ~1-2k out; card: ~$0.003-0.006 -------------------------
    critic_usd: float = 0.0045
    # Gaps the critic lists per question (capped at max_gaps by the code). No measurement exists: assumption.
    gaps_per_q: int = 4
    # --- Forecasting on the arm's brief -------------------------------------------------------------------------------------
    # Cheap ensemble ~$0.01/q incl. polarity check (ADR-0009, ADR-0010). Frontier roster: $0.10-0.14/q measured on 4 replayed
    # questions (ADR-0010) → 0.12. Each follow-up search adds ~1.5k tokens of brief to every member's input: 3 cheap members at
    # ~$0.25/M in ≈ $0.001; 5 frontier members at $2/M in (config.py list prices) ≈ $0.015.
    forecast_usd_base: float = 0.010
    forecast_usd_per_search: float = 0.001
    # --- Run cap (main.py: shadow spend counts to the run's cost cap, checked before each new question) -----------------------
    run_cap_usd: float = 10.0  # TOURNAMENT_MAX_RUN_COST_USD
    live_usd_per_q: float = 0.13  # frontier live forecast (ADR-0010 $0.10-0.14) + research tokens
    cheap_shadow_usd_per_q: float = 0.01  # arm A, the existing cheap shadow
    max_questions_per_run: int = 20  # DEFAULT_MAX_QUESTIONS_PER_RUN
    # --- Volume -----------------------------------------------------------------------------------------------------------
    # Tournament questions the live bot forecasts per week. 50 = ADR-0010's ~800 per season as the card spreads it (planning
    # number). The measured upper end is ~80: 276 live Gemini research calls in 24 days (2026-10-03..26), which still includes
    # wide-mode questions until 2026-10-08 and retried questions.
    questions_per_week: float = 50.0
    live_calls_per_question: float = 1.0  # one Gemini research call per live question (research.py); retries add a little
    # --- Arms -------------------------------------------------------------------------------------------------------------
    arms: tuple[str, ...] = ("gaps", "against", "control")  # B, C, D; arm A (the cheap shadow) already runs

    @property
    def mean_queries(self) -> float:
        n = sum(c for _, c in self.queries_dist)
        return sum(q * c for q, c in self.queries_dist) / n

    @property
    def followup_dist(self) -> tuple[tuple[int, int], ...]:
        return self.followup_queries_dist if self.followup_queries_dist is not None else self.queries_dist

    @property
    def search_token_usd(self) -> float:
        return (self.search_tokens_in * self.gemini_usd_per_m_in + self.search_tokens_out * self.gemini_usd_per_m_out) / 1e6

    @property
    def questions_per_month(self) -> float:
        return self.questions_per_week * WEEKS_PER_MONTH


@dataclass(frozen=True)
class Setting:
    name: str
    label: str
    cap_usd: float  # FollowupConfig.max_cost_usd_per_q
    booked_fee_per_query: float | None  # FollowupConfig.grounding_usd_per_query; None = list price from Inputs
    max_gaps: int = 5  # FollowupConfig.max_gaps (D runs exactly this many unless the cap stops it)
    on_card: bool = True


SETTINGS: tuple[Setting, ...] = (
    Setting("a", "cap $0.06, fee counted (card default)", 0.06, None),
    Setting("b", "cap $0.20, fee counted", 0.20, None),
    Setting("c", "cap $0.06, fee booked at $0 (free tier)", 0.06, 0.0),
    Setting("d", "fee booked at $0, at most 3 searches (not on the card)", 0.06, 0.0, max_gaps=3, on_card=False),
    Setting("e", "cap $0.15, fee counted, at most 3 searches (not on the card)", 0.15, None, max_gaps=3, on_card=False),
)


@dataclass(frozen=True)
class ArmResult:
    mode: str
    searches: float  # expected follow-up searches per question
    queries: float  # expected Google search queries per question
    booked_usd: float  # expected follow-up spend per question as the record and the cap count it (critic + searches)
    real_usd_free: float  # what it really costs while the project is inside the free tier (no grounding fee)
    real_usd_list: float  # what it really costs once the free tier is used up (grounding fee at list price)
    forecast_usd: float

    @property
    def booked_total(self) -> float:
        return self.booked_usd + self.forecast_usd


def arm_expectation(inp: Inputs, s: Setting, mode: str) -> ArmResult:
    """Expected searches, queries and spend per question for one arm, replaying research_followup._run's cap rule (1 round)."""
    fee = inp.grounding_usd_per_query if s.booked_fee_per_query is None else s.booked_fee_per_query
    target = s.max_gaps if mode == "control" else min(inp.gaps_per_q, s.max_gaps)
    critic = 0.0 if mode == "control" else inp.critic_usd
    total = sum(c for _, c in inp.followup_dist)
    dist = [(q, c / total) for q, c in inp.followup_dist if c]
    tok = inp.search_token_usd

    def rec(n: int, spent: float, dearest: float | None) -> tuple[float, float, float]:
        """Expected (further searches, further queries, further booked spend) from this state."""
        if n >= target:
            return 0.0, 0.0, 0.0
        expected = inp.search_cost_prior_usd if dearest is None else dearest
        if spent + expected > s.cap_usd + 1e-12:
            return 0.0, 0.0, 0.0
        es = eq = ec = 0.0
        for q, p in dist:
            cost = tok + q * fee
            s2, q2, c2 = rec(n + 1, spent + cost, cost if dearest is None else max(dearest, cost))
            es += p * (1 + s2)
            eq += p * (q + q2)
            ec += p * (cost + c2)
        return es, eq, ec

    searches, queries, search_booked = rec(0, critic, None)
    return ArmResult(
        mode=mode, searches=searches, queries=queries, booked_usd=critic + search_booked,
        real_usd_free=critic + searches * tok, real_usd_list=critic + searches * tok + queries * inp.grounding_usd_per_query,
        forecast_usd=inp.forecast_usd_base + searches * inp.forecast_usd_per_search,
    )


@dataclass(frozen=True)
class MonthResult:
    setting: Setting
    arms: tuple[ArmResult, ...]
    booked_per_week: float
    booked_per_month: float
    live_queries: float
    exp_queries: float
    free_used_share: float  # share of the free tier the month's use takes (> 1 = over)
    live_free_used_share: float  # the same for live research (+ other use) alone
    overflow_day: float | None  # day of the month the free tier runs out (None = it lasts the month)
    google_bill: float  # Google grounding bill for the month, whole project
    google_bill_without_exp: float
    real_per_month: float  # experiment's real cost: critic + tokens + forecasts + the extra Google bill it causes
    questions_per_run_cap: int  # questions a run can start before the run cap stops it (booked cost; at most max_questions)


def month(inp: Inputs, s: Setting) -> MonthResult:
    arms = tuple(arm_expectation(inp, s, m) for m in inp.arms)
    qpm = inp.questions_per_month
    booked_q = sum(a.booked_total for a in arms)
    live_calls = qpm * inp.live_calls_per_question
    live_q = live_calls * inp.mean_queries
    exp_q = qpm * sum(a.queries for a in arms)
    exp_calls = qpm * sum(a.searches for a in arms)
    other_q = inp.other_queries_per_month
    if inp.free_tier_unit == "query":
        use, live_use = live_q + exp_q + other_q, live_q + other_q
        overflow_units = max(0.0, use - inp.free_per_month)
        overflow_queries = overflow_units
        live_overflow_queries = max(0.0, live_use - inp.free_per_month)
    elif inp.free_tier_unit == "prompt":
        other_calls = other_q / inp.mean_queries if inp.mean_queries else 0.0
        use, live_use = live_calls + exp_calls + other_calls, live_calls + other_calls
        overflow_units = max(0.0, use - inp.free_per_month)
        overflow_queries = overflow_units * inp.mean_queries  # calls past the allowance are billed per query
        live_overflow_queries = max(0.0, live_use - inp.free_per_month) * inp.mean_queries
    else:
        raise ValueError(f"free_tier_unit must be 'query' or 'prompt', not {inp.free_tier_unit!r}")
    bill = overflow_queries * inp.grounding_usd_per_query
    bill_without = live_overflow_queries * inp.grounding_usd_per_query
    real = qpm * sum(a.real_usd_free + a.forecast_usd for a in arms) + (bill - bill_without)
    per_q_run = inp.live_usd_per_q + inp.cheap_shadow_usd_per_q + booked_q
    # main.py checks `spent >= cap` before starting each question, so a question starts while the spend is still below the cap
    fit = min(inp.max_questions_per_run, math.ceil(inp.run_cap_usd / per_q_run - 1e-9))
    return MonthResult(
        setting=s, arms=arms, booked_per_week=booked_q * inp.questions_per_week, booked_per_month=booked_q * qpm,
        live_queries=live_q, exp_queries=exp_q, free_used_share=use / inp.free_per_month,
        live_free_used_share=live_use / inp.free_per_month,
        overflow_day=(inp.free_per_month / use * DAYS_PER_MONTH) if use > inp.free_per_month else None,
        google_bill=bill, google_bill_without_exp=bill_without, real_per_month=real, questions_per_run_cap=fit,
    )


# --- Power -------------------------------------------------------------------------------------------------------------------
def z(p: float) -> float:
    return NormalDist().inv_cdf(p)


def mde(sd: float, n: int, m: int = 2, alpha: float = 0.05, power: float = 0.80) -> float:
    """Protocol §5: MDE ≈ (z_{1-α/(2m)} + z_power) × SD / √N; m = 1 gives the protocol's 2.8 factor, m = 2 the card's 3.1."""
    return (z(1 - alpha / (2 * m)) + z(power)) * sd / math.sqrt(n)


def outcome_probs(effect: float, sd: float, n: int, kill: float = 0.015, m: int = 2, alpha: float = 0.05) -> dict[str, float]:
    """Chance of each outcome of the card's rule vs arm A, normal approximation, ignoring clustering and the extra test vs D
    (both make it worse). CI = mean ± h with h = z_{1-α/(2m)} × SD/√N.
      adopt: lower end >= kill (a CI that excludes 0 but whose lower end is below kill is a narrow win on the card)
      kill: upper end < kill
      narrow: lower end > 0, lower end < kill, upper end >= kill
      inconclusive: everything else (lower end <= 0 and upper end >= kill)."""
    se = sd / math.sqrt(n)
    h = z(1 - alpha / (2 * m)) * se
    cdf = NormalDist(effect, se).cdf
    p_adopt = 1 - cdf(kill + h)
    p_kill = cdf(kill - h)
    lo = max(h, kill - h)
    p_narrow = max(0.0, cdf(kill + h) - cdf(lo))
    return {"adopt": p_adopt, "narrow": p_narrow, "kill": p_kill, "inconclusive": max(0.0, 1 - p_adopt - p_narrow - p_kill)}


def n_resolved_by(switch_on: date, analysis: date, per_week: float, lag_weeks: float) -> float:
    """Resolved binary questions with records in all arms by `analysis`: questions start resolving `lag_weeks` after switch-on,
    then arrive at `per_week`."""
    weeks = (analysis - switch_on).days / 7 - lag_weeks
    return max(0.0, weeks * per_week)


def date_for_n(switch_on: date, n: int, per_week: float, lag_weeks: float) -> date:
    return switch_on + timedelta(days=round((lag_weeks + n / per_week) * 7))


# --- Output ------------------------------------------------------------------------------------------------------------------
def cost_tables(inp: Inputs, settings: tuple[Setting, ...] = SETTINGS) -> str:
    rows = [month(inp, s) for s in settings]
    out = [
        f"Inputs: {inp.questions_per_week:g} questions/week ({inp.questions_per_month:.0f}/month), "
        f"{inp.mean_queries:.2f} search queries per live Gemini call, critic lists {inp.gaps_per_q} gaps, "
        f"free tier {inp.free_per_month:,} per month counted per {inp.free_tier_unit}, "
        f"other grounded use {inp.other_queries_per_month:g} queries/month.",
        "",
        "Per question, per arm (B gaps / C against / D control):",
        "",
        "| Setting | Searches B / C / D | Booked $/q per arm B / C / D | Real $/q per arm inside free tier | Real $/q once over |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        a = r.arms
        out.append(
            f"| ({r.setting.name}) {r.setting.label} | " + " / ".join(f"{x.searches:.2f}" for x in a) + " | "
            + " / ".join(f"${x.booked_total:.3f}" for x in a) + " | "
            + " / ".join(f"${x.real_usd_free + x.forecast_usd:.3f}" for x in a) + " | "
            + " / ".join(f"${x.real_usd_list + x.forecast_usd:.3f}" for x in a) + " |")
    out += [
        "",
        "Per week and month for B + C + D together, and Google's free tier:",
        "",
        "| Setting | Booked $/week | Booked $/month | Google queries/month: live + experiment | Free tier used (live alone) "
        "| Runs out on day | Google bill/month | Real $/month for the experiment | Questions per run before the run cap |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        day = f"{r.overflow_day:.0f}" if r.overflow_day is not None else "lasts"
        out.append(
            f"| ({r.setting.name}) | ${r.booked_per_week:.2f} | ${r.booked_per_month:.2f} | "
            f"{r.live_queries:,.0f} + {r.exp_queries:,.0f} | {r.free_used_share:.0%} ({r.live_free_used_share:.0%}) | {day} | "
            f"${r.google_bill:.2f} | ${r.real_per_month:.2f} | {r.questions_per_run_cap} |")
    return "\n".join(out)


def power_tables(sds: tuple[float, ...] = (0.12, 0.20), ns: tuple[int, ...] = (100, 150, 250), m: int = 2,
                 kill: float = 0.015, effects: tuple[float, ...] = (0.0, 0.015, 0.03, 0.05), n_prob: int = 120) -> str:
    out = [f"MDE (80% power, two-sided 5% split over m = {m} arms → {1 - 0.05 / m:.1%} CI), nats of log score:", "",
           "| SD(diff), assumption | " + " | ".join(f"N = {n}" for n in ns) + " |", "|---|" + "---|" * len(ns)]
    for sd in sds:
        out.append(f"| {sd:g} | " + " | ".join(f"{mde(sd, n, m):.3f}" for n in ns) + " |")
    out += ["", f"Chance of each outcome at N = {n_prob} (normal approximation; clustering and the extra test vs D ignored):", "",
            "| SD | true gain vs A | adopt | narrow win | kill | inconclusive |", "|---|---|---|---|---|---|"]
    for sd in sds:
        for e in effects:
            p = outcome_probs(e, sd, n_prob, kill, m)
            out.append(f"| {sd:g} | {e:+.3f} | {p['adopt']:.0%} | {p['narrow']:.0%} | {p['kill']:.0%} | {p['inconclusive']:.0%} |")
    return "\n".join(out)


def _parse(argv: list[str] | None) -> Inputs:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    base = Inputs()
    for f in fields(Inputs):
        v = getattr(base, f.name)
        if isinstance(v, (int, float, str)) and not isinstance(v, bool):
            ap.add_argument(f"--{f.name}", type=type(v), default=v)
    for name in ("queries_dist", "followup_queries_dist"):
        ap.add_argument(f"--{name}", default=None, help="e.g. '0:2,1:55,2:202,3:17' (queries:count)")
    ns = ap.parse_args(argv)
    kw = {f.name: getattr(ns, f.name) for f in fields(Inputs)
          if hasattr(ns, f.name) and f.name not in ("queries_dist", "followup_queries_dist")}
    for name in ("queries_dist", "followup_queries_dist"):
        if getattr(ns, name):
            kw[name] = tuple(tuple(int(x) for x in pair.split(":")) for pair in getattr(ns, name).split(","))
    return replace(base, **kw)


def main(argv: list[str] | None = None) -> None:
    inp = _parse(argv)
    print(cost_tables(inp))
    print()
    print(power_tables())


if __name__ == "__main__":
    main()
