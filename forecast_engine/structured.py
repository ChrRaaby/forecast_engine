"""Structured-reasoning protocol for binary questions (EXP-005): the forecasting model itself works through
base rate -> scenarios -> drivers -> reconcile, as JSON; every number that can be computed is computed here, in code.

  1. Base rate: 2-3 reference classes, each with fit/misfit, a frequency and a source; either a count of comparable cases
     (k of n, shrunk in code to (k+1)/(n+2); n < 5 flagged) or an event rate that code converts to the question window
     (1 - exp(-rate * t)); weights -> p0.
  2. Scenarios (written before the drivers): 3-5 mutually exclusive, exhaustive; P(s) and P(YES|s); p_scen = sum P(s)P(YES|s).
  3. Drivers: direction, evidence ref, odds multiplier from a fixed scale; p_drv = odds(p0) * product (capped).
  4. Reconcile: if p_drv and p_scen agree (within 10 pp or a 1.5x odds factor) the code number is their log-odds mean;
     otherwise the model's own final number, with the route it trusts. Both are stored.

No "Bayesian" wording in the prompts (R-21). Experiment-only: not imported by the live bot (main.py / core.py / prompts.py).
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from textwrap import dedent
from typing import Any

# v2 (2026-10-07, after the unscored pilot, research/13; before any scored run): proportions as k of n with shrinkage, and the
# blind base-rate call sees the question background (still no research). v1 was used only by the pilot.
PROTOCOL_VERSION = "structured-v2"
SMALL_N = 5  # a proportion from fewer comparable cases is flagged

MULTIPLIERS = (1.25, 1.5, 2.0, 3.0)  # allowed odds factors per driver (inverse for "down")
TOTAL_ODDS_CAP = 10.0  # product of all drivers is clipped to [1/10, 10]
AGREE_PP = 0.10  # routes agree if within 10 percentage points ...
AGREE_ODDS = 1.5  # ... or within a 1.5x odds factor
SCENARIO_SUM_TOL = 0.05  # scenario probabilities must sum to 1 +- this (then renormalised)
P_FLOOR = 0.001  # keeps logit finite
FINAL_CLIP = (0.01, 0.99)  # same as the live binary clip


# ---- arithmetic (pure functions)
def logit(p: float) -> float:
    p = min(max(p, P_FLOOR), 1 - P_FLOOR)
    return math.log(p / (1 - p))


def sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def window_probability(events: float, per_days: float, window_days: float) -> float:
    """P(at least one event in the window) for a constant event rate (Poisson): 1 - exp(-rate * t)."""
    if events < 0 or per_days <= 0 or window_days < 0:
        raise ValueError("events >= 0, per_days > 0 and window_days >= 0 required")
    return 1 - math.exp(-(events / per_days) * window_days)


def shrunk_proportion(k: int, n: int) -> float:
    """k YES out of n comparable cases, shrunk toward 1/2: (k + 1) / (n + 2). 2 of 2 -> 0.75, 0 of 2 -> 0.25, 30 of 100 -> 0.304."""
    if not 0 <= k <= n:
        raise ValueError("0 <= k <= n required")
    return (k + 1) / (n + 2)


def apply_drivers(p0: float, factors: list[float], cap: float = TOTAL_ODDS_CAP) -> tuple[float, float]:
    """Returns (p_drv, applied total factor). The product of odds factors is clipped to [1/cap, cap]."""
    total = math.prod(factors) if factors else 1.0
    total = min(max(total, 1 / cap), cap)
    return sigmoid(logit(p0) + math.log(total)), total


def routes_agree(a: float, b: float) -> bool:
    return abs(a - b) <= AGREE_PP or abs(logit(a) - logit(b)) <= math.log(AGREE_ODDS)


def logodds_mean(a: float, b: float) -> float:
    return sigmoid((logit(a) + logit(b)) / 2)


def clip_final(p: float) -> float:
    return min(max(p, FINAL_CLIP[0]), FINAL_CLIP[1])


# ---- parsed structure
@dataclass
class RefClass:
    name: str
    fit: str
    misfit: str
    kind: str  # "proportion" | "rate"
    source: str  # "R3" (research item), "question", or "memory"
    p: float  # probability for the question window, computed in code for "rate"
    weight: float
    detail: dict[str, Any] = field(default_factory=dict)  # the raw numbers (k/n/of_what, or events/per_days/window_days)


@dataclass
class BaseRate:
    classes: list[RefClass]
    p0: float  # weighted mean of the class probabilities, computed in code
    reasoning: str
    confidence: str


@dataclass
class Scenario:
    name: str
    description: str
    p: float  # renormalised
    p_yes: float


@dataclass
class Driver:
    text: str
    direction: str  # "up" | "down"
    multiplier: float  # from MULTIPLIERS, as the model gave it (always >= 1)
    factor: float  # odds factor applied: multiplier for up, 1/multiplier for down
    evidence: str
    independence: str


@dataclass
class StructuredForecast:
    base: BaseRate | None
    scenarios: list[Scenario]
    drivers: list[Driver]
    p0: float | None
    p_scen: float | None
    p_drv: float | None
    driver_total: float | None  # odds factor actually applied (after the cap)
    agree: bool | None
    code_final: float | None  # the arm's forecast: log-odds mean if routes agree, else the model's number
    model_final: float | None  # the model's own final number, always stored
    trusted_route: str
    reconcile_reason: str
    final: float | None  # clip_final(code_final)
    problems: list[str] = field(default_factory=list)  # validation notes; the forecast may still be usable

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProtocolError(ValueError):
    """The model's answer can't be used at all (no JSON object, or no usable final number)."""


# ---- parsing and validation
def parse_json_object(text: str) -> dict[str, Any]:
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", t, flags=re.S)
    if m:
        t = m.group(1)
    elif not t.startswith("{"):
        i, j = t.find("{"), t.rfind("}")
        if i == -1 or j <= i:
            raise ProtocolError(f"no JSON object in the answer: {text[:200]}")
        t = t[i : j + 1]
    try:
        obj = json.loads(t)
    except json.JSONDecodeError as e:
        raise ProtocolError(f"invalid JSON ({e}): {t[:200]}") from e
    if not isinstance(obj, dict):
        raise ProtocolError("the JSON answer is not an object")
    return obj


def _num(x: Any) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        return None
    return float(x)


def _prob(x: Any) -> float | None:
    v = _num(x)
    if v is None:
        return None
    if 1 < v <= 100:
        v /= 100  # answered in percent
    return v if 0 <= v <= 1 else None


def _s(x: Any) -> str:
    return x.strip() if isinstance(x, str) else ""


def parse_base_rate(raw: Any, problems: list[str], max_window_days: float | None) -> BaseRate | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("reference_classes"), list):
        problems.append("base_rate: missing or not an object with reference_classes")
        return None
    classes: list[RefClass] = []
    for i, c in enumerate(raw["reference_classes"]):
        if not isinstance(c, dict):
            problems.append(f"base_rate class {i}: not an object")
            continue
        kind, w = _s(c.get("kind")), _num(c.get("weight"))
        detail: dict[str, Any] = {}
        p = None
        if kind == "proportion":
            k, n = _num(c.get("k")), _num(c.get("n"))
            detail = {"k": k, "n": n, "of_what": _s(c.get("of_what"))}
            if k is not None and n is not None and k.is_integer() and n.is_integer() and 0 <= k <= n and n >= 1:
                p = shrunk_proportion(int(k), int(n))
                detail["raw"] = k / n
                if n < SMALL_N:
                    problems.append(f"base_rate class {i}: only {int(n)} comparable cases (k of n = {int(k)}/{int(n)})")
        elif kind == "rate":
            ev, per, win = _num(c.get("events")), _num(c.get("per_days")), _num(c.get("window_days"))
            detail = {"events": ev, "per_days": per, "window_days": win}
            if ev is not None and per and per > 0 and win is not None and win >= 0:
                if max_window_days is not None and win > max_window_days + 1:
                    problems.append(f"base_rate class {i}: window_days {win:g} longer than the time to resolution "
                                    f"({max_window_days:.0f} d); capped")
                    win = max_window_days
                    detail["window_days_capped"] = win
                p = window_probability(ev, per, win)
        if p is None or w is None or w < 0:
            problems.append(f"base_rate class {i}: unusable numbers (kind={kind!r})")
            continue
        classes.append(RefClass(name=_s(c.get("name")), fit=_s(c.get("fit")), misfit=_s(c.get("misfit")), kind=kind,
                                source=_s(c.get("source")) or "memory", p=p, weight=w, detail=detail))
    if not classes:
        problems.append("base_rate: no usable reference class")
        return None
    if len(classes) < 2:
        problems.append(f"base_rate: {len(classes)} usable reference class (protocol asks for 2-3)")
    total_w = sum(c.weight for c in classes)
    if total_w <= 0:
        for c in classes:
            c.weight = 1 / len(classes)
        problems.append("base_rate: weights sum to 0; equal weights used")
    else:
        if abs(total_w - 1) > 0.01:
            problems.append(f"base_rate: weights sum to {total_w:.2f}; renormalised")
        for c in classes:
            c.weight /= total_w
    p0 = sum(c.p * c.weight for c in classes)
    return BaseRate(classes=classes, p0=p0, reasoning=_s(raw.get("reasoning")), confidence=_s(raw.get("confidence")))


def parse_scenarios(raw: Any, problems: list[str]) -> list[Scenario]:
    if not isinstance(raw, list):
        problems.append("scenarios: missing")
        return []
    out = []
    for i, s in enumerate(raw):
        p, py = (_prob(s.get("probability")), _prob(s.get("p_yes"))) if isinstance(s, dict) else (None, None)
        if p is None or py is None:
            problems.append(f"scenario {i}: unusable probability")
            continue
        out.append(Scenario(name=_s(s.get("name")), description=_s(s.get("description")), p=p, p_yes=py))
    if not 3 <= len(out) <= 5:
        problems.append(f"scenarios: {len(out)} usable (protocol asks for 3-5)")
    total = sum(s.p for s in out)
    if out and abs(total - 1) > SCENARIO_SUM_TOL:
        problems.append(f"scenarios: probabilities sum to {total:.2f}, not 1; scenario route not used")
        return out  # kept for display, but p_scen is not computed (see build)
    for s in out:
        s.p = s.p / total if total else 0.0
    return out


def parse_drivers(raw: Any, problems: list[str]) -> list[Driver]:
    if not isinstance(raw, list):
        problems.append("drivers: missing")
        return []
    out = []
    for i, d in enumerate(raw):
        if not isinstance(d, dict) or d.get("direction") not in ("up", "down"):
            problems.append(f"driver {i}: missing direction")
            continue
        m = _num(d.get("multiplier"))
        if m is not None and 0 < m < 1:
            m = 1 / m  # gave the inverse; direction carries the sign
        if m is None or not any(abs(m - k) < 1e-6 for k in MULTIPLIERS):
            problems.append(f"driver {i}: multiplier {d.get('multiplier')!r} not on the scale {MULTIPLIERS}; dropped")
            continue
        out.append(Driver(text=_s(d.get("text")), direction=d["direction"], multiplier=m,
                          factor=m if d["direction"] == "up" else 1 / m, evidence=_s(d.get("evidence")) or "none",
                          independence=_s(d.get("independence"))))
    return out


def build(raw: dict[str, Any], *, max_window_days: float | None = None, given_base: BaseRate | None = None
          ) -> StructuredForecast:
    """Validate the model's JSON and compute every number. given_base: arm C's blind base rate (overrides raw['base_rate'])."""
    problems: list[str] = []
    base = given_base if given_base is not None else parse_base_rate(raw.get("base_rate"), problems, max_window_days)
    scenarios = parse_scenarios(raw.get("scenarios"), problems)
    drivers = parse_drivers(raw.get("drivers"), problems)
    p0 = base.p0 if base else None

    total = sum(s.p for s in scenarios)
    p_scen = sum(s.p * s.p_yes for s in scenarios) if scenarios and abs(total - 1) <= 1e-6 else None
    p_drv = driver_total = None
    if p0 is not None:
        p_drv, driver_total = apply_drivers(p0, [d.factor for d in drivers])
        if driver_total != (math.prod(d.factor for d in drivers) if drivers else 1.0):
            problems.append(f"drivers: total odds factor capped at x{TOTAL_ODDS_CAP:g} either way")

    rec = raw.get("reconcile") if isinstance(raw.get("reconcile"), dict) else {}
    model_final = _prob(rec.get("final_probability"))
    agree = routes_agree(p_drv, p_scen) if p_drv is not None and p_scen is not None else None
    if agree:
        code_final = logodds_mean(p_drv, p_scen)
    else:
        code_final = model_final
        if agree is None:
            problems.append("reconcile: a route is missing; the model's final number is used")
    if code_final is None:
        raise ProtocolError("no usable final number (routes disagree or are missing, and no valid final_probability)")
    return StructuredForecast(
        base=base, scenarios=scenarios, drivers=drivers, p0=p0, p_scen=p_scen, p_drv=p_drv, driver_total=driver_total,
        agree=agree, code_final=code_final, model_final=model_final, trusted_route=_s(rec.get("trusted_route")),
        reconcile_reason=_s(rec.get("reason")), final=clip_final(code_final), problems=problems,
    )


def build_blind_base(raw: dict[str, Any], *, max_window_days: float | None = None) -> tuple[BaseRate | None, list[str]]:
    problems: list[str] = []
    return parse_base_rate(raw.get("base_rate", raw), problems, max_window_days), problems


# ---- prompts
_BASE_RATE_SPEC = """\
STEP 1 - BASE RATE (the outside view). Before using any specifics of this case, ask how often things like this happen.
Give 2-3 different reference classes (groups of comparable past cases). For each:
  - name: the class, defined precisely enough that someone could count its members;
  - fit: why this case belongs to it; misfit: how this case differs;
  - a frequency, in ONE of two forms:
      kind "proportion": k of n = in n comparable cases, k ended the way this question resolves YES (whole numbers you could
        list), and of_what = what was counted (e.g. "court appeals of this type decided within 2 weeks of the hearing,
        2015-2025"). The code turns it into (k+1)/(n+2), so small samples count for less; n under 5 is flagged;
      kind "rate": events = how many times such an event happened in per_days days (e.g. 3 events per 3650 days), and
        window_days = the length of the window this question asks about. The code converts it: P = 1 - exp(-rate x window);
  - source: "R<n>" for a research item that gives the number, "question" if the question text gives it, or "memory" if it comes
    from your own knowledge (be honest; a remembered number is fine, an invented one is not);
  - weight: how much you trust this class (weights sum to 1).
Then reasoning: why these weights, what the classes disagree on; confidence: "low", "medium" or "high".
Spend real effort here: the base rate is the anchor every later step moves from."""

_SCENARIO_SPEC = """\
STEP 2 - SCENARIOS (before the drivers). Describe 3-5 ways the next period could unfold. They must be mutually exclusive and
together cover everything that could happen. For each: name, description, probability (the chance this scenario happens; all
scenario probabilities sum to 1) and p_yes (the chance the question resolves YES if it happens). The code computes
P(YES) = sum of probability x p_yes."""

_DRIVER_SPEC = """\
STEP 3 - DRIVERS. List the specific facts about THIS case that make YES more or less likely than the base rate. For each:
text; direction "up" or "down"; multiplier: how strongly it changes the odds, chosen from 1.25 (slight), 1.5 (moderate),
2 (strong), 3 (very strong) - "down" divides the odds by it; evidence: "R<n>", "question" or "memory"; independence: one line on
why it isn't already in the base rate or in another driver. Count each fact once. Things that are true of every case in your
reference classes are not drivers. The code applies them: odds(P) = odds(base rate) x product of multipliers (capped at x10
either way)."""

_RECONCILE_SPEC = """\
STEP 4 - RECONCILE. Steps 1+3 and step 2 give two estimates. Say which you trust more and why (trusted_route: "drivers",
"scenarios" or "both"), and give your final_probability for YES (0-1). If the two estimates are close, the code averages them;
if they are far apart, your final_probability is used, so make it count."""

_JSON_SHAPE = """\
{
  "base_rate": {
    "reference_classes": [
      {"name": "...", "fit": "...", "misfit": "...", "kind": "proportion", "k": 3, "n": 25, "of_what": "...", "source": "memory", "weight": 0.6},
      {"name": "...", "fit": "...", "misfit": "...", "kind": "rate", "events": 3, "per_days": 3650, "window_days": 12, "source": "R2", "weight": 0.4}
    ],
    "reasoning": "...",
    "confidence": "medium"
  },
  "scenarios": [
    {"name": "...", "description": "...", "probability": 0.6, "p_yes": 0.05}
  ],
  "drivers": [
    {"text": "...", "direction": "down", "multiplier": 2, "evidence": "R0", "independence": "..."}
  ],
  "reconcile": {"trusted_route": "both", "reason": "...", "final_probability": 0.1}
}"""


def _clean(text: str) -> str:
    return dedent(text).strip() + "\n"


def research_block(items: list[dict[str, Any]]) -> str:
    """Research items numbered R0, R1, ... so the model can cite them."""
    if not any((it.get("text") or "").strip() for it in items):
        return "No research is available for this question."
    blocks = []
    for i, it in enumerate(items):
        head = f"[R{i}] ({it.get('source')})" + (f" {it['title']}" if it.get("title") else "")
        if it.get("published_at"):
            head += f" - {str(it['published_at'])[:10]}"
        blocks.append(f"{head}\n{(it.get('text') or '').strip()}")
    return "\n\n".join(blocks)


def _question_block(q: dict[str, Any], *, with_background: bool) -> str:
    parts = [f"Question: {q['question_text']}"]
    if with_background and q.get("background_info"):
        parts.append(f"Background:\n{q['background_info']}")
    parts.append(f"Resolution criteria (not yet satisfied):\n{q.get('resolution_criteria', '')}")
    if q.get("fine_print"):
        parts.append(f"Fine print:\n{q['fine_print']}")
    if q.get("scheduled_resolution_time"):
        parts.append(f"Scheduled resolution: {str(q['scheduled_resolution_time'])[:10]}")
    return "\n\n".join(parts)


def protocol_prompt(q: dict[str, Any], research: str, today: str) -> str:
    """Arm B: the full protocol in one call."""
    return _clean(f"""
You are a professional forecaster. Work through the four steps below in order and answer with ONE JSON object only, no other
text, in exactly this shape:
{_JSON_SHAPE}

{_question_block(q, with_background=True)}

Research (cite items as R0, R1, ...):
{research}

Today is {today}.

{_BASE_RATE_SPEC}

{_SCENARIO_SPEC}

{_DRIVER_SPEC}

{_RECONCILE_SPEC}
""")


def blind_base_rate_prompt(q: dict[str, Any], today: str) -> str:
    """Arm C, call 1: base rate from the question and its background, without the research, so it can't be fitted to the news
    but still knows where things stand (v2: v1 hid the background too, and missed the setup on q46076, research/13)."""
    shape = _JSON_SHAPE.split('"scenarios"')[0].rstrip().rstrip(",") + "\n}"
    return _clean(f"""
You are a professional forecaster doing only the first step of a forecast: the base rate. You see the question and its
background, but deliberately none of the research or recent news, so that your base rate doesn't simply echo the latest
reports. Use the background to understand where things stand, then ask how often cases like this end in YES. Answer with ONE
JSON object only, no other text, in exactly this shape:
{shape}

{_question_block(q, with_background=True)}

Today is {today}.

{_BASE_RATE_SPEC.replace('"R<n>" for a research item that gives the number, ', '')}
""")


def given_base_block(base: BaseRate) -> str:
    lines = [f"- {c.name} ({c.kind}, source {c.source}): P = {c.p:.3f}, weight {c.weight:.2f}" for c in base.classes]
    return "\n".join(lines) + f"\n  => base rate p0 = {base.p0:.3f} (confidence {base.confidence or 'n/a'}). Reasoning: {base.reasoning}"


def protocol_prompt_given_base(q: dict[str, Any], research: str, today: str, base: BaseRate) -> str:
    """Arm C, call 2: steps 2-4, starting from the blind base rate (the model may not change it)."""
    shape = '{\n  "scenarios"' + _JSON_SHAPE.split('"scenarios"', 1)[1]
    return _clean(f"""
You are a professional forecaster. A colleague who saw the question and its background, but no research, has already set the base rate below. Take it
as your starting point; do not change it. Work through steps 2-4 in order and answer with ONE JSON object only, no other text, in
exactly this shape:
{shape}

{_question_block(q, with_background=True)}

Research (cite items as R0, R1, ...):
{research}

Today is {today}.

STEP 1 - BASE RATE (given):
{given_base_block(base)}

{_SCENARIO_SPEC}

{_DRIVER_SPEC}

{_RECONCILE_SPEC}
""")
