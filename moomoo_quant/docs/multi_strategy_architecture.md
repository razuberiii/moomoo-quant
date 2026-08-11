# Multi-Strategy Architecture

The runtime boundary is:

`Strategy -> Portfolio Manager -> Risk Manager -> Proposed Orders only`

Strategies emit versioned signals and target weights. They cannot read another
strategy's cash or positions. Portfolio Manager converts each target to JPY
notional and fractional quantity, preserves strategy attribution, then nets by
symbol. Risk Manager produces an append-only decision. The final layer can only
persist a `PROPOSED_ONLY` record and has no broker SDK dependency.

Benchmarks are stored and displayed as comparison series. They are never
strategy accounts and cannot have budgets, lifecycle events, positions or orders.
