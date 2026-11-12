"""Which OpenRouter host served each model call, and a guard for experiments that pin one host per open-weight model (B-93).

OpenRouter routes an open-weight model (e.g. DeepSeek V4.1 Flash) to many hosts that can run different builds or precisions;
in our four cheap runs of 2026-11-07/09 DeepSeek was spread over ~15 hosts in every run. An experiment that pins a host
(`--pin-hosts MODEL=HOST`) must then be checked: every forecasting call of a pinned model was served by that host, in every
arm. Otherwise the arms are not comparable and the report is refused (protocol §5).

Host names: OpenRouter takes a provider slug in `provider.order` ("deepinfra") and reports a display name in the response
("DeepInfra"); they are compared after lower-casing and dropping spaces, dashes, dots and underscores.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any


def norm(host: str | None) -> str:
    return re.sub(r"[\s\-_.]", "", str(host or "")).lower()


def parse_pins(items: Sequence[str] | None) -> dict[str, str]:
    """["deepseek/deepseek-v4.1-flash=deepinfra", ...] -> {model: host}. Raises ValueError on a malformed or repeated item."""
    pins: dict[str, str] = {}
    for item in items or ():
        model, sep, host = item.partition("=")
        model, host = model.strip(), host.strip()
        if not sep or not model or not host:
            raise ValueError(f"--pin-hosts wants MODEL=HOST, got {item!r}")
        if model in pins:
            raise ValueError(f"--pin-hosts names {model} twice")
        pins[model] = host
    return pins


def forecast_calls(rec: Mapping[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    """(model, call.extra) for every forecasting call in a record that got an answer back (failed calls carry no host)."""
    for f in rec.get("forecasters") or ():
        call = f.get("call") or {}
        if call.get("error") and not call.get("output"):
            continue
        yield str(f.get("model") or call.get("model") or ""), dict(call.get("extra") or {})


def host_counts(record_paths: Iterable[Path]) -> dict[str, Counter]:
    """model -> Counter of served hosts (display names; '?' when the response named none)."""
    out: dict[str, Counter] = defaultdict(Counter)
    for path in record_paths:
        rec = json.loads(Path(path).read_text(encoding="utf-8"))
        for model, extra in forecast_calls(rec):
            out[model][extra.get("upstream_provider") or "?"] += 1
    return dict(out)


def check_pins(arms: Mapping[str, Iterable[Path]], pins: Mapping[str, str]) -> tuple[list[str], list[str]]:
    """(problems, summary). A problem is any answered call of a pinned model, in any arm, that was not requested with that pin
    (e.g. a reused baseline from an unpinned run) or was served by another host or by an unnamed one. The summary lists the
    hosts per arm and model, pinned or not, for the report."""
    problems: list[str] = []
    summary: list[str] = []
    for arm, paths in arms.items():
        hosts: dict[str, Counter] = defaultdict(Counter)
        for path in paths:
            rec = json.loads(Path(path).read_text(encoding="utf-8"))
            qid = (rec.get("question") or {}).get("question_id")
            for model, extra in forecast_calls(rec):
                served = extra.get("upstream_provider")
                hosts[model][served or "?"] += 1
                pin = pins.get(model)
                if not pin:
                    continue
                if norm(extra.get("pinned_host")) != norm(pin):
                    problems.append(f"arm {arm} q{qid} {model}: not requested with the pin {pin} "
                                    f"(pinned_host {extra.get('pinned_host')!r})")
                elif norm(served) != norm(pin):
                    problems.append(f"arm {arm} q{qid} {model}: served by {served or 'an unnamed host'}, pinned {pin}")
        for model, c in sorted(hosts.items()):
            tag = f" (pinned {pins[model]})" if model in pins else ""
            summary.append(f"arm {arm} {model}{tag}: " + ", ".join(f"{h} {n}" for h, n in c.most_common()))
    return problems, summary
