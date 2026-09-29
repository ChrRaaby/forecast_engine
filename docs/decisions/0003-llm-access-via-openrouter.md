# 0003: LLM access via OpenRouter

- **Status:** Accepted
- **Date:** 2026-09-29

## Context
The ensemble uses models from several vendors (OpenAI, Anthropic, Google; R-04). Each direct API needs its own account, key and
billing. The template supports OpenRouter out of the box.

## Options considered
1. **OpenRouter for everything**: one key, one bill, one spend limit, all vendors. Cons: small markup; batch APIs (−50%) aren't
   available through it, which matters for backtests (research/03).
2. **Direct vendor APIs**: batch discounts, vendor credits apply directly. Cons: three keys and three bills to manage.
3. **Mix**: OpenRouter for live, direct APIs where credits or batch discounts apply.

## Decision
Option 1 for M1: key "FutureEvalKey", $20 total spend limit (temporary, raised when the Metaculus grant arrives). Move to option 3
when donated vendor credits arrive (B-01) or when M2 backtests make batch pricing worth it; that switch gets its own ADR.

Rules borrowed from nostreambot: all model IDs live in one file, checked against the live OpenRouter model list, never written from
memory. Donated credits go on a separate key with a guard that it's only used for Metaculus work.

## Consequences
- Simple setup and one hard spend limit.
- Backtests at scale may cost up to ~2× what batch APIs would; revisit before M2 experiments.
