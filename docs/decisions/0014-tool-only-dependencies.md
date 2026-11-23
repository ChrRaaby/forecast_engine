# 0014: Researcher pilot: Playwright and pypdf outside the lock file

- **Status:** Proposed (accepted with the B-102 pull request)
- **Date:** 2026-11-24

## Context
The researcher pilot (B-102) has to read pages that only show their figures after JavaScript has run, and PDFs behind index pages.
That needs a browser driver (Playwright) and a PDF reader (pypdf). `poetry.lock` is part of the bot's code fingerprint (ADR-0011) and
is installed on every live run.

## Options considered
1. **Add both to `pyproject.toml` / `poetry.lock`** (optional group): reproducible, but it changes the fingerprint of the live bot
   for something the bot never imports.
2. **Install by hand where the pilot runs** (`pip install playwright pypdf`, tested with playwright 1.65.2 and pypdf 7.1.1), imported
   only when a page is opened. Tests use fakes, so CI needs neither.

## Decision
Option 2 for the pilot. The browser is the Google Chrome already on the PC, so no browser download is needed.

## Consequences
The live bot, its CI and its fingerprint are untouched. Versions are not pinned: acceptable for a one-evening pilot. If a reader becomes
a shadow arm in GitHub Actions, the dependency goes into the lock file then, with its own code version, and the run time of a browser
there has to be measured first (unchecked).
