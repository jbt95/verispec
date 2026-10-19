# VeriSpec spec format (v0.1)

This is the reference for **format version 0.1**, kept for specs that have not
been migrated yet. A v0.1 spec is a YAML file with no `spec_version` field.

The current reference is [`docs/spec-format.md`](spec-format.md). Everything
here still works: the CLI loads a v0.1 spec and checks it exactly as before.
What v0.1 cannot express is scalars only.

To move a spec to v1.0:

```console
$ verispec migrate spec.yaml --in-place
migrated spec.yaml from spec_version 0.1 to 1.0
```

`migrate` without `--in-place` prints the upgraded YAML to stdout. The upgrade
adds `spec_version: "1.0"`; it does not change the meaning of the spec.

## One file, one state machine

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

The file is the canonical, human-owned, agent-read-only artifact. Everything
else in VeriSpec is derived from it.

## Fields

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `name` | string | no | The name of the spec. Defaults to `Unnamed`. |
| `description` | string | no | Free text. Shown in the brief and the report. |
| `vars` | mapping | yes | The state variables. At least one is required. |
| `init` | mapping | no | Initial values, as expressions. |
| `actions` | list | no | The transitions. |
| `invariants` | list | no | Properties that must always hold. |

There is no `spec_version`, no `imports`, and no collection type.

## Types

Scalar state only:

| Type | Declaration | Default value |
| --- | --- | --- |
| `int` | `{type: int}` | `0` |
| `bool` | `{type: bool}` | `false` |
| `str` | `{type: str}` | `""` |
| `enum[...]` | `{type: "enum[pending,done]"}` | the first enum value |

`min` and `max` on a state variable are **documentation**, not solver axioms.
The checker does not add them to the model, so a violated bound cannot hide a
violated invariant. The wording in the original v0.1 reference suggested
otherwise; the v1 reference says so explicitly.

`init` entries are expressions, evaluated in the order they appear in the
file. A bare word that is not a known variable name is a string literal, so
`status: pending` means the string `pending`.

## Expression language

Allowed: comparisons (`== != < <= > >= in`), `and/or/not`, arithmetic
`+ - * // %`, parentheses, `if ... else`, list literals,
`len/all/any/min/max/abs` (all/any over list literals in Z3). Names are state
vars or action params.

Forbidden (clear error, never `eval`): attribute access, subscripts, imports,
arbitrary calls, comprehensions over unbounded domains, floats, `eval/exec`.
Implemented as an AST whitelist in `src/verispec/expr.py`.

`//` is floor division.

## Types and limits in v0.1

- Scalar `int` (bounded for BMC), `bool`, `str`, `enum[...]` only. No
  maps/lists of state yet. Model collections as bounded counters or multiple
  vars (see `examples/bank.yaml`). This matches the report's CIR v0:
  integers, booleans, bounded scalars first.
- BMC is bounded: "no violation up to bound K", never "proven for all depths".

## Adapter contract (any language)

`reset()`, `apply(action, **params)`, `snapshot() -> dict` matching spec vars
exactly.

Traces: JSON list of `{action, params, state}`, first entry
`action: "init"`, for `verispec check-trace`. This is the language-agnostic
Route B bridge.

## What v0.1 cannot do

| Missing in v0.1 | Added in v1.0 |
| --- | --- |
| Maps, sets, sequences of state | `map`, `set`, `seq` with a finite domain |
| Structured state | `record` with named scalar fields |
| Reading a collection | Subscripting, `map_get`, `set_has`, `seq_get`, ... |
| Reusing a spec across specs | `imports` with alias namespacing |
| Saying which format a file uses | `spec_version` |

See the [changes from v0.1](spec-format.md#changes-from-v0.1) section of the
v1 reference for the full comparison, and
[`rfcs/0001-bounded-collections.md`](../rfcs/0001-bounded-collections.md) for
the design that introduced collections.
