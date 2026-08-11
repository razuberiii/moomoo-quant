# JPY Multi-Asset Trend v1 Frozen Specification

Status: frozen. Any logic change requires a new `JPY Multi-Asset Trend v2`.

- Strategy ID: `jpy-multi-asset-trend`
- Assets: SPY, QQQ, GLD, IEF and JPY cash
- Signal currency: JPY
- Base/reporting currency: JPY
- Signal frequency: month-end close, excluding an incomplete current month
- Momentum: 12 monthly observations
- Trend filter: JPY close above 10-month simple moving average
- Eligible: momentum is positive and trend filter is true
- Ranking: descending JPY momentum, symbol ascending as deterministic tie-breaker
- Allocation: top two eligible assets receive 50% each
- One eligible asset: 50% asset and 50% JPY cash
- No eligible assets: 100% JPY cash
- Theoretical execution: next common US trading-day open
- Long-only, fractional theoretical quantities, no borrowing, no shorting, no leverage, no options
- Cost model: configured commission, slippage and FX conversion costs

The golden-master tests lock the parameters, latest complete signal, selected
historical signals, crisis observations and primary performance statistics.
