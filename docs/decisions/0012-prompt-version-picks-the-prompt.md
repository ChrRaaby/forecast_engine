# 0012: The prompt version picks the forecast prompt

- **Status:** Accepted
- **Date:** 2026-11-07

## Context
`forecast()` built every prompt from `forecast_engine/prompts.py` and ignored `BotConfig.prompt_version`. A prompt experiment (e.g.
EXP-002, dates and explicit YES wording) could therefore not be replayed on frozen research or run as an unpublished shadow arm:
the new prompt had to go live before it could be measured. Protocol §7 asks for reasoning-only changes to be compared on identical
research. Found while fixing the F-13 replay trap (PR #18, B-75).

## Options considered
1. **Prompt version picks the prompt** (chosen by Christian, 2026-11-07): each version is a module; configs name one; versions are
   archived and drift-guarded. Small live-code change; every record names its exact prompt.
2. **Experiment-only prompt hook**: replay code passes in a different prompt builder; live code untouched. Smaller, but the prompt is
   a function, not a version, so records and shadow arms can't name it reliably.
3. **Keep as is**: a new prompt must go live first; EXP-002's ~$5 replay test stays impossible.

## Decision
Option 1. `forecast_engine/prompt_versions.py`: `LIVE` is what `prompts.py` renders; `VARIANTS` maps experimental versions to modules
in `forecast_engine/prompt_variants/`. `forecast()` uses `prompts.py` when `cfg.prompt_version == LIVE`, the registered variant
otherwise, and refuses an unknown version before any model call. The drift guard (`evals/prompt_archive.py`) requires published
configs (DEFAULT, TOURNAMENT, WIDE) to use `LIVE`, allows variants only in shadow/experiment arms, and requires a frozen archive
copy of every variant that renders identically and whose code fingerprint is pinned. Variant modules are part of the code
fingerprint (ADR-0011).

## Consequences
- A prompt experiment = one variant module + archive entry + an arm config; it can replay on archived records (`evals.replay`) or
  run live as an unpublished shadow, compared with the same research as the live prompt.
- Adopting a variant is still a normal live prompt change (edit prompts.py, bump `LIVE`, `prompt_version` and `CONFIG_VERSION`).
- Live forecasts are unchanged by this decision (byte-identical prompts, same `config_hash`).
