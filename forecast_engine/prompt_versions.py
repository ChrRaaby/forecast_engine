"""Which forecast prompt a config gets (B-75(2), Christian 2026-11-07): the config's `prompt_version` picks it.

- `LIVE` is the version that `forecast_engine/prompts.py` renders. Every published config (DEFAULT, TOURNAMENT, WIDE) must name it
  (the drift guard in evals/prompt_archive.py checks this).
- `VARIANTS` are experimental prompts, one module each in `forecast_engine/prompt_variants/`. A replay arm or a shadow arm names
  one in its `prompt_version`, so a prompt experiment runs on frozen research before anything goes live (protocol §7).
- An unknown version is refused before any model call; it is never silently replaced by the live prompt.

Adding a variant, in one commit:
  1. write `forecast_engine/prompt_variants/<name>.py` with `forecast_prompt(q, research, today)`;
  2. add it to VARIANTS below, under its version string;
  3. archive a frozen copy in evals/ and add it to `evals.prompt_archive.ARCHIVE` (the drift guard prints the hashes);
  4. register the new code fingerprint (tests/test_provenance.py prints the line for forecast_engine/code_versions.json).
Making a variant the live prompt is a normal prompt change: edit prompts.py, set LIVE and `BotConfig.prompt_version` to the new
version, bump CONFIG_VERSION (see evals/prompt_archive.py).
"""
from __future__ import annotations

from collections.abc import Callable
from types import ModuleType

from .prompt_variants import dates_yes_2026_11_07
from .schema import QuestionSnapshot

LIVE = "template-2026-09-26"  # what forecast_engine/prompts.py renders; prompts ported from metac-bot-template c16d91f

# prompt version -> module with forecast_prompt(q, research, today). Append only (records name these versions).
VARIANTS: dict[str, ModuleType] = {
    dates_yes_2026_11_07.VERSION: dates_yes_2026_11_07,  # EXP-002 (B-09b): scheduled resolution date + "resolves YES" wording
}

PromptBuilder = Callable[[QuestionSnapshot, str, str], str]


class UnknownPromptVersion(ValueError):
    """A config names a prompt version that is neither the live prompt nor a registered variant."""


def variant_builder(version: str) -> PromptBuilder:
    if version not in VARIANTS:
        raise UnknownPromptVersion(
            f"prompt_version {version!r} is neither the live prompt ({LIVE!r}) nor a registered variant "
            f"({sorted(VARIANTS) or 'none'}); see forecast_engine/prompt_versions.py")
    return VARIANTS[version].forecast_prompt


def known_versions() -> list[str]:
    return [LIVE, *VARIANTS]
