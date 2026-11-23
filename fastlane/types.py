"""One question of the FAST lane, whichever source it came from."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from forecast_engine.schema import QuestionSnapshot

# (url, params) -> parsed JSON. Injected so tests never touch the network.
GetJson = Callable[[str, dict[str, Any] | None], Any]

POLYMARKET_ID_BASE = 1_000_000_000  # question ids: Polymarket 1e9 + market id, code-made 2e9 + hash; Metaculus ids are far below
SERIES_ID_BASE = 2_000_000_000


@dataclass(frozen=True)
class FastQuestion:
    key: str  # "pm:<market id>" or "series:<family>:<detail>:<date>"
    source: str  # "polymarket" | "series"
    snapshot: QuestionSnapshot
    closes: datetime  # when the question's answer is fixed
    meta: dict[str, Any] = field(default_factory=dict)  # what the resolver needs; never shown to a model
    # The market's YES price when the question was picked. Stored by the runner in a private file (market_prices.jsonl) that no
    # prompt, research call or record reads; used only for the 10-90% pick filter and, after resolution, a market-vs-bot comparison.
    price_at_pick: float | None = None

    def to_pick(self, picked_at: datetime) -> dict[str, Any]:
        return {"key": self.key, "source": self.source, "question_id": self.snapshot.question_id, "picked_at": picked_at.isoformat(),
                "closes": self.closes.isoformat(), "title": self.snapshot.question_text, "type": self.snapshot.question_type,
                "meta": self.meta}
