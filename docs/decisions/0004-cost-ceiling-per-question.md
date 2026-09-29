# 0004: Cost ceiling of $0.50 per question

- **Status:** Accepted
- **Date:** 2026-09-29

## Context
The original PoC criterion was ≤ $0.10/question (I-08). The cost model (research/03) showed the designs the research favours cost
$0.30–0.80/question, so $0.10 would lock us into the configuration that loses. Top-15 bots spend ~$1.40/question (R-10).

## Options considered
1. **$0.10/question**: fits the $100 PoC easily. Cons: only scenario B is affordable.
2. **$0.50/question**: allows a mid-tier ensemble (scenario C, ~$0.32–0.38); a 400-question season ≤ $200. Cons: needs the grant or credits for a full season.
3. **No cap**: Cons: no discipline; "better" could just mean "more expensive".

## Decision
Option 2 (confirmed by Christian 2026-09-29). The passing configuration must cost ≤ $0.50/question at live prices, and the PoC itself
must stay within $100 + credits. M1 runs scenario B (~$0.05–0.10/question), well under the ceiling. The current $20 OpenRouter limit is
temporary until the Metaculus grant ($100–500 expected) arrives.

## Consequences
- Cost is a first-class metric in every experiment (evaluation protocol §4): an improvement that breaks the cap isn't one.
- The cap may limit our ceiling versus top bots (R-10). Revisit at the PoC gate with measured numbers.
