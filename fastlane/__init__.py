"""FAST lane (EXP-017 screen): short-horizon questions that resolve in days, so a prompt/research change gets evidence in weeks.

Questions come from two sources, both public and read-only, both closing 3-7 days after they are asked:
  A  Polymarket markets (non-sports, real liquidity, priced 10-90% when picked). The price is stored apart and never shown to a model.
  B  Number questions made by code from free feeds (temperature, earthquakes, crypto closes, ECB rates), resolved from the same feeds.
No other venue is used (Christian, 2026-11-16).

Nothing here touches forecast_engine/*.py, main.py or bot_helpers.py, so the live code fingerprint (ADR-0011) is unchanged.
Results of this lane are screens only: they never change the live bot without evidence under docs/evaluation-protocol.md.
"""
