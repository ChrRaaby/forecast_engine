"""The researcher pilot's block (what every reader returns), its validator and the safe calculator (B-102).

The validator rejects, it never repairs: a figure without a link, a date and a quote that is on the fetched page is not counted.
The quote check is EXP-013's fixed one (`research_v2._norm`, `_value_in_quote`), imported.
"""
from __future__ import annotations

import ast
import json
import operator
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from forecast_engine import research_v2 as rv

QUOTE_MAX = 200
HISTORY_MAX, DEVELOPMENTS_MAX, LINE_MAX = 12, 5, 240
FIELDS = ("current_value", "as_of_date", "source_url", "quote", "history", "developments", "not_found")


@dataclass(frozen=True)
class Block:
    current_value: str | None
    as_of_date: str | None
    source_url: str | None
    quote: str | None
    history: tuple[dict[str, str], ...] = ()
    developments: tuple[dict[str, str], ...] = ()
    not_found: tuple[str, ...] = ()


_YEAR = re.compile(r"(?<!\d)(19|20)\d{2}(?!\d)")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def is_url(s: Any) -> bool:
    if not isinstance(s, str) or " " in s:
        return False
    try:
        parts = urlsplit(s)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname)


def date_problem(s: Any, today: str) -> str | None:
    if not isinstance(s, str) or not _YEAR.search(s):
        return "no date"
    if _ISO.match(s):
        try:
            datetime.strptime(s, "%Y-%m-%d")
        except ValueError:
            return f"not a date: {s}"
        if s > today:
            return f"date in the future: {s}"
    return None


def same_page(a: str, b: str) -> bool:
    def strip(u: str) -> str:
        return re.sub(r"^https?://(www\.)?", "", (u or "").split("#")[0]).rstrip("/").lower()

    return strip(a) == strip(b)


def page_for(url: str, pages: dict[str, str]) -> str | None:
    return next((t for u, t in pages.items() if same_page(u, url)), None)


def quote_on_page(quote: str, text: str) -> bool:
    """As `rv.render_block` checks it: every line of the quote word for word on the page, formatting forgiven."""
    parts = [" ".join(ln.split()) for ln in quote.splitlines() if ln.strip()]
    page = rv._norm(text)
    return bool(parts) and all(rv._norm(p) in page for p in parts)


def _value_problems(obj: dict, pages: dict[str, str], today: str, quote_max: int) -> list[str]:
    value, url, quote = obj["current_value"], obj["source_url"], obj["quote"]
    if not isinstance(value, str) or len(value) > 200:
        return ["current_value is not a short text"]
    bad: list[str] = []
    if d := date_problem(obj["as_of_date"], today):
        bad.append(f"current_value: {d}")
    if not is_url(url):
        bad.append("current_value: no link")
    if not isinstance(quote, str) or not quote.strip():
        bad.append("current_value: no quote")
    elif len(quote) > quote_max:
        bad.append(f"quote longer than {quote_max} characters")
    elif is_url(url):
        text = page_for(url, pages)
        if text is None:
            bad.append("quote not checked: the cited page was not fetched")
        elif not quote_on_page(quote, text):
            bad.append("quote is not on the fetched page")
        elif not rv._value_in_quote(value, quote):
            bad.append("the value is not in the quote")
    return bad


def validate(obj: Any, pages: dict[str, str], today: str, quote_max: int = QUOTE_MAX) -> tuple[Block | None, list[str]]:
    """(block, []) or (None, reasons). `pages`: text actually fetched, by url. Nothing is corrected or dropped to make it pass."""
    if not isinstance(obj, dict):
        return None, ["not a JSON object"]
    bad: list[str] = []
    missing, extra = [f for f in FIELDS if f not in obj], [f for f in obj if f not in FIELDS]
    if missing:
        bad.append("missing fields: " + ", ".join(missing))
    if extra:
        bad.append("unknown fields: " + ", ".join(map(str, extra)))
    if bad:
        return None, bad
    value, hist, devs, nf = obj["current_value"], obj["history"], obj["developments"], obj["not_found"]
    for name, seq, top in (("history", hist, HISTORY_MAX), ("developments", devs, DEVELOPMENTS_MAX), ("not_found", nf, 20)):
        if not isinstance(seq, list):
            bad.append(f"{name} is not a list")
        elif len(seq) > top:
            bad.append(f"{name} has {len(seq)} entries, at most {top}")
    if bad:
        return None, bad
    if any(not isinstance(x, str) or not x.strip() for x in nf):
        bad.append("not_found holds something other than text")
    if value is None or value == "":
        if not nf:
            bad.append("no current value and nothing in not_found")
        bad += [f"{f} given without a current value" for f in ("as_of_date", "source_url", "quote") if obj[f] not in (None, "")]
    else:
        bad += _value_problems(obj, pages, today, quote_max)
    for n, row in enumerate(hist, 1):
        if not isinstance(row, dict) or set(row) != {"date", "value", "url"}:
            bad.append(f"history row {n}: needs exactly date, value, url")
            continue
        if d := date_problem(row["date"], today):
            bad.append(f"history row {n}: {d}")
        if not isinstance(row["value"], str) or not row["value"].strip():
            bad.append(f"history row {n}: no value")
        if not is_url(row["url"]):
            bad.append(f"history row {n}: no link")
    for n, dev in enumerate(devs, 1):
        if not isinstance(dev, dict) or set(dev) != {"date", "text", "url"}:
            bad.append(f"development {n}: needs exactly date, text, url")
            continue
        if d := date_problem(dev["date"], today):
            bad.append(f"development {n}: {d}")
        text = dev["text"]
        if not isinstance(text, str) or not text.strip() or "\n" in text or len(text) > LINE_MAX:
            bad.append(f"development {n}: not one short line")
        if not is_url(dev["url"]):
            bad.append(f"development {n}: no link")
    if bad:
        return None, bad
    return Block(value or None, obj["as_of_date"] or None, obj["source_url"] or None, obj["quote"] or None,
                 tuple(hist), tuple(devs), tuple(nf)), []


def rows_on_fetched_pages(block: Block, pages: dict[str, str]) -> int:
    """History rows whose figure is on the fetched text of the page they cite (a first check; 3 rows per table are read by hand)."""
    return sum(1 for r in block.history if (t := page_for(r["url"], pages)) is not None and rv._value_in_quote(r["value"], t))


def not_found_block(why: str) -> dict:
    return {"current_value": None, "as_of_date": None, "source_url": None, "quote": None, "history": [], "developments": [],
            "not_found": [why]}


def parse_json(text: str) -> dict | None:
    """The first JSON object in a model's answer (code fences and stray words around it are skipped), or None."""
    dec = json.JSONDecoder()
    for m in re.finditer(r"\{", text or ""):
        try:
            obj, _ = dec.raw_decode(text, m.start())
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Mod: operator.mod,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}


def calc(expression: str) -> float:
    """Arithmetic on numbers only: + - * / % ** and brackets. Anything else raises ValueError."""
    if len(expression) > 200:
        raise ValueError("expression too long")

    def ev(n: ast.AST) -> float:
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and type(n.value) in (int, float):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, ast.Pow) and (abs(b) > 64 or abs(a) > 1e6):
                raise ValueError("power too large")
            return _OPS[type(n.op)](a, b)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        raise ValueError("only numbers and + - * / % ** ( )")

    try:
        out = ev(ast.parse(expression.replace(",", "").strip(), mode="eval"))
    except (SyntaxError, ZeroDivisionError, OverflowError, RecursionError, TypeError) as e:
        raise ValueError(str(e)) from e
    if not isinstance(out, (int, float)):
        raise ValueError("not a number")
    return out
