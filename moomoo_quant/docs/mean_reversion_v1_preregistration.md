# US ETF Short-Term Mean Reversion v1 Pre-registration

Created before the first JPY-denominated full-history research run. This version
must not be overwritten after observing its result.

## Hypothesis

Liquid broad US equity ETFs can show a short-lived rebound after an unusually
oversold close while their long-term trend remains positive. The expected edge
comes from temporary liquidity pressure and behavioral overreaction, and is
intentionally different from monthly trend following.

## Fixed specification

- Strategy ID: `us-etf-short-term-mean-reversion`
- Version: `1`
- Research asset: SPY only
- Instrument type: unlevered US equity ETF
- Direction: long only; JPY cash otherwise
- Signal currency: USD
- Reporting and ledger currency: JPY
- Long-term regime: close above SMA(200)
- Entry: RSI(5) below 25
- Exit priority: RSI(5) above 55, then five trading days held, then close 2% below entry execution price
- Position size: at most 25% of the strategy account's current cash
- Quantity: theoretical fractional shares; whole-share estimate is display-only
- Signal timing: daily close
- Theoretical execution: next US trading-day open
- Costs: configured commission, 5 bps entry/exit slippage, and 10 bps FX conversion cost per transaction
- Data: adjusted SPY OHLCV plus backward-aligned USDJPY; execution FX must be strictly prior-known
- Initial research capital: JPY 100,000
- No shorting, margin, leverage, options, broker order, REAL environment, or SIMULATE order

## Predefined acceptance gates

All gates must pass before SHADOW is permitted:

1. Data alignment and next-open execution tests pass.
2. Net trade expectancy is positive after all configured costs.
3. Out-of-sample Sharpe from 2022 onward is positive.
4. Absolute monthly-return correlation with Trend v1 is below 0.70.
5. Results are not dependent on one isolated year, judged from yearly output.

Failure keeps the strategy in `RESEARCH` with a recorded rejection reason. No
parameter search or threshold change is allowed for v1.
