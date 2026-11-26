"""The FAST lane's own arm table (EXP-017 card).

Every arm is a cheap-roster config. Arms that also run as tournament shadows are taken from `SHADOW_CONFIGS` unchanged (same config
hash), so the lane compares like with like. The lane-only arms below are defined here and nowhere else: they are not in
`forecast_engine/config.py` and no tournament list contains them (tests/test_fastlane.py checks both).

research = "shared": the question's research, gathered once and given to every such arm (EXP-013 steps applied per arm on top).
research = "none": an empty research bundle (the sensitivity control); nothing is fetched for it.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from forecast_engine.config import SHADOW_CONFIGS, BotConfig

HIGH_EFFORT_MAX_TOKENS = 32000  # the limit covers thinking and answer; a model that thinks more needs room to answer (2x2 card)


@dataclass(frozen=True)
class Arm:
    name: str
    cfg: BotConfig
    research: str = "shared"  # "shared" | "none"

    def __post_init__(self) -> None:
        if self.research not in ("shared", "none"):
            raise ValueError(f"research must be 'shared' or 'none', not {self.research!r}")


def high_effort(cfg: BotConfig) -> BotConfig:
    """Only the forecaster calls change: research calls, the polarity check and helpers keep their own settings."""
    return replace(cfg, forecasters=tuple(replace(f, reasoning_effort="high", max_tokens=HIGH_EFFORT_MAX_TOKENS)
                                          for f in cfg.forecasters))


def _shadow(name: str) -> Arm:
    return Arm(name, SHADOW_CONFIGS[name])


ARMS: dict[str, Arm] = {a.name: a for a in (
    _shadow("cheap"),             # A, the baseline
    _shadow("cheap-old-prompt"),  # B (EXP-002 pair)
    _shadow("exp013-c-writer"),   # C (EXP-013 writer)
    _shadow("exp013-d-news"),     # not in EXP-017: 6 more AskNews credits a question
    Arm("cheap-aa", SHADOW_CONFIGS["cheap"]),                     # A2: A again, same research, its own calls (A/A control)
    Arm("no-research", SHADOW_CONFIGS["cheap"], research="none"),  # N: A without research (sensitivity control)
    Arm("effort-high", high_effort(SHADOW_CONFIGS["cheap"])),      # reasoning-effort 2x2 (own card, not EXP-017)
    Arm("writer-effort-high", high_effort(SHADOW_CONFIGS["exp013-c-writer"])),
)}
LANE_ONLY = ("cheap-aa", "no-research", "effort-high", "writer-effort-high")
