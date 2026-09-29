# forecast_engine

> An LLM-based judgmental forecasting bot: it researches questions about real-world events and outputs calibrated probabilities with explanations. It targets Metaculus tournaments and is inspired by the laertes bot that won the Summer 2026 Metaculus Cup.

## Status
Phase: **M1 in progress**: bot built and dry-run, not live yet. Latest session log: `docs/log/2026-09-30.md`

## Where things live
| Path | Purpose |
|---|---|
| `docs/spec.md` | Living spec: problem, scope, requirements, architecture, milestones. The single source of truth. |
| `docs/research/` | Research notes and reports, one file per question |
| `docs/evaluation-protocol.md` | How every change is evaluated (governing rule, threat model, statistics, decision rules) |
| `docs/experiments/` | Experiment cards + generated results; HOLDOUT touch log |
| `docs/backlog.md` | Prioritised backlog (Now / Next / Later / Parked) |
| `docs/playbook-tracker.md` | Playbook recommendations → features → status → results |
| `docs/inbox.md` | Raw brain-dump of ideas and constraints; append freely |
| `docs/ideas.md` | Triaged register of ideas/constraints, with Claude's assessment |
| `docs/log/` | One short log per working session: what was done, decisions, next actions |
| `docs/session-prompts/` | Ready-to-paste prompts for Claude Code sessions |
| `docs/decisions/` | Architecture Decision Records (ADRs), one per significant decision |
| `CLAUDE.md` | Instructions and conventions for Claude Code working in this repo |

## Workflow
1. **Research**: capture findings in `docs/research/`, then summarise conclusions in the spec.
2. **Decide**: record each significant choice as an ADR in `docs/decisions/`.
3. **Plan**: keep milestones and open questions current in `docs/spec.md`.
4. **Build**: work milestone by milestone with Claude Code (plan first, then implement).
