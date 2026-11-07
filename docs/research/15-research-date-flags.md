# 15. Research date flags: first count on stored records (B-51a)

Date: 2026-11-07. Offline, $0, no model calls. Tool: `tools/count_research_flags.py` (read-only, prints counts and matched date
phrases only; no article text). Checker: `forecast_engine/research_checks.py`. Flags are stored in `research.quality_flags` of new
records; the prompt text is unchanged, so **forecasts are unchanged**.

## Flags (reference date = the bundle's `as_of`)

| Flag | Fires when | Notes |
|---|---|---|
| `weekday_mismatch` | "Friday, October 4, 2026", "Fri, Oct 4", "Friday 4 October" with a weekday that is wrong for that date | no year: the `as_of` year, or the year before if that puts the date > 60 days after `as_of` |
| `published_after_as_of` | item `published_at` is later than `as_of` | naive timestamps taken as UTC |
| `future_weekday_date` | a weekday+date phrase after `as_of` | plain future dates without a weekday are not flagged |
| `stale_season_year` (soft) | "MY 2024/25", "2024/25 marketing year", "crop year 2024-25", "FY2024", "fiscal year 2024" with start year < `as_of` year − 1 | single years: the year itself; "2024/25 season" is deliberately not matched (sports history) |

Judgement call: the brief's wording ("period ended before the as_of year minus 1") would not flag "2024/25 marketing year" for an
`as_of` in 2026 (it ends in 2025), but the brief's own test case says it must. I used the **start** year (2024 < 2025) for ranges.

## Count on stored records (data/runs, excluding `test_questions`)

n = 609 records (360 unique questions), 4,840 research items (4,231 asknews_latest, 609 gemini_grounded; no follow-up items stored yet).

| | Count |
|---|---|
| Records with at least one flag | 180 (29.6%) |
| Items with at least one flag | 288 (6.0%): asknews_latest 235 of 4,231, gemini_grounded 53 of 609 |
| `weekday_mismatch` | 86 flags in 40 questions (asknews 84, gemini 2) |
| `published_after_as_of` | 0 |
| `future_weekday_date` | 399 flags in 81 questions (asknews 323, gemini 76); 380 of the 399 have a correct weekday |
| `stale_season_year` | 5 flags in 2 questions (all asknews) |
| `check_error` | 0 |

By config_version, flagged records: frontier-2026-10-08 154 of 458; m1-baseline-2026-10-01 7 of 18; -10-03b 10 of 56; -10-05 5 of 48;
-10-06 4 of 29.

## What this shows and doesn't

- `weekday_mismatch` is the clean signal. A mismatch is arithmetic, so each flag is a genuinely wrong weekday in the text (e.g. "Wednesday,
  October 2, 2026"; 2 Oct 2024 was a Wednesday, which suggests text from another year). 40 of 360 questions have at least one.
  I did not read the surrounding articles, so whether the *content* is stale, or just a typo, is unchecked.
- `future_weekday_date` is mostly noise: 380 of 399 have a correct weekday, so they are real scheduled events ("Sunday, October 4,
  2026" in a bundle from 1 October). Only the ~19 whose weekday is also wrong are suspect. Treat this flag as informational; a
  tighter rule would be "future and wrong weekday", which `weekday_mismatch` already covers.
- `stale_season_year` is rare (5 flags, 2 questions) and the examples are mostly legitimate history (46382: "fiscal year 2014",
  "fiscal year 2020" quoted as background). Only 46081's "2024/25 marketing year" looks like the stale case from research/09.
- `published_after_as_of` never fires: consistent with the as-of guard already holding.
- Not shown: whether flagged items **cost accuracy**. The count says how often the text looks off, not that a forecast was moved by it.
  The score comparison (flagged vs unflagged questions) is B-51b's gate and needs resolved outcomes; n is small (360 questions, 40
  with a real mismatch).
- Big-miss overlap (B-72): the weekly miss-review list lives in the private data repo, which is not in this checkout, so I did not
  scan it. Checking the question ids named in the backlog (46133, 46134, 46382, 46139, 46083, 46076): only 46382 has any flag,
  and those are the historical-fiscal-year false positives above. This covers only the ids quoted in the backlog, not the full list.
