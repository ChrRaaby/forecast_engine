# 06: Trading side branch (2026-10-03)

**Question:** Christian asked how to approach a trading side branch, prompted by Metaculus's
[Trading Plan for the Trade Signal Tournament](https://www.metaculus.com/notebooks/7778/trading-plan-for-the-trade-signal-tournament/).
What is the cheapest honest way to find out whether the bot's forecasts could make money? (I-15, B-25.)

## Facts
- The notebook (TomL, 2021-09-10) belongs to the 2021 [Trade Signal Tournament](https://www.metaculus.com/tournament/trade-signal-tournament/):
  Metaculus questions on ~9 US economic indicators (CPI, jobs etc.), $1,500 prize pool. A volunteer "Community Trader",
  elected by the community, turned the forecasts into options trades on a retail platform, using the prize pool as stake;
  Metaculus guaranteed a $1,000 floor ([announcement](https://metaculus.medium.com/can-metaculus-create-a-trading-signal-8131b7da6cac)).
  The notebook body itself could not be fetched (Metaculus blocks automated reads), so the trade-level plan is not summarised here.
- Outcome: the pool was [$1,495.40 on 2021-09-30](https://www.metaculus.com/questions/7465/trade-signal-tournament-prize-pool-2021-09-30/)
  (roughly flat), and ["Will more than half of the remaining trades make money?"](https://www.metaculus.com/questions/8003/trade-signal-metaculus-trades-make-money/)
  resolved **No**.

## Judgement
- **Accuracy is not edge.** A trade only pays if the forecast beats the *price*, after fees and spread. Scheduled macro
  releases are priced by professional consensus, and options add a volatility-pricing problem on top of the probability.
  The 2021 result is what you'd expect from that, so don't copy the plan.
- **Binary prediction markets** (Polymarket, Kalshi, Manifold) are the natural venue: the price *is* a probability, so the
  bot's output maps straight to a position with no volatility model in between.
- The right metric is **edge vs. the market price at forecast time**, not calibration. A well-calibrated bot can still lose
  money against a sharper market.
- Keep the market price **out of the prompt** in the trading arm, so the forecast is independent of the price it is compared
  against. B-13 (market prices as a research source) is a separate question: it may improve accuracy but makes the
  forecast useless as a trading signal.

## Recommended approach
1. **Shadow mode, no money.** Add liquid binary markets as a question source for the existing core (like `--mode wide`).
   Snapshot the market price (mid, bid/ask, volume) when the bot forecasts. After resolution, simulate trades with
   fractional Kelly sizing, fees and spread. Read-only public APIs; nothing is placed. (B-41, B-42)
2. **Backtest against price history, if available.** Polymarket and Manifold publish price histories (unverified for
   our purpose), which could give paired forecast/price data without waiting for resolutions. Same leakage rules as
   the evaluation protocol apply. (B-43)
3. **Go/no-go gate.** A few hundred resolved paired forecasts with positive edge after costs and a confidence interval
   that excludes zero (reuse the B-31 bootstrap). Only then the Denmark legal and tax check (spec §9) (B-44), and only
   then tiny real stakes, with Christian's explicit go-ahead.

Sequencing: after the M2 harness (B-29 scorer, B-31 reports, B-38 replay), which this branch reuses. It does not compete
with the tournament bot for priority.

## Conclusion
Trading stays a non-goal for v1 (spec §3). The side branch is a paper-trading measurement of edge vs. market, with a
gate before any money is involved. Backlog: B-41…B-44 ("Side branch: trading" section).
