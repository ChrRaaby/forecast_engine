"""FAST lane (EXP-017 screen): short-horizon questions that resolve in days, so a prompt/research change gets evidence in weeks.

Questions come from two sources, both public and read-only, both closing 3-7 days after they are asked:
  A  Polymarket markets (non-sports, real liquidity). The market's price is never read into a question or a prompt.
  B  Questions made by code from a free price/rate feed (crypto daily closes, ECB reference rates), resolved from the same feed.
Kalshi is excluded: its API terms bar collecting data for projects like this (Christian, 2026-11-16). Manifold is excluded too.

Nothing here touches forecast_engine/*.py, main.py or bot_helpers.py, so the live code fingerprint (ADR-0011) is unchanged.
Results of this lane are screens only: they never change the live bot without evidence under docs/evaluation-protocol.md.
"""
