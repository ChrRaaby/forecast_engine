"""Reasoning graph of one forecaster's answer (B-46; extends the B-20 record schema). Binary questions only for now.

base rate -> drivers (cruxes) -> evidence (claim, source, direction, strength) -> scenarios -> final probability.
Every node carries `quote`, the exact sentence from the forecaster's text it was read from.

The graph is produced by a model (offline extractor in evals/graph_extract.py; the graph-first arm of EXP-005 later), so nothing
in it is trusted until the checks here have run:
  - quote check: a node whose quote is not a substring of the forecaster's text is dropped and counted;
  - tracing: which research item an evidence node came from is decided in code (URL, then title, then text overlap), not by the
    model; evidence that matches no item is "untraced";
  - the final probability always comes from the forecast record, never from the extractor.
Not imported by the live bot.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

GRAPH_SCHEMA_VERSION = "b46-graph-v1"

Direction = Literal["up", "down"]  # pushes P(YES) up or down
TraceMethod = Literal["url", "title", "text"]

# Text-overlap tracing: share of an evidence claim's content words found in a research item, and the minimum number of shared
# words. Set on the B-46 pilot (docs/research/11-reasoning-graph-pilot.md); a heuristic, so the method and score are stored.
TRACE_MIN_SHARE = 0.6
TRACE_MIN_WORDS = 3
# Direction-consistency flag (graph_features): net signed strength at least this, and the final at least this far from 50%.
MISMATCH_MIN_PUSH = 3
MISMATCH_MIN_MARGIN = 0.15


@dataclass
class BaseRate:
    status: Literal["stated", "none"]
    reference_class: str = ""
    value: float | None = None  # probability in [0, 1], if the forecaster gave a number
    quote: str = ""


@dataclass
class Driver:
    id: str
    text: str
    direction: Direction
    strength: int  # 1 weak .. 3 strong
    quote: str


@dataclass
class Evidence:
    id: str
    claim: str
    direction: Direction
    strength: int
    quote: str
    driver_id: str | None = None
    source_title: str | None = None
    source_url: str | None = None
    source_date: str | None = None
    source_cited: bool = False  # the source's title or URL appears in the forecaster's text (only then used for tracing)
    traced_to: int | Literal["untraced"] = "untraced"  # research item index, decided in code
    trace_method: TraceMethod | None = None
    trace_score: float | None = None
    extractor_traced_to: int | None = None  # what the extractor claimed; kept for auditing the tracer


@dataclass
class Scenario:
    name: str
    description: str
    quote: str
    probability: float | None = None


@dataclass
class Final:
    probability: float  # from the forecast record, never from the extractor
    stated_shift_from_base: str | None = None
    quote: str = ""
    shift_from_base: float | None = None  # probability - base_rate.value, when both are numbers


@dataclass
class GraphChecks:
    dropped: dict[str, int] = field(default_factory=dict)  # node kind -> nodes dropped by the quote check
    invalid: dict[str, int] = field(default_factory=dict)  # node kind -> nodes dropped as malformed (bad direction, strength..)
    dropped_quotes: list[str] = field(default_factory=list)  # the failing quotes, for auditing the extractor
    orphan_evidence: int = 0  # evidence whose driver_id names no surviving driver (driver_id cleared)

    @property
    def n_dropped(self) -> int:
        return sum(self.dropped.values())


@dataclass
class ReasoningGraph:
    base_rate: BaseRate
    drivers: list[Driver]
    evidence: list[Evidence]
    scenarios: list[Scenario]
    final: Final
    checks: GraphChecks = field(default_factory=GraphChecks)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["checks"]["n_dropped"] = self.checks.n_dropped
        return d


# ---- JSON schemas
_Q = {"type": "string", "description": "the exact sentence from the forecaster's text, copied verbatim"}
_DIR = {"type": "string", "enum": ["up", "down"], "description": "pushes the probability of YES up or down"}
_STRENGTH = {"type": "integer", "minimum": 1, "maximum": 3}
_NULLABLE_STR = {"type": ["string", "null"]}
_PROB = {"type": ["number", "null"], "minimum": 0, "maximum": 1}

# What the extractor model must return (sent as Gemini's responseJsonSchema).
EXTRACTOR_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "base_rate": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["stated", "none"]},
                "reference_class": {"type": "string"},
                "value": _PROB,
                "quote": _Q,
            },
            "required": ["status", "reference_class", "value", "quote"],
        },
        "drivers": {"type": "array", "items": {
            "type": "object",
            "properties": {"id": {"type": "string"}, "text": {"type": "string"}, "direction": _DIR, "strength": _STRENGTH,
                           "quote": _Q},
            "required": ["id", "text", "direction", "strength", "quote"],
        }},
        "evidence": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "id": {"type": "string"}, "claim": {"type": "string"}, "driver_id": _NULLABLE_STR, "direction": _DIR,
                "strength": _STRENGTH, "source_title": _NULLABLE_STR, "source_url": _NULLABLE_STR, "source_date": _NULLABLE_STR,
                "research_item": {"type": ["integer", "null"], "description": "index of the research item, null if none"},
                "quote": _Q,
            },
            "required": ["id", "claim", "driver_id", "direction", "strength", "source_title", "source_url", "source_date",
                         "research_item", "quote"],
        }},
        "scenarios": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "probability": _PROB, "description": {"type": "string"}, "quote": _Q},
            "required": ["name", "probability", "description", "quote"],
        }},
        "final": {
            "type": "object",
            "properties": {"stated_shift_from_base": _NULLABLE_STR, "quote": _Q},
            "required": ["stated_shift_from_base", "quote"],
        },
    },
    "required": ["base_rate", "drivers", "evidence", "scenarios", "final"],
}

# The stored, checked graph (ReasoningGraph.to_dict()).
GRAPH_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": GRAPH_SCHEMA_VERSION,
    "type": "object",
    "properties": {
        "base_rate": EXTRACTOR_RESPONSE_SCHEMA["properties"]["base_rate"],
        "drivers": EXTRACTOR_RESPONSE_SCHEMA["properties"]["drivers"],
        "evidence": {"type": "array", "items": {
            "type": "object",
            "properties": {
                **{k: v for k, v in EXTRACTOR_RESPONSE_SCHEMA["properties"]["evidence"]["items"]["properties"].items()
                   if k != "research_item"},
                "traced_to": {"oneOf": [{"type": "integer", "minimum": 0}, {"const": "untraced"}]},
                "source_cited": {"type": "boolean"},
                "trace_method": {"enum": ["url", "title", "text", None]},
                "trace_score": {"type": ["number", "null"]},
                "extractor_traced_to": {"type": ["integer", "null"]},
            },
            "required": ["id", "claim", "direction", "strength", "quote", "traced_to"],
        }},
        "scenarios": EXTRACTOR_RESPONSE_SCHEMA["properties"]["scenarios"],
        "final": {
            "type": "object",
            "properties": {"probability": {"type": "number", "minimum": 0, "maximum": 1},
                           "stated_shift_from_base": _NULLABLE_STR, "quote": {"type": "string"},
                           "shift_from_base": {"type": ["number", "null"]}},
            "required": ["probability"],
        },
        "checks": {"type": "object"},
    },
    "required": ["base_rate", "drivers", "evidence", "scenarios", "final", "checks"],
}


# ---- quote check
_TRANSLATE = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                            "−": "-", " ": " ", "*": "", "`": "", "#": ""})


def normalize_text(s: str) -> str:
    """Case, whitespace, curly quotes, dashes and markdown emphasis are ignored; everything else must match exactly."""
    s = unicodedata.normalize("NFKC", s).translate(_TRANSLATE)
    return re.sub(r"\s+", " ", s).strip().casefold()


def quote_ok(quote: str | None, source_norm: str) -> bool:
    q = normalize_text(quote or "").rstrip(".")
    return len(q) >= 8 and q in source_norm


# ---- tracing evidence to research items
_STOP = set("""the a an and or of to in on for by with from at as is are was were be been being has have had that this these those
it its their his her they them which who what when will would could should may might can not no yes but if than then so also
into over under about after before during since until per more most less least very such there here other any each all""".split())


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9.%-]*[a-z0-9%]|[a-z0-9]", normalize_text(s))
            if w not in _STOP and (len(w) >= 3 or any(c.isdigit() for c in w))}


def _norm_url(u: str) -> str:
    u = re.sub(r"^https?://", "", u.strip().lower())
    return re.sub(r"^www\.", "", u).rstrip("/")


def trace_evidence(ev: Evidence, items: list[dict[str, Any]], rationale_norm: str) -> None:
    """Set ev.traced_to / trace_method / trace_score. items are research items as stored in the record.

    A URL or title match counts only if the forecaster's own text cites it; otherwise the extractor could copy a source from the
    research and the "trace" would just echo the extractor's guess. Uncited evidence is traced by text overlap.
    """
    def cited(s: str | None) -> bool:
        return bool(s) and len(s.strip()) >= 8 and normalize_text(s) in rationale_norm

    url_cited, title_cited = cited(ev.source_url), cited(ev.source_title)
    ev.source_cited = url_cited or title_cited
    if url_cited:
        target = _norm_url(ev.source_url)
        for i, it in enumerate(items):
            if it.get("url") and _norm_url(it["url"]) == target:
                ev.traced_to, ev.trace_method, ev.trace_score = i, "url", 1.0
                return
    if title_cited and len(ev.source_title.strip()) >= 15:
        t = normalize_text(ev.source_title)
        for i, it in enumerate(items):
            title = normalize_text(it.get("title") or "")
            if title and (t == title or t in title or (len(title) >= 15 and title in t)):
                ev.traced_to, ev.trace_method, ev.trace_score = i, "title", 1.0
                return
    best: tuple[float, int] | None = None
    for i, it in enumerate(items):
        text_words = _words(f"{it.get('title') or ''} {it.get('text') or ''}")
        for probe in (ev.claim, ev.quote):
            w = _words(probe)
            if not w:
                continue
            shared = len(w & text_words)
            share = shared / len(w)
            if shared >= TRACE_MIN_WORDS and share >= TRACE_MIN_SHARE and (best is None or share > best[0]):
                best = (share, i)
    if best is not None:
        ev.traced_to, ev.trace_method, ev.trace_score = best[1], "text", round(best[0], 3)
    else:
        ev.traced_to, ev.trace_method, ev.trace_score = "untraced", None, None


# ---- building a checked graph from the extractor's JSON
class MalformedGraph(ValueError):
    """The extractor's output is not a JSON object of the expected shape."""


def _prob(x: Any) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    x = float(x)
    if 1 < x <= 100:  # the model answered in percent
        x /= 100
    return x if 0 <= x <= 1 else None


def _strength(x: Any) -> int | None:
    if isinstance(x, bool):
        return None
    if isinstance(x, float) and x.is_integer():
        x = int(x)
    return x if isinstance(x, int) and 1 <= x <= 3 else None


def _str(x: Any) -> str | None:
    return x.strip() if isinstance(x, str) and x.strip() else None


def build_graph(raw: Any, *, rationale: str, final_probability: float, research_items: list[dict[str, Any]]) -> ReasoningGraph:
    """Validate the extractor's JSON against the forecaster's text and the research; raise MalformedGraph if unusable."""
    if not isinstance(raw, dict) or not all(k in raw for k in ("base_rate", "drivers", "evidence", "scenarios", "final")):
        raise MalformedGraph(f"expected an object with base_rate/drivers/evidence/scenarios/final, got {str(raw)[:200]}")
    for k in ("drivers", "evidence", "scenarios"):
        if not isinstance(raw[k], list):
            raise MalformedGraph(f"{k} is not a list")
    if not isinstance(raw["base_rate"], dict) or not isinstance(raw["final"], dict):
        raise MalformedGraph("base_rate/final is not an object")

    src = normalize_text(rationale)
    checks = GraphChecks()

    def keep(kind: str, quote: Any) -> bool:
        if quote_ok(quote if isinstance(quote, str) else "", src):
            return True
        checks.dropped[kind] = checks.dropped.get(kind, 0) + 1
        checks.dropped_quotes.append(f"{kind}: {str(quote)[:200]}")
        return False

    def invalid(kind: str) -> None:
        checks.invalid[kind] = checks.invalid.get(kind, 0) + 1

    br = raw["base_rate"]
    base = BaseRate(status="none")
    if br.get("status") == "stated":
        if keep("base_rate", br.get("quote")):
            base = BaseRate(status="stated", reference_class=_str(br.get("reference_class")) or "",
                            value=_prob(br.get("value")), quote=br["quote"])

    drivers: list[Driver] = []
    for d in raw["drivers"]:
        if not isinstance(d, dict) or d.get("direction") not in ("up", "down") or _strength(d.get("strength")) is None \
                or not _str(d.get("id")) or not _str(d.get("text")):
            invalid("driver")
            continue
        if keep("driver", d.get("quote")):
            drivers.append(Driver(id=d["id"].strip(), text=d["text"].strip(), direction=d["direction"],
                                  strength=_strength(d["strength"]), quote=d["quote"]))
    driver_ids = {d.id for d in drivers}

    evidence: list[Evidence] = []
    for e in raw["evidence"]:
        if not isinstance(e, dict) or e.get("direction") not in ("up", "down") or _strength(e.get("strength")) is None \
                or not _str(e.get("claim")):
            invalid("evidence")
            continue
        if not keep("evidence", e.get("quote")):
            continue
        ri = e.get("research_item")
        ev = Evidence(
            id=_str(e.get("id")) or f"e{len(evidence) + 1}", claim=e["claim"].strip(), direction=e["direction"],
            strength=_strength(e["strength"]), quote=e["quote"], driver_id=_str(e.get("driver_id")),
            source_title=_str(e.get("source_title")), source_url=_str(e.get("source_url")),
            source_date=_str(e.get("source_date")),
            extractor_traced_to=ri if isinstance(ri, int) and not isinstance(ri, bool) else None,
        )
        if ev.driver_id is not None and ev.driver_id not in driver_ids:
            ev.driver_id = None
            checks.orphan_evidence += 1
        trace_evidence(ev, research_items, src)
        evidence.append(ev)

    scenarios: list[Scenario] = []
    for s in raw["scenarios"]:
        if not isinstance(s, dict) or not _str(s.get("name")):
            invalid("scenario")
            continue
        if keep("scenario", s.get("quote")):
            scenarios.append(Scenario(name=s["name"].strip(), description=_str(s.get("description")) or "",
                                      quote=s["quote"], probability=_prob(s.get("probability"))))

    fr = raw["final"]
    quote = fr.get("quote") if isinstance(fr.get("quote"), str) else ""
    if quote and not keep("final", quote):
        quote = ""  # the probability itself never depends on the extractor, so only the quote is dropped
    final = Final(probability=float(final_probability), stated_shift_from_base=_str(fr.get("stated_shift_from_base")),
                  quote=quote)
    if base.value is not None:
        final.shift_from_base = round(final.probability - base.value, 4)

    return ReasoningGraph(base_rate=base, drivers=drivers, evidence=evidence, scenarios=scenarios, final=final, checks=checks)


def graph_features(g: ReasoningGraph) -> dict[str, Any]:
    """Per-member features for EXP-005 step 0 (do they predict larger errors?). Hypothesis-generating only."""
    n_ev = len(g.evidence)
    # Signed weight of everything the member cites (+strength for "up", -strength for "down"). A member whose drivers and evidence
    # clearly push one way but whose number sits clearly on the other side of 50% is internally inconsistent: on the pilot this
    # flagged exactly two members, both YES/NO inversions (research/11). Thresholds keep near-balanced cases out.
    push = sum(n.strength * (1 if n.direction == "up" else -1) for n in [*g.drivers, *g.evidence])
    untraced = sum(1 for e in g.evidence if e.traced_to == "untraced")
    return {
        "has_base_rate": g.base_rate.status == "stated",
        "base_rate_value": g.base_rate.value,
        "shift_from_base": g.final.shift_from_base,
        "n_drivers": len(g.drivers),
        "n_evidence": n_ev,
        "n_untraced": untraced,
        "untraced_share": untraced / n_ev if n_ev else None,
        "n_scenarios": len(g.scenarios),
        "n_dropped": g.checks.n_dropped,
        "net_push": push,
        "direction_mismatch": (push <= -MISMATCH_MIN_PUSH and g.final.probability >= 0.5 + MISMATCH_MIN_MARGIN)
        or (push >= MISMATCH_MIN_PUSH and g.final.probability <= 0.5 - MISMATCH_MIN_MARGIN),
    }
