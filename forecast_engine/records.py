"""Append-only forecast records: one full JSON per question plus a compact JSONL cost/provenance log per run.

Written under data/forecasts/<run_id>/ (gitignored) and uploaded as a GitHub Actions artifact (ADR-0002).
"""
from __future__ import annotations

import json
from pathlib import Path

from .schema import ForecastRecord


class RecordWriter:
    def __init__(self, run_id: str, root: Path | str = "data/forecasts") -> None:
        self.run_dir = Path(root) / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.run_dir / "forecasts.jsonl"

    def write(self, record: ForecastRecord, *, published: bool, status: str) -> Path:
        full = record.to_dict()
        full["published"] = published
        full["status"] = status
        path = self.run_dir / f"q{record.question.question_id}.json"
        path.write_text(json.dumps(full, indent=2, ensure_ascii=False), encoding="utf-8")
        summary = {
            "question_id": record.question.question_id,
            "post_id": record.question.post_id,
            "type": record.question.question_type,
            "url": record.question.page_url,
            "config_version": record.config_version,
            "config_hash": record.config_hash,
            "as_of": full["as_of"],
            "status": status,
            "published": published,
            "aggregate": full["aggregate"],
            "models": [f.model for f in record.forecasters],
            "forecasters_ok": record.n_ok,
            "tokens_in": [f.call.tokens_in for f in record.forecasters],
            "tokens_out": [f.call.tokens_out for f in record.forecasters],
            "forecast_cost_usd": [f.call.cost_usd for f in record.forecasters],
            "research_providers": record.research.providers,
            "research_calls": [
                {"provider": c.provider, "model": c.model, "tokens_in": c.tokens_in, "tokens_out": c.tokens_out,
                 "cost_usd": c.cost_usd, "error": c.error}
                for c in record.research.calls
            ],
            "asknews_calls": sum(c.extra.get("asknews_calls", 0) for c in record.research.calls),
            "cost_usd": round(record.cost_usd, 6),
            "cost_complete": record.cost_complete,
            "errors": record.errors + record.research.errors,
            "flags": record.flags,
        }
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(summary, ensure_ascii=False) + "\n")
        return path
