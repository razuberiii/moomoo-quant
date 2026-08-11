# Proposed Orders Safety Boundary

The current Execution Layer has no import of a securities trading context. It may
persist an idempotent `ProposedOrder` with theoretical fractional quantity,
whole-share estimate and estimated commission/slippage/FX cost.

It cannot send that record. `ProposedOrderService.execute()` always raises
`ExecutionDisabledError`. The legacy simulator compatibility module also always
raises. Static AST tests reject broker order/unlock symbols, a real-environment
attribute and securities trade-context imports in executable Python source.
