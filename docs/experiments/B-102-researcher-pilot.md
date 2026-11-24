# B-102: Researcher pilot (can a self-directed researcher find the figure a question resolves on?)

_Card written before any paid call, 2026-11-24. Approved by Christian 2026-11-15 (15 questions, at most $3.25, $0.15 a question); build
started on his message of 2026-11-23. The paid run needs a second "go" naming up to $3.25. No forecasts, no scores._

## Why
EXP-013's source block reads the resolution source with a plain download. On 234 tournament questions since 15 Oct about half had a
readable page; 9% were JavaScript-only, 10% index or landing pages, about 30% had no usable link. In the first week it found a value on
20% of questions.

## Readers (same block, same validator)
| Reader | How | Limits |
|---|---|---|
| R0 | EXP-013's source step as merged, imported | as EXP-013 |
| R1 | Each resolution link in a real browser (20 s), then R0's extraction on the rendered text | as EXP-013 |
| R2 | gemini-3.8-flash, at most 8 steps: search (Google grounding, at most 4), open, follow, calc | $0.15 in tokens, 4 minutes |
| R3 | anthropic/claude-sonnet-5.5 with Anthropic's web search, one call, at most 2 searches | $0.15, 4 minutes |

Block: `current_value`, `as_of_date`, `source_url`, `quote` (at most 200 characters, word for word on the fetched page: EXP-013's check),
`history` (at most 12 dated rows with links), `developments` (at most 5), `not_found`. A figure without link and date is refused, not
repaired. A refused block is kept in the sheet with the reasons. R0/R1 keep EXP-013's 300-character quote limit.

## Step 0 (free, 2026-11-24)
- **Models with built-in search through OpenRouter:** the current OpenAI, Anthropic, Google and xAI models list one; fee per search
  Anthropic and OpenAI $0.010, Google $0.014, xAI $0.005 (live list 2026-11-24). The docs say the fee comes on top of tokens; they do not say whether it is
  inside the returned cost, so the tool counts the higher of returned cost and list price, and the first paid call settles it.
  `max_uses`, the only way to bound a one-call reader beforehand, is honoured by Anthropic only. Hence R3 = Sonnet 5.5 ($2/$10 per M,
  on the frontier roster). Opus 5.5 at twice the token price would likely pass $0.15 (estimate).
- **Browser on the PC:** three of three JavaScript-only links gave the figure a person sees (Power BI 1,734 active cases at 22/11/2026;
  Texas DMV "1-20 of 1507 rows"; Netflix 7,300,000 views for the week to 15 Nov), in 2 to 9 seconds (all three compared with a screenshot; pages chosen, not drawn).
- **Classifier:** it was a throw-away script in the EXP-013 thread's temporary folder, now `evals/source_links.py`. It runs on any
  question. Two corrections: short JSON answers and ordinary text with brackets (Wikipedia) were called JavaScript-only. Rerun on
  the 22 JavaScript links of 15 Nov (now): 19 still JavaScript-only, 2 readable, 1 blocked.

## Questions
"Open" is read as **outcome not due yet** (scheduled resolution at least a day away): tournament questions take forecasts for a few hours
only, and on 2026-11-24 none was open in that sense. Drawn from FutureEval / MiniBench questions about a number (pattern in
`is_number_question`), 5 per group, seed 1100. A short group stays short. Drawn again on the day of the run and shown to Christian
before any paid call; a question with no current figure anywhere yet can be excluded by hand at that point, with its reason saved.

## Safety
`open` takes http(s) links only, refuses local and private addresses, unusual ports, credentials in links, prediction-market sites (T7; no
Kalshi data) and files over 5 MB. A local proxy repeats the address check on every connection the browser makes, so redirects and page
scripts are covered. Page text is passed as data; the tools can only search, open, follow and calculate.

## Pass rules (fixed)
A reader passes if it stays under the caps, has **no invented figure** in 15 questions (quote not in the fetched text, or different from the
hand check, while not saying "not found") and finds the correct figure on **at least 8 of 15**. R1 solving the JavaScript group (4 or 5
of 5) is its own finding: a small fix proposal for EXP-013's source step. Counts per group, never percentages (5 per group is small).
Unverifiable quotes (R3 cites a page that cannot be opened here) count as refused; the hand check decides whether the figure was wrong.

## Reported back, not decided
Which reader, if any, becomes the shadow arm "researcher brief instead of today's research" (own card); whether its history table beats
numbers desk part H's history step; real cost per question for R2 ($0.03–0.06 estimated) and R3 ($0.10–0.15 estimated).

## Results
_(after the run)_
