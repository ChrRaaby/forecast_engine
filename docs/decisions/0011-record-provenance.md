# 0011: Record provenance: every forecast names the code, prompts and settings that made it

- **Status:** Accepted
- **Date:** 2026-11-03

## Context
Christian wants to be certain, for every live forecast, which version of the bot made it: code, harness, prompts, models and
settings. Until now a record stored `config_version` (a label typed by hand) and `config_hash` (a hash of `BotConfig`). Neither
covers the code: a change to parsing, guards, the polarity check or the CDF conversion could alter a submitted forecast without
anyone bumping the label, and records named no git commit, no prompt template (PR #18 had to infer it), no library versions, no
OpenRouter host and not the CDF actually posted (B-71). Design doc: https://claude.ai/artifact/7c2seJZqVg52CJA6H6y2Tc.

## Options considered
1. **Rely on `CONFIG_VERSION` discipline**: no work; but it is a person's memory, and nothing checks it.
2. **Require a `CONFIG_VERSION` bump for every code change**: strict, but couples harmless code edits to the forecast label (it
   resets the retry ledger and needs a new prompt-archive mapping) and still names no commit.
3. **Provenance block + registered code fingerprint** (chosen): each record carries its own provenance; the code is identified by a
   fingerprint that must be registered, separate from the forecast label.

## Decision
Option 3 (`forecast_engine/provenance.py`):
- Each run gathers a manifest (git commit, clean checkout, code fingerprint, Python + library versions, GitHub run), written to
  `data/forecasts/<run_id>/manifest.json`; every live and shadow record stores it plus its own settings (`config_hash` and the full
  `BotConfig`), prompt versions (forecast, polarity check, research follow-up), role (live / live-fallback / shadow), roster, and a
  `version_id` = hash of code fingerprint + runtime + settings + prompts (not of the run, so equal versions compare equal).
- The code fingerprint covers `forecast_engine/*.py`, `main.py`, `bot_helpers.py` and `poetry.lock`
  (exact text, LF line endings, so the same on every OS and Python version; even a comment edit counts). Each fingerprint must be in `forecast_engine/code_versions.json` (append only); `tests/test_provenance.py` fails
  otherwise, so CI is red on an unregistered change. Because cron runs `main` even when CI is red, the bot also checks at run start:
  if unregistered it still forecasts and publishes (Christian, decision 1) and flags every record `version_unregistered`; the
  monitor shows a warning.
- The polarity prompt gets `POLARITY_PROMPT_VERSION`, pinned by hash in the test. Records store `prompt_version` (B-75(1)) and
  `submitted` (B-71). Model calls store OpenRouter's routed host and generation ID, Gemini's model version and response ID, only
  when the provider returns them. OpenRouter hosts are recorded, not pinned (decision 2). The private comment shows the version
  ID (decision 3).
- Records stay append-only: older records are never edited; their commits are reconstructed from archived run logs into the data
  repo (decision 4), marked as reconstructed.

## Consequences
- Every forecast from this change on can be traced to a commit, a registered code version and its exact settings and prompts, and two
  forecasts can be compared by `version_id` alone.
- Every code change to the engine needs one line in `code_versions.json` (the failing test prints it). Forecast-altering changes
  still bump `CONFIG_VERSION` as before.
- Search engines behind AskNews and Gemini cannot be versioned; their output stays stored in full, which is what replay uses.
- No forecast, prompt or `config_hash` changes; older records replay as before.
