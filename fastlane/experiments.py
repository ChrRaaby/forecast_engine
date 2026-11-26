"""Experiments that run in the FAST lane, as their cards fix them. A run serves exactly one experiment.

EXP-017 (docs/experiments/EXP-017-fast-lane-calibration.md): arms, spend cap, picking stops and the one look. Changing a value here
after the card is frozen needs a card amendment first.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# One host per open-weight model inside an experiment (protocol §5, B-93): the host EXP-015 and EXP-016 used. Wafer serves it
# quantized (mxfp4, OpenRouter's endpoint list 2026-11-26); kept for comparability with those runs and named on the card.
PINNED_HOSTS = {"deepseek/deepseek-v4.1-flash": "wafer"}


@dataclass(frozen=True)
class Experiment:
    name: str
    arms: tuple[str, ...]  # arms[0] is the baseline A
    candidates: tuple[str, ...]  # compared with A at 1 - 0.05/m
    aa_control: str | None  # compared with A at 95%: must show nothing
    sensitivity_control: str | None  # compared with A at 95%: must show a loss
    cap_usd: float  # booked spend (Google's fee at list price) for this experiment, inside the lane's $80
    last_pick_day: date  # no new questions after this day
    max_picked: int  # no new questions once this many were picked
    look_n: int  # the one look: this many complete resolved questions ...
    look_day: date  # ... or this day, whichever is first
    min_n: int  # below this at the look: counts only, no verdict
    seed: int = 1017

    @property
    def level(self) -> float:
        return 1.0 - 0.05 / max(1, len(self.candidates))


EXPERIMENTS: dict[str, Experiment] = {
    "EXP-017": Experiment(
        name="EXP-017", arms=("cheap", "cheap-old-prompt", "exp013-c-writer", "cheap-aa", "no-research"),
        candidates=("cheap-old-prompt", "exp013-c-writer"), aa_control="cheap-aa", sensitivity_control="no-research",
        cap_usd=45.0, last_pick_day=date(2027, 1, 20), max_picked=320, look_n=280, look_day=date(2027, 1, 31), min_n=140),
}
DEFAULT_EXPERIMENT = "EXP-017"
