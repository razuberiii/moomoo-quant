import math

from . import config


def commission_usd(notional_usd: float) -> float:
    """Return commission for one filled order under the configured fee schedule."""
    if notional_usd <= 0:
        return 0.0
    return min(
        max(notional_usd * config.COMMISSION_RATE, config.COMMISSION_MIN_USD),
        config.COMMISSION_CAP_USD,
    )


def affordable_quantity(cash_usd: float, price_usd: float, fractional: bool = True) -> float:
    """Largest quantity whose notional plus commission fits in cash_usd."""
    if cash_usd <= config.COMMISSION_MIN_USD or price_usd <= 0:
        return 0.0

    low = 0.0
    high = cash_usd / price_usd
    for _ in range(60):
        mid = (low + high) / 2
        total = mid * price_usd + commission_usd(mid * price_usd)
        if total <= cash_usd:
            low = mid
        else:
            high = mid

    return low if fractional else float(math.floor(low))
