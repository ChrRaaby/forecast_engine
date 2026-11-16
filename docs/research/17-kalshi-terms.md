# Kalshi API terms: decision to exclude (2026-11-16)

**Decision (Christian, 2026-11-16):** Kalshi is out of the FAST lane (EXP-017) and of any new data collection. Manifold is out too.

## Analysis supplied by Christian, recorded as given
Source: an analysis Christian pasted into the project thread on 2026-11-16. It is not a quotation of the agreement, and Claude has
not been able to check it against the text: kalshi.com/legal returns HTTP 429 to automated fetches, and the Developer Agreement PDF
([Kalshi-Developer-Agreement.pdf, v1.1](https://kalshi-public-docs.s3.amazonaws.com/Kalshi-Developer-Agreement.pdf)) would not decode.

> Under Kalshi's standard Developer Agreement, using their APIs strictly to collect and store data for an external hobby project,
> public data product, dashboard, machine learning model, or a third-party application is generally not permitted without explicit
> written authorization.

1. **Purpose limitation:** API access and data use are for facilitating your own trading on their exchange, not data harvesting or
   building standalone analytics tools.
2. **Redistribution and public display:** publishing collected data on a public site, building a public dashboard, or feeding data
   into external software or machine-learning/AI workflows generally violates the data terms without a licence or written consent.
3. **Automated scraping and rate limits:** public endpoints can be fetched with GET, but automated or high-frequency harvesting that
   breaks the acceptable-use policy, limits or terms can get keys revoked or IPs blocked.

Allowed in practice: personal backtesting for your own trading inside your own account; the demo environment (`demo.kalshi.com`)
with mock funds.

## Consequences for this repo
- FAST lane: Polymarket and code-made data series only.
- **Open:** `evals/markets.py` (EXP-012 market matcher) already calls Kalshi's public API and caches responses under
  `data/markets/kalshi` and `data/markets/raw/api-elections-kalshi-com` in the private data repo. By the analysis above that use
  is not covered. Needs a decision (stop fetching, delete cached Kalshi data, drop Kalshi from EXP-012's venue list).
