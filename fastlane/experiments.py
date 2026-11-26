"""Experiments that run in the FAST lane, as their cards fix them.

A run serves one primary experiment and may let others join it on the same questions (`plan`): every arm runs once, its cost goes
to the line of the first experiment that lists it, and the shared research goes to the primary's line. A joined experiment whose
cap is used up loses only its own arms.

EXP-017 (docs/experiments/EXP-017-fast-lane-calibration.md), EXP-018 (docs/experiments/EXP-018-reasoning-effort-2x2.md): arms, spend
caps, picking stops and looks. Changing a value here after a card is frozen needs a card amendment first.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

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
    # a 2x2 card's primary: the mean over these (treatment, control) differences, one test at 95%; the 2nd minus the 1st is its
    # interaction (described only)
    contrast: tuple[tuple[str, str], ...] = ()
    second_look_n: int | None = None  # one more look at this many, only if the card's rule allows it

    @property
    def level(self) -> float:
        return 1.0 - 0.05 / max(1, len(self.candidates))


EXPERIMENTS: dict[str, Experiment] = {
    "EXP-017": Experiment(
        name="EXP-017", arms=("cheap", "cheap-old-prompt", "exp013-c-writer", "cheap-aa", "no-research"),
        candidates=("cheap-old-prompt", "exp013-c-writer"), aa_control="cheap-aa", sensitivity_control="no-research",
        cap_usd=45.0, last_pick_day=date(2027, 1, 20), max_picked=320, look_n=280, look_day=date(2027, 1, 31), min_n=140),
    "EXP-018": Experiment(
        name="EXP-018", arms=("cheap", "effort-high", "exp013-c-writer", "writer-effort-high"),
        candidates=(), aa_control=None, sensitivity_control=None,
        cap_usd=25.0, last_pick_day=date(2027, 1, 20), max_picked=320, look_n=280, look_day=date(2027, 1, 31), min_n=140,
        seed=1018, contrast=(("effort-high", "cheap"), ("writer-effort-high", "exp013-c-writer")), second_look_n=560),
}
# How many pilot questions EXP-018 uses before it is frozen, and its pass rule (card)
EFFORT_PILOT_QUESTIONS = 10
EFFORT_PILOT_MIN_OK = 9
EFFORT_PILOT_MAX_SECONDS = 300.0


def plan(names: Sequence[str]) -> tuple[tuple[str, ...], dict[str, str]]:
    """Arms for a run serving these experiments (the first is primary), and each arm's owner (whose line pays for it).
    Every experiment must start from the same baseline arm."""
    exps = [EXPERIMENTS[n] for n in names]
    if not exps:
        raise ValueError("no experiment")
    if len({e.arms[0] for e in exps}) != 1:
        raise ValueError(f"experiments with different baselines can't share a run: {[e.arms[0] for e in exps]}")
    arms: list[str] = []
    owners: dict[str, str] = {}
    for e in exps:
        for a in e.arms:
            if a not in owners:
                owners[a] = e.name
                arms.append(a)
    return tuple(arms), owners


DEFAULT_EXPERIMENT = "EXP-017"
