# VeriSpec spec format (v0.1)

One YAML file = one state machine. This is the canonical, human-owned, agent-read-only artifact.

```yaml
name: Bank
description: ...
vars:                       # scalar state only in v0.1
  balance_a: {type: int, min: 0, max: 100}   # int (bounded: min/max used by Z3 BMC)
  flag:      {type: bool}
  status:    {type: "enum[pending,done]"}
init:                       # var: expression (evaluated in order)
  balance_a: "50"
actions:
  - name: transfer_a_to_b
    params:
      amount: {type: int, min: 1, max: 50}
    requires: ["amount > 0", "balance_a >= amount"]   # preconditions, unprimed vars + params
    effects:                                          # var: expression over current state + params
      balance_a: "balance_a - amount"
invariants:
  - {name: total_conserved, expr: "balance_a + balance_b == 100"}
```

## Expression language (same AST -> Z3, Python, TypeScript)
Allowed: comparisons (`== != < <= > >= in`), `and/or/not`, arithmetic `+ - * // %`,
parentheses, `if ... else`, list literals, `len/all/any/min/max/abs` (all/any over
list literals in Z3). Names are state vars or action params.
Forbidden (clear error, never `eval`): attribute access, subscripts, imports,
arbitrary calls, comprehensions over unbounded domains, floats, `eval/exec`.
Implemented as an AST whitelist in `src/verispec/expr.py`.

## Types & limits (honest v0.1 scope)
- Scalar `int` (bounded for BMC), `bool`, `str`, `enum[...]` only. No maps/lists of
  state yet — model collections as bounded counters or multiple vars (see bank.yaml).
  This matches the report's CIR v0: integers, booleans, bounded scalars first.
- BMC is bounded: "no violation up to bound K", never "proven for all depths".

## Adapter contract (any language)
`reset()`, `apply(action, **params)`, `snapshot() -> dict` matching spec vars exactly.
Traces: JSON list of `{action, params, state}`, first entry `action: "init"`, for
`verispec check-trace` — the language-agnostic Route B bridge.
