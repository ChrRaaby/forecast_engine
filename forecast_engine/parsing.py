"""Deterministic parsers for forecaster outputs.

The template uses a second LLM call to parse outputs. We parse with regexes instead: free, reproducible, and a failed
parse is visible in the record rather than silently "fixed". Anything ambiguous (especially magnitude words like
"million" in numeric answers, the classic units bug, R-17) is rejected, not guessed.
"""
from __future__ import annotations

import re
import statistics

PERCENTILES = (10, 20, 40, 60, 80, 90)
MC_MIN_PROB = 0.001

_MAGNITUDE_WORDS = re.compile(r"\b(thousand|million|billion|trillion|k|m|mn|bn|b|t)\b", re.IGNORECASE)


class ParseError(ValueError):
    pass


def parse_binary(text: str, clip: tuple[float, float] = (0.01, 0.99)) -> float:
    matches = re.findall(r"Probability\s*:\s*\**\s*([0-9]+(?:\.[0-9]+)?)\s*%", text, flags=re.IGNORECASE)
    if not matches:
        raise ParseError('no "Probability: ZZ%" found')
    value = float(matches[-1]) / 100.0
    if not 0.0 <= value <= 1.0:
        raise ParseError(f"probability out of range: {matches[-1]}%")
    lo, hi = clip
    return min(hi, max(lo, value))


def parse_multiple_choice(text: str, options: tuple[str, ...] | list[str]) -> dict[str, float]:
    try:
        raw = _mc_by_name(text, options)
    except ParseError as by_name:
        # The template prompt shows the answer format as "Option_A: ...", in the order the options are listed, and some
        # models copy the placeholder labels literally. Map letters to options positionally, but only if every letter is there.
        try:
            raw = _mc_by_letter(text, options)
        except ParseError:
            raise by_name
    return _mc_normalize(raw)


def _mc_by_letter(text: str, options: tuple[str, ...] | list[str]) -> dict[str, float]:
    if len(options) > 26:
        raise ParseError("too many options for letter labels")
    raw: dict[str, float] = {}
    for i, opt in enumerate(options):
        letter = chr(ord("A") + i)
        found = re.findall(
            # "Option_A: 7%", also "Option_A (Eintracht Frankfurt vs. Köln): 7%" (Qwen3.8 Max, 2026-10-08)
            r"^[\s\*\-#>]*Option[_\s]*" + letter + r"\**\s*(?:\([^)\n]*\))?\**\s*:\s*\**\s*([0-9]+(?:\.[0-9]+)?)\s*%?",
            text,
            flags=re.MULTILINE,
        )
        if not found:
            raise ParseError(f"no probability found for Option_{letter}")
        raw[opt] = float(found[-1])
    return raw


def _mc_by_name(text: str, options: tuple[str, ...] | list[str]) -> dict[str, float]:
    raw: dict[str, float] = {}
    for opt in options:
        pattern = (
            r"^[\s\*\-#>]*(?:Option[_\s]*)?[\"'“”]?" + re.escape(opt) + r"[\"'“”]?\**\s*:\s*\**\s*"
            r"([0-9]+(?:\.[0-9]+)?)\s*(%?)"
        )
        found = re.findall(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
        if not found:
            raise ParseError(f"no probability found for option {opt!r}")
        raw[opt] = float(found[-1][0])
    return raw


def _mc_normalize(raw: dict[str, float]) -> dict[str, float]:
    total = sum(raw.values())
    if total <= 0:
        raise ParseError("option probabilities sum to zero")
    # Accept either percentages (sum ~100) or fractions (sum ~1); anything else means a misparse.
    if 90 <= total <= 110:
        scale = 100.0
    elif 0.9 <= total <= 1.1:
        scale = 1.0
    else:
        raise ParseError(f"option probabilities sum to {total}, expected ~100 or ~1")
    probs = {k: max(MC_MIN_PROB, v / scale) for k, v in raw.items()}
    return normalize(probs)


def parse_numeric(text: str, unit: str = "", allow_point: bool = False) -> list[tuple[float, float]]:
    """Returns [(percentile as fraction, value)] for the 6 declared percentiles, non-decreasing in value.

    Ties are legitimate (e.g. "0, 0, 1, 1, 2, 3" on a count question); decreasing values are a misparse or a confused
    forecaster and are rejected. Metaculus needs strictly increasing values, which make_strictly_increasing() provides
    at submission time.

    All six values equal is a point forecast. On a discrete question (`allow_point=True`) that is a legitimate "I'm sure
    it's 3": the tie-break keeps the mass inside that value's bucket and the CDF standardisation spreads ~1% elsewhere.
    First seen 2026-10-06 on MiniBench 46123 (judges 0-3), where all three models answered 3 and 19 runs failed. On a
    continuous question a single number is more likely a misread prompt, so it stays rejected.
    """
    values: dict[int, float] = {}
    for m in re.finditer(r"Percentile\s*(10|20|40|60|80|90)\s*:\s*(.+)", text, flags=re.IGNORECASE):
        values[int(m.group(1))] = _parse_number(m.group(2), unit)
    missing = [p for p in PERCENTILES if p not in values]
    if missing:
        raise ParseError(f"missing percentiles {missing}")
    ordered = [values[p] for p in PERCENTILES]
    if any(b < a for a, b in zip(ordered, ordered[1:])):
        raise ParseError(f"percentile values decrease: {ordered}")
    if ordered[0] == ordered[-1] and not allow_point:
        raise ParseError(f"all percentile values equal: {ordered}")
    return [(p / 100.0, v) for p, v in zip(PERCENTILES, ordered)]


def _parse_number(raw: str, unit: str) -> float:
    s = re.sub(r"\(.*?\)", "", raw)  # drop "(lowest number value)" etc.
    s = s.replace("*", "").replace('"', "").replace("−", "-").strip()
    s = re.sub(r"^[$€£]", "", s).strip()
    m = re.match(r"^(-?\s*[$€£]?\s*[\d,]*\.?\d+)\s*(.*)$", s)
    if not m:
        raise ParseError(f"not a number: {raw.strip()!r}")
    number = m.group(1).replace(",", "").replace(" ", "").lstrip("$€£")
    trailing = m.group(2).strip().rstrip(".").strip()
    if trailing.lower().startswith("e") and re.match(r"^e[+-]?\d", trailing.lower()):
        raise ParseError(f"scientific notation: {raw.strip()!r}")
    trailing_wo_unit = trailing.replace(unit, "") if unit else trailing
    if _MAGNITUDE_WORDS.search(trailing_wo_unit) or re.search(r"\d", trailing):
        raise ParseError(f"ambiguous magnitude/units: {raw.strip()!r}")
    try:
        return float(number)
    except ValueError as e:
        raise ParseError(f"not a number: {raw.strip()!r}") from e


def fit_to_bounds(
    points: list[tuple[float, float]], lower: float, upper: float, open_lower: bool, open_upper: bool
) -> tuple[list[tuple[float, float]], bool]:
    """Keep declared percentiles where Metaculus accepts them. Returns (points, clipped?).

    Closed bounds: values are clamped to the bound. Open bounds: Metaculus (via forecasting-tools) rejects values more than 2x the
    question range beyond a bound, so they are clamped to 1.9x. First seen 2026-10-03 on a USD/Toman question where all three
    models put P90 at 500k against a 150k-250k range.
    """
    span = upper - lower
    lo = lower - 1.9 * span if open_lower else lower
    hi = upper + 1.9 * span if open_upper else upper
    out = [(p, min(hi, max(lo, v))) for p, v in points]
    return out, out != points


def make_strictly_increasing(points: list[tuple[float, float]], scale: float) -> list[tuple[float, float]]:
    """Break ties by the smallest meaningful step (1e-6 of the question range) so the CDF can be built."""
    eps = max(abs(scale), 1.0) * 1e-6
    out: list[tuple[float, float]] = []
    for pct, v in points:
        if out and v <= out[-1][1]:
            v = out[-1][1] + eps
        out.append((pct, v))
    return out


def normalize(probs: dict[str, float]) -> dict[str, float]:
    total = sum(probs.values())
    return {k: v / total for k, v in probs.items()}


def median_binary(preds: list[float]) -> float:
    return float(statistics.median(preds))


def median_multiple_choice(preds: list[dict[str, float]]) -> dict[str, float]:
    options = list(preds[0].keys())
    return normalize({o: float(statistics.median(p[o] for p in preds)) for o in options})


def median_numeric(preds: list[list[tuple[float, float]]]) -> list[tuple[float, float]]:
    """Pointwise median per declared percentile. Monotonicity is preserved: the median is monotone in each argument."""
    out = []
    for i, (pct, _) in enumerate(preds[0]):
        out.append((pct, float(statistics.median(p[i][1] for p in preds))))
    return out
