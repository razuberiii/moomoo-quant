from __future__ import annotations

import math

from .models import AggregatedTarget, MarketPrice, StrategyContribution, TargetRequest


class PortfolioError(ValueError):
    pass


def aggregate_targets(
    requests: list[TargetRequest],
    prices: dict[str, MarketPrice],
    current_attribution: dict[tuple[str, str], float],
    portfolio_capital_jpy: float,
) -> list[AggregatedTarget]:
    contributions: dict[str, list[StrategyContribution]] = {}
    for request in requests:
        if request.allocated_capital_jpy < 0 or request.cash_jpy < 0:
            raise PortfolioError("Strategy capital and cash must be non-negative")
        if sum(request.target_weights.values()) > 1.0 + 1e-9:
            raise PortfolioError(f"{request.strategy_id} target weights exceed 100%")
        if any(weight < 0 for weight in request.target_weights.values()):
            raise PortfolioError("Short target weights are forbidden")
        for symbol, weight in request.target_weights.items():
            if symbol not in prices:
                raise PortfolioError(f"Missing price for {symbol}")
            quote = prices[symbol]
            unit_jpy = quote.price_usd * quote.usdjpy
            if unit_jpy <= 0:
                raise PortfolioError(f"Invalid JPY unit price for {symbol}")
            notional = request.allocated_capital_jpy * weight
            quantity = notional / unit_jpy
            contributions.setdefault(symbol, []).append(
                StrategyContribution(
                    strategy_id=request.strategy_id,
                    symbol=symbol,
                    target_weight=weight,
                    target_notional_jpy=notional,
                    target_quantity_fractional=quantity,
                    target_quantity_whole=math.floor(quantity),
                    current_quantity=current_attribution.get((request.strategy_id, symbol), 0.0),
                )
            )

    output = []
    for symbol, parts in sorted(contributions.items()):
        quote = prices[symbol]
        target_qty = sum(part.target_quantity_fractional for part in parts)
        current_qty = sum(part.current_quantity for part in parts)
        target_notional = sum(part.target_notional_jpy for part in parts)
        output.append(
            AggregatedTarget(
                symbol=symbol,
                target_quantity_fractional=target_qty,
                target_quantity_whole=math.floor(target_qty),
                target_notional_jpy=target_notional,
                current_quantity=current_qty,
                current_value_jpy=current_qty * quote.price_usd * quote.usdjpy,
                proposed_net_change=target_qty - current_qty,
                target_weight=target_notional / portfolio_capital_jpy if portfolio_capital_jpy else 0.0,
                price_usd=quote.price_usd,
                usdjpy=quote.usdjpy,
                data_timestamp=quote.market_timestamp,
                fx_timestamp=quote.fx_timestamp,
                contributions=parts,
            )
        )
    return output
