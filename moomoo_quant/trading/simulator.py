"""Legacy compatibility module; all trading-account access is disabled."""

from ..multi_strategy.execution import ExecutionDisabledError


def get_simulation_status():
    raise ExecutionDisabledError(
        "Trading-account access is disabled in the proposed-orders-only phase."
    )


def get_spy_sim_position():
    raise ExecutionDisabledError(
        "Trading-account access is disabled in the proposed-orders-only phase."
    )
