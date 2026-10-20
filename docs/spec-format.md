# VeriSpec spec format (v1)

One YAML file is one state machine. That file is the canonical, human-owned,
agent-read-only artifact: everything else in VeriSpec (the Z3 checker, the TLA+
and Lean exports, the generated property tests, the trace checker, the agent
brief) is derived from it.

This document is the reference for **format version 1.0**. If your spec still
has no `spec_version` field, it is a v0.1 spec: see
[`docs/spec-format-v0.1.md`](spec-format-v0.1.md).

- [The model](#the-model)
- [A complete example](#a-complete-example)
- [Top-level fields](#top-level-fields)
- [`spec_version`](#spec_version)
- [`imports`](#imports)
- [`vars`](#vars)
- [`init`](#init)
- [`actions`](#actions)
- [`invariants`](#invariants)
- [The expression language](#the-expression-language)
- [Semantics and edge cases](#semantics-and-edge-cases)
- [How collections are encoded in Z3](#how-collections-are-encoded-in-z3)
- [Versioning and compatibility](#versioning-and-compatibility)
- [The JSON trace format](#the-json-trace-format)
- [The adapter contract](#the-adapter-contract)
- [The TLA+ and Lean exports](#the-tla-and-lean-exports)
- [The command line reference](#the-command-line-reference)
- [Error messages you will see](#error-messages-you-will-see)
- [Changes from v0.1](#changes-from-v0.1)

---

## The model

A spec declares:

| Part | Question it answers |
| --- | --- |
| `vars` | What state exists, and what may it contain? |
| `init` | What is the state before anything happens? |
| `actions` | What can happen, when is it allowed, and what does it change? |
| `invariants` | What must be true in every reachable state? |

The semantics is a transition system. A run is a sequence of states
`S0 -> S1 -> ... -> Sn`. `S0` is the state produced by `init`. Each step picks
an action whose `requires` hold in the current state, and applies its `effects`
to obtain the next state. Every state in the run, including `S0`, must satisfy
every invariant.

Actions that are not mentioned in an `effects` block keep their previous value
(the frame condition). The checker never assumes an action is enabled: an
action whose `requires` are false in the current state simply cannot be taken.

## A complete example

```yaml
spec_version: "1.0"
name: Bank
description: >
  Two accounts A and B.  Transfers conserve the total and no account may go
  negative.
imports:
  - path: ./lib/common.yaml
    as: common
    version: "^1.0"
vars:
  balance_a: {type: int, min: 0, max: 100}
  status:    {type: "enum[pending,done]"}
  balances:  {type: map, key: int, key_min: 0, key_max: 7, value: int, value_min: 0, value_max: 1000, max_size: 8}
  by_name:   {type: map, key: str, key_domain: [alice, bob], value: int, value_min: 0, value_max: 100}
  seen:      {type: set, of: str, elem_domain: [a, b, c], max_size: 3}
  history:   {type: seq, of: int, elem_min: 0, elem_max: 10, max_size: 4}
  customer:  {type: record, fields: {name: str, tier: "enum[gold,silver]", active: bool}}
init:
  balance_a: "50"
  status: pending
  balances: {0: 10, 1: 20}
  by_name: {alice: 5}
  seen: []
  history: []
  customer: {name: alice, tier: gold, active: true}
actions:
  - name: transfer
    params:
      amount: {type: int, min: 1, max: 50}
    requires: ["amount > 0", "balance_a >= amount"]
    effects:
      balance_a: "balance_a - amount"
      customer:
        active: "false"
invariants:
  - {name: total_conserved, expr: "balance_a + balance_b == 100"}
```

## Top-level fields

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `spec_version` | string | no | Semver of the format. Absent means v0.1. |
| `name` | string | no | The name of the spec. Defaults to `Unnamed`. |
| `description` | string | no | Free text. Shown in the brief and the report. |
| `imports` | list | no | Other specs to merge in, namespaced by alias. |
| `vars` | mapping | yes | The state variables. At least one is required. |
| `init` | mapping | no | Initial values. Missing variables get their type default. |
| `actions` | list | no | The transitions. Names must be unique. |
| `invariants` | list | no | Properties that must hold in every reachable state. |
| `fairness` | mapping | no | Weak and/or strong fairness assumptions on named actions (see [Temporal properties and fairness](#temporal-properties-and-fairness)). |
| `temporal` | list | no | Liveness properties (`eventually`, `leads_to`) checked by bounded lasso search and finite traces. |
| `refines` | mapping | no | On a **concrete** spec only: refinement map to an abstract spec (see [Refinement mapping](#refinement-mapping)). |

Unknown top-level keys are ignored, so a spec can carry comments-as-data
without breaking the loader.

## `spec_version`

```yaml
spec_version: "1.0"
```

`spec_version` is the semantic version of the *format* the file is written in,
not of the tool. It is optional: a spec without it is treated as **v0.1** and
still loads, with scalar-only state.

This CLI understands format majors `0` and `1`. A spec that declares an
unknown major is rejected:

```console
$ verispec validate spec-v2.yaml
error: spec format v2 is not supported by this CLI (supported majors: [0, 1]); run `verispec migrate`
```

Upgrade a v0.1 spec with:

```console
$ verispec migrate spec.yaml --in-place
migrated spec.yaml from spec_version 0.1 to 1.0
```

`migrate` without `--in-place` prints the upgraded YAML to stdout.

## `imports`

```yaml
imports:
  - path: ./lib/common.yaml
    as: common
    version: "^1.0"
```

| Key | Required | Meaning |
| --- | --- | --- |
| `path` | yes | Path to the imported spec, relative to the importing file. |
| `as` | no | Alias used as the namespace prefix. Defaults to the file stem. |
| `version` | no | Semver range the imported `spec_version` must satisfy. |

The imported spec's `vars`, `actions` and `invariants` are merged into the
importing spec. Every name is prefixed with the alias and an underscore:

| Imported name | Merged name |
| --- | --- |
| `counter` (var) | `common_counter` |
| `bump` (action) | `common_bump` |
| `counter_non_negative` (invariant) | `common_counter_non_negative` |

Expressions inside the imported spec are rewritten to the prefixed names, so an
imported spec keeps referring to its own variables.

**Cross-module references are not allowed.** An imported spec cannot name a
variable of the importing spec, and the importing spec cannot name a variable
of the imported spec before the merge (use the prefixed name instead). A
module is self-contained.

The `version` range is checked against the imported spec's `spec_version`. The
supported range syntax is a subset of semver: `1.0`, `^1.0`, `~1.0`, `>=1.0,<2.0`,
`1.x`. If the imported spec does not satisfy the range, loading fails:

```console
$ verispec validate main.yaml
error: import ./lib/common.yaml has spec_version 0.1, which does not satisfy '^1.0'
```

Use `verispec deps` to see what was resolved, and `verispec deps --lock
verispec.lock` to record the digests of the spec and its dependencies in a
lock file. `verispec deps --check-lock verispec.lock` fails when either changed.

## `vars`

A variable declaration is either a type string or a mapping with a `type` key.

### Scalars

| Type | Declaration | Notes |
| --- | --- | --- |
| `int` | `{type: int}` | Optional `min` / `max`. |
| `bool` | `{type: bool}` | |
| `str` | `{type: str}` | |
| `enum` | `{type: "enum[pending,done]"}` | Also `{type: enum, values: [pending, done]}`. |

```yaml
vars:
  balance_a: {type: int, min: 0, max: 100}
  flag:      {type: bool}
  status:    {type: "enum[pending,done]"}
```

`min` and `max` on a **state variable are documentation**, not solver axioms.
The checker does not add `0 <= balance_a <= 100` to the model, so a violated
bound cannot hide a violated invariant. They exist so that the brief, the
report and the generated tests can tell the implementer what the intended
range is. (Parameter bounds are different: see [`actions`](#actions).)

The default value of a scalar, used when a variable has no `init` entry, is
`0` for `int`, `false` for `bool`, `""` for `str`, and the first enum value for
an enum.

### Maps

```yaml
balances: {type: map, key: int, key_min: 0, key_max: 7, value: int, value_min: 0, value_max: 1000, max_size: 8}
```

| Key | Required | Meaning |
| --- | --- | --- |
| `key` | yes | Key type: `int`, `str` or `enum[...]`. `bool` is rejected. |
| `value` | yes | Value type: any scalar. |
| `key_min`, `key_max` | for `int` keys | The inclusive range of legal keys. |
| `key_domain` | for `str` keys | The explicit list of legal keys. |
| `value_min`, `value_max` | no | Documentation bounds on values. |
| `max_size` | no | Capacity. Defaults to the size of the key domain. |

### Sets

```yaml
seen: {type: set, of: str, elem_domain: [a, b, c], max_size: 3}
```

| Key | Required | Meaning |
| --- | --- | --- |
| `of` | yes | Element type: `int`, `str` or `enum[...]`. `bool` is rejected. |
| `elem_min`, `elem_max` | for `int` elements | The inclusive range of legal elements. |
| `elem_domain` | for `str` elements | The explicit list of legal elements. |
| `max_size` | no | Capacity. Defaults to the size of the element domain. |

### Sequences

```yaml
history: {type: seq, of: int, elem_min: 0, elem_max: 10, max_size: 4}
```

| Key | Required | Meaning |
| --- | --- | --- |
| `of` | yes | Element type: any scalar, including `bool`. |
| `elem_min`, `elem_max` | no | Documentation bounds on the elements. Must be declared together. |
| `max_size` | yes | Capacity. Must be at least 1. Defaults to 16. |

### Records

```yaml
customer: {type: record, fields: {name: str, tier: "enum[gold,silver]", active: bool}}
```

| Key | Required | Meaning |
| --- | --- | --- |
| `fields` | yes | Non-empty mapping of field name to scalar type. |

Field names must be valid identifiers. Record fields are scalar only in v1.

### The finite domain rule

**Every collection has a finite domain.** The domain is the set of keys (for
maps) or elements (for sets) that may exist:

| Element type | Domain |
| --- | --- |
| `int` | `[key_min .. key_max]` / `[elem_min .. elem_max]`, inclusive |
| `str` | the explicit `key_domain` / `elem_domain` list |
| `enum[...]` | the enum values |
| `bool` | rejected for map keys and set elements |

The domain is not decoration. It is what makes the Z3 encoding total: every
possible read is a concrete slot, so the solver never invents a key that no
implementation could produce, and therefore never reports a phantom
counterexample.

The domain size is capped at 256 values per collection. A wider range is an
error, not a warning:

```console
$ verispec validate wide.yaml
error: var m: domain of 1000 values exceeds the limit of 256; narrow the range
```

## `init`

`init` maps a variable name to its initial value. The form of the value
depends on the type.

**Scalars take an expression.** Write it as a string, quoted, so YAML does not
eat it:

```yaml
init:
  balance_a: "50"
  flag: false
  status: pending
```

A bare word that is not a known variable name is a string literal, so
`status: pending` means the string `pending`. A bare word that *is* a known
variable name is a reference to that variable, which is how you initialise one
variable from another. `init` entries are evaluated in the order they appear
in the file, and each one may only refer to variables initialised before it.

**Collections take concrete data.**

```yaml
init:
  balances: {0: 10, 1: 20}
  by_name: {alice: 5}
  seen: []
  history: []
```

A key outside the declared domain is an error, not a silent drop:

```console
$ verispec validate ood.yaml
error: init.m: key 9 is outside the declared domain
```

**Records take a mapping of field to value**, with every field present and no
extra field:

```yaml
init:
  customer: {name: alice, tier: gold, active: true}
```

```console
$ verispec validate rec.yaml
error: init.customer: missing field(s) ['tier']
```

A record field whose value is a bare word matching a known variable name is
treated as an expression; everything else is a literal.

A variable with no `init` entry starts at its type default.

## `actions`

```yaml
actions:
  - name: transfer
    params:
      amount: {type: int, min: 1, max: 50}
    requires: ["amount > 0", "balance_a >= amount"]
    effects:
      balance_a: "balance_a - amount"
      customer:
        active: "false"
```

| Field | Required | Meaning |
| --- | --- | --- |
| `name` | yes | Unique identifier of the action. |
| `params` | no | Parameters of the action. Scalars only in v1. |
| `requires` | no | Preconditions, as a list of boolean expressions. |
| `effects` | no | Assignments, as a mapping of variable to expression. |

### `params`

Parameters are scalars only in v1 (`int`, `bool`, `str`, `enum[...]`).

**Parameter bounds are enforced by the solver.** The checker constrains every
value of an integer parameter to `min <= p <= max`, and every enum parameter to
one of the enum values. This is different from the `min` / `max` of a state
variable, which are documentation only. An unbounded integer parameter is
allowed but discouraged; `verispec lint` reports it as `unbounded-param`,
because the generated tests then have to invent a range.

### `requires`

`requires` is a list of boolean expressions over the current state and the
action's parameters. The action is enabled in a state only if **all** of them
hold. An empty (or missing) `requires` means the action is enabled
everywhere; `verispec lint` reports that as `no-precondition`.

```yaml
requires:
  - "amount > 0"
  - "balance_a >= amount"
  - "reference not in seen"
```

### `effects`

`effects` maps a state variable to the expression that computes its new value.
The expression is evaluated in the *current* state, with the parameters
available, so `balance_a - amount` means "the current balance minus the
parameter".

Variables that are not listed keep their value.

**Records are updated field by field.** The effect of a record variable is a
mapping of field name to expression, and only the named fields change:

```yaml
effects:
  customer:
    active: "false"
```

`customer["tier"]` keeps its old value, and every other variable is unchanged.

Assigning to a variable that does not exist, or to an unknown field of a
record, is an error:

```console
$ verispec validate bad.yaml
error: action transfer effect assigns unknown var 'balnace'
error: action transfer: unknown field 'tier' of record var 'customer'; known fields: ['active', 'name', 'tier']
```

## `invariants`

```yaml
invariants:
  - {name: total_conserved, expr: "balance_a + balance_b == 100"}
  - {name: non_negative, expr: "balance_a >= 0 and balance_b >= 0"}
```

An invariant is a `{name, expr}` mapping. The expression must be boolean.
Names must be unique. A bare string is also accepted and is used as both the
name and the expression.

Invariants are checked in every state of every explored run, including the
initial state. The bounded model checker also enforces collection capacity
bounds (see below). Optional **temporal** properties are checked separately as
bounded liveness (see [Temporal properties and fairness](#temporal-properties-and-fairness)).

`verispec lint` reports specs whose invariants can never fail, because a
tautology is worse than no invariant: it looks like evidence and is not.

## Temporal properties and fairness

Safety invariants must hold in **every** state. **Temporal** properties describe
progress: something good must **eventually** happen, or a condition must
**lead to** a goal. They use the same expression language as invariants.

```yaml
fairness:
  weak: [complete]
  strong: []

temporal:
  - name: eventually_done
    kind: eventually
    goal: 'phase == "done"'
  - name: pending_implies_done
    kind: leads_to
    when: 'phase == "pending"'
    goal: 'phase == "done"'
```

| `kind` | Meaning |
| --- | --- |
| `eventually` | Along every fair infinite run, `goal` holds at some step. |
| `leads_to` | Whenever `when` holds, `goal` holds eventually after (always (`when` → eventually `goal`)). |

`fairness.weak` lists actions with **weak fairness** (`WF`): on a lasso
counterexample cycle, if an action stays enabled on every transition in the
cycle, it must be taken at least once in that cycle (bounded approximation).
`fairness.strong` uses a similar **strong fairness** (`SF`) rule: if the action
is enabled on any transition in the cycle, it must be taken in the cycle.

### Checking

- `verispec check` runs safety BMC, then **bounded liveness** (lasso-shaped
  counterexamples) unless `--skip-liveness` is set. Use `--temporal <name>` to
  focus on one property. Guarantees: `bounded-liveness-proved(k)` alongside
  `bounded-proved(k)` for safety.
- `verispec check-trace` reports **pending** obligations when a finite trace
  conforms to actions and invariants but has not yet satisfied a temporal
  property at the last step (`guarantee: pending-obligation`).
- `verispec export-tla` adds temporal formulas, fairness definitions, and
  extends `Spec` with fairness conjuncts.

Specs without `fairness` or `temporal` are unchanged. See
[RFC-0002](../rfcs/0002-temporal-liveness.md).

## Refinement mapping

A concrete (implementation-level) spec may declare that it **refines** an
abstract (business-level) spec. The `refines` block lives on the concrete file
only; the abstract spec is unchanged.

```yaml
refines:
  spec: bank.yaml
  state:
    balance_a: "balance_a + pending_a"
    balance_b: "balance_b + pending_b"
  actions:
    initiate_transfer:
      stutter: true
    complete_transfer:
      abstract: transfer_a_to_b
      params:
        amount: amount
```

| Field | Meaning |
| --- | --- |
| `spec` | Path to the abstract YAML (relative to the concrete file). |
| `state` | Abstract variable name → expression over **concrete** state variables. |
| `actions.<concrete>` | Either `{stutter: true}` or `{abstract: <name>, params?: {...}}`. |

### Checking

- `verispec refines <concrete.yaml> [abstract.yaml]` runs bounded Z3 refinement
  checking. When `abstract.yaml` is omitted, `refines.spec` is used.
- Counterexamples include **concrete** and **abstract** traces. Guarantee:
  `bounded-refinement-proved(k)` when no violation is found within depth *k*.
- `verispec export-tla` prepends a comment documenting the map when `refines:` is
  present.

Scalar state abstraction only (maps/sets/records in `refines.state` are not
supported yet). Worked example: `examples/bank-concrete-pending.yaml` refines
`examples/bank.yaml`. See [RFC-0004](../rfcs/0004-refinement-mapping.md).

## The expression language

The same AST is used by the concrete interpreter, the Z3 translation, the TLA+
export, the Lean export and the code generators. There is no `eval` anywhere:
the language is an explicit whitelist, and anything outside it is rejected with
an error.

### Operators

| Category | Operators |
| --- | --- |
| Comparison | `==`, `!=`, `<`, `<=`, `>`, `>=` |
| Membership | `in`, `not in` |
| Boolean | `and`, `or`, `not` |
| Arithmetic | `+`, `-`, `*`, `//`, `%` |
| Conditional | `if <cond> then <a> else <b>` written as `a if cond else b` |
| Grouping | `( ... )` |

`//` is floor division. `%` is the remainder. Division by zero is an error at
evaluation time.

### Functions

| Function | Arity | Meaning |
| --- | --- | --- |
| `len(x)` | 1 | Length of a map, set, sequence, string or list. |
| `abs(x)` | 1 | Absolute value of an int. |
| `min(a, b, ...)` | 2+ | Smallest int. |
| `max(a, b, ...)` | 2+ | Largest int. |
| `all(<expr> for <x> in <list>)` | 1 | True when the predicate holds for every element. |
| `any(<expr> for <x> in <list>)` | 1 | True when the predicate holds for some element. |
| `map_get(m, k)` | 2 | Value of `m[k]`, or the value type default when the key is absent. |
| `map_set(m, k, v)` | 3 | A copy of `m` with `k` set to `v`. Keys outside the domain are ignored. |
| `set_add(s, v)` | 2 | A copy of `s` with `v` added. Elements outside the domain are ignored. |
| `set_has(s, v)` | 2 | Whether `v` is in `s`. |
| `seq_get(q, i)` | 2 | Element at index `i`, or the element default when the index is out of range. |
| `seq_append(q, v)` | 2 | A copy of `q` with `v` appended. |
| `keys(m)` | 1 | The keys of a map, as a list. |
| `values(m)` | 1 | The values of a map, as a list. |

### Subscript

`c[k]` reads a map (by key), a sequence (by index) or a record (by field name).

```text
balances[0]          # map read
history[0]           # sequence read
customer["active"]   # record read
```

Records are read with a **string constant** field name. `customer[active]`
does not work, and neither does `customer.active`: attribute access stays
forbidden everywhere.

### Membership

```text
"reference not in seen"    # set membership
"0 in balances"            # map key membership
"3 in history"             # list literal membership
```

`in` on the right of a map tests for the *key*, not the value.

### `keys()` and `values()` are not checkable

`keys(m)` and `values(m)` have no finite symbolic domain: a Z3 model does not
enumerate the keys of a map. They work in the concrete interpreter and in the
generated code, but they are **not supported by the Z3 translation**, and the
checker says so instead of silently guessing:

```console
$ verispec check keys.yaml
error: keys()/values() are not supported in the Z3 translation (they have no finite symbolic domain)
```

The same applies to `all(...)` / `any(...)` over `keys()` or `values()`.

### Rejected syntax

| Rejected | Why |
| --- | --- |
| Attribute access (`x.real`) | Ambiguous with method calls; records use `x["field"]`. |
| Imports, arbitrary calls | The whitelist is closed: only the functions listed above. |
| Floats | No float semantics in the checker; the Z3 encoding is integral. |
| `eval` / `exec` | Never used, at all. |
| Comprehensions over collections | Only `all` / `any` over list literals. |
| Slices, lambdas, walrus, f-strings | Outside the whitelist. |

```console
$ verispec validate attr.yaml
error: action a effect x: forbidden syntax Attribute in expression 'x.real': only comparisons, and/or/not, + - * // %, in, subscript, len/all/any/min/max/abs and ['keys', 'map_get', 'map_set', 'seq_append', 'seq_get', 'set_add', 'set_has', 'values'] are allowed
```

## Semantics and edge cases

### Missing map keys read as the value type default

Reading a key that is not present returns the default of the *value* type,
never an error:

| Value type | Default |
| --- | --- |
| `int` | `0` |
| `bool` | `false` |
| `str` | `""` |
| `enum[...]` | the first enum value |

This is the same in the interpreter and in Z3, so the two semantics never
disagree. The default is a documentation convenience, not a constraint: the
invariant layer is what decides whether a missing key is a bug.

### Out-of-domain keys are ignored

`map_set(m, k, v)` with a `k` outside the declared key domain returns a copy
of `m` unchanged. `set_add(s, v)` with an out-of-domain `v` behaves the same.
The Z3 encoding does exactly the same thing (`If(inside, Store(...), coll)`),
so the interpreter and the checker agree by construction.

### Growing past `max_size` is a violation, not a truncation

A collection that exceeds its capacity is reported as a violation, in the same
way an invariant violation is:

```console
$ verispec check cap.yaml --bound 4
COUNTEREXAMPLE found within bound 3: invariant(s) violated: i, seen_capacity
  ...
  violated at step 3: seen_capacity (bound) len(seen) <= 2
```

The capacity violation is named `<variable>_capacity` and has the kind `bound`.
The collection is never silently truncated: the point of the bound is to make
the overflow visible.

### Stutter steps

The checker may take a step in which no action is applied; the state is then
unchanged. Such a step appears in a counterexample trace as
`-> action (stutter)`. It exists so that a run of length *k* is a prefix of a
longer run, and it never changes what is reachable.

### The initial state is checked too

Invariants are evaluated in the state produced by `init`. An invariant that is
false in the initial state is reported at step 0, before any action runs.

### Enum values are strings

An enum value *is* a string in every backend. `status == "pending"` and
comparing an enum variable with a string literal are both accepted, and they
mean the same thing in the interpreter, in Z3 and in the generated code.

### Records are partial on write, total on read

A record effect only touches the fields it names. A record read always
succeeds: `customer["tier"]` returns the current value of that field.

## How collections are encoded in Z3

The checker builds one Z3 state per step, per variable. A scalar is a single
constant (`Int`, `Bool` or `String`).

A **map** becomes three constants:

| Constant | Sort | Meaning |
| --- | --- | --- |
| `<name>_v_<step>` | `Array(Int, value_sort)` | The value of every domain slot. |
| `<name>_p_<step>` | `Array(Int, Bool)` | Whether the slot is present. |
| `<name>_n_<step>` | `Int` | The number of present slots. |

A **set** becomes two constants: `<name>_p_<step>` (`Array(Int, Bool)`) and
`<name>_n_<step>` (`Int`).

A **sequence** becomes `<name>_i_<step>` (`Array(Int, elem_sort)`) and
`<name>_n_<step>` (`Int`), where the first `n` slots are the elements.

A **record** becomes one constant per field, with the field's own sort.

The domain is the index space. The domain slot of the key `k` is
`domain_index(k)`: for an integer domain it is `k - key_min`, and for a string
or enum domain it is the position of the key in the declared list. The
translation of a map read is therefore:

```
If(and(inside_domain(k), present[idx(k)]), values[idx(k)], default(value_type))
```

Three consequences follow, and they are the reason for the design:

1. **Every read is total.** There is no unconstrained read, so the solver
   cannot pick a key that does not exist and manufacture a violation.
2. **The interpreter and the solver agree.** `map_get`, `map_set`, `set_add`
   and `seq_get` have one implementation per backend and the same semantics.
3. **Capacity is checkable.** The size counter is part of the state, so
   `size > max_size` is a constraint the solver can violate, and it is checked
   at every step like an invariant.

`map_set(m, k, v)` becomes:

```
values'  = If(inside(k), Store(values, idx(k), v), values)
present' = If(inside(k), Store(present, idx(k), true), present)
size'    = If(inside(k), If(present[idx(k)], size, size + 1), size)
```

`set_add` is the same without the value array. `seq_append(q, v)` stores `v`
at index `size` and increments the size.

Enums are encoded as Z3 strings constrained to the enum's value set, which
keeps the solver semantics identical to the interpreter's.

## Versioning and compatibility

The format has its own version, independent of the tool version.

| Change | Version bump |
| --- | --- |
| A new construct is added, existing specs keep working | MINOR (`1.0` -> `1.1`) |
| A construct is removed, renamed or changes meaning | MAJOR (`1.x` -> `2.0`) |
| A fix that does not change the meaning of any spec | PATCH |

Policy:

- The CLI keeps accepting at least the previous MAJOR version of the format.
  This CLI accepts v0.1 and v1.x.
- A spec without `spec_version` is a v0.1 spec and is loaded as such.
- Deprecations are announced for a full MINOR release before removal, and
  `verispec migrate` upgrades a spec to the current format.
- Changes to the format follow the RFC process in [`rfcs/README.md`](../rfcs/README.md).

## The JSON trace format

A trace is how a real implementation reports what it did. It is a JSON list of
steps, and the first step is the initial state:

```json
[
  {"action": "init", "params": {}, "state": {"balance_a": 50, "balance_b": 50}},
  {"action": "transfer_a_to_b", "params": {"amount": 20}, "state": {"balance_a": 30, "balance_b": 70}}
]
```

| Field | Meaning |
| --- | --- |
| `action` | The action name, or `"init"` for the first step. |
| `params` | The parameters passed to the action, as a JSON object. |
| `state` | The full state after the step, as a JSON object. |

The `state` object must contain **exactly** the variables of the spec, with
the same names. A missing or unknown variable is an error.

The JSON representation of each type is:

| Spec type | JSON |
| --- | --- |
| `int` | number |
| `bool` | `true` / `false` |
| `str` | string |
| `enum[...]` | string (one of the enum values) |
| `map` | object, keys **stringified** (`"0"` for the integer key `0`) |
| `set` | array of elements |
| `seq` | array of elements, in order |
| `record` | object keyed by field name |

`verispec check-trace` replays the trace against the spec and checks, step by
step: the state is well formed, the collection capacities hold, the invariants
hold, the action exists, the parameters match the declaration, the
preconditions hold in the previous state, and the effects produce exactly the
recorded state. The first mismatch is the failure.

## The adapter contract

An adapter connects a real implementation to a spec. It is the only
language-specific piece, and it is deliberately tiny:

| Method | Meaning |
| --- | --- |
| `reset()` | Put the implementation in the initial state of the spec. |
| `apply(action, **params)` | Apply one action. |
| `snapshot() -> dict` | The current state, with exactly the spec's variables. |

The names of the actions and the parameter names must match the spec exactly.
`apply` may raise when a precondition does not hold; the generated tests only
call actions whose preconditions hold.

See [`docs/adapter-authoring.md`](adapter-authoring.md) for the full guide and
[`docs/language-integrations.md`](language-integrations.md) for the per-language
recipes.

## The TLA+ and Lean exports

Two exports turn the spec into a form other tools can read. Neither changes
what the spec means; both are derived from it.

### TLA+

```console
$ verispec export-tla specs/bank.yaml -o Bank.tla
wrote TLA+ module to Bank.tla
```

The module has:

- `Init`, the initial state,
- one action per spec action, with the preconditions as guards and the
  effects as the primed assignments,
- `Next`, the disjunction over the actions,
- `Spec == Init /\ [][Next]_vars`,
- one definition per invariant, and `Invariants`, their conjunction.

Collections become TLA+ constructs: a map becomes a function
`[D -> V]`, a set becomes `SUBSET D`, a sequence becomes `Seq(S)`. A record
becomes a TLA+ record.

Run it with TLC:

```console
$ verispec check-tlc specs/bank.yaml
```

`check-tlc` writes the module and runs it through TLC when TLC is installed;
when it is not, the command says so instead of failing silently. The export
is what a reviewer reads to see the design in a notation they already know.

### Lean

```console
$ verispec export-lean specs/bank.yaml -o Bank.lean
wrote Lean 4 skeleton to Bank.lean
  Note: the theorems are `sorry`; this is a skeleton, not a proof.
```

The Lean file declares a `State` structure, the `init` value, one function
per action, and **one theorem per invariant**:

```lean
theorem total_conserved (s : State) :
    ((s.balance_a + s.balance_b) = 100) := by
  sorry
```

Every theorem is discharged with `sorry`. The export **states** the
invariants as theorems; it does **not** prove them. A file full of `sorry`
is a skeleton and a to-do list, and the CLI says so when it writes it.

Turning the skeleton into a proof is a separate, substantial job: the
invariants have to be discharged by hand, or by turning the Z3 model into a
Lean proof term.

## The command line reference

The whole toolchain, in one table. Every command is documented in more
detail elsewhere; this is the map.

| Command | What it does |
| --- | --- |
| `verispec validate <spec> [--format text\|json\|sarif]` | Parse and type check a spec. |
| `verispec lint <spec>` | Check the spec itself: vacuity, trivial invariants, never-enabled actions. |
| `verispec check <spec> [--bound N] [--invariant NAME] [--timeout MS] [--format text\|json\|sarif]` | Bounded model check the spec with Z3. |
| `verispec check-trace <spec> <trace.json> [--format text\|json\|sarif]` | Check that a real implementation follows the spec. |
| `verispec gen-tests <spec> --lang python\|typescript\|javascript\|go\|rust\|c\|java -o FILE [--adapter X] [--package NAME]` | Generate property tests for an adapter. |
| `verispec export-tla <spec> -o FILE` | Export the spec as a TLA+ module. |
| `verispec export-lean <spec> -o FILE` | Export the spec as a Lean 4 skeleton. |
| `verispec brief <spec> [-o FILE]` | Generate the agent brief. |
| `verispec report <spec> [-o FILE.html] [--trace T] [--bound N] [--format html\|json]` | Write the conformance report. |
| `verispec view <spec> [-o FILE.html] [--bound N]` | Write the counterexample viewer. |
| `verispec init [DIR] --template ledger\|idempotency\|order\|rate-limiter\|queue --lang python\|typescript\|go [--name NAME] [--force]` | Scaffold a spec, an adapter and a README from a template. |
| `verispec sign <spec> --approver NAME [--note TEXT] [--gpg] [--gpg-key KEY] [-o FILE]` | Sign a spec: the digest, the approver, optionally a GPG signature. |
| `verispec verify-signature <spec> [--signature FILE]` | Verify that a spec still matches its signature. |
| `verispec migrate <spec> [--in-place]` | Migrate a spec to the current format version. |
| `verispec deps <spec> [--lock FILE] [--check-lock FILE]` | Show the resolved imports, or lock and check them. |
| `verispec registry [QUERY] [--name NAME] [--registry DIR]` | Browse the open registry of reusable specs. |
| `verispec diff-trace <a.json> <b.json>` | Compare two traces. |
| `verispec monitor <trace.json> --spec <spec> [--follow] [--interval SECONDS]` | Check a live trace while the system runs. |
| `verispec corpus [DIR] [--bound N]` | Run the conformance corpus. |
| `verispec mcp` | Run the MCP server on stdio. |
| `verispec check-tlc <spec> [-o FILE] [--timeout SECONDS]` | Run the TLA+ export through TLC, when TLC is installed. |
| `verispec gen-harness <spec> --target kani\|esbmc [-o FILE]` | Generate a harness for an external checker. |
| `verispec version` | Print the version. |

Exit codes:

| Code | Meaning |
| --- | --- |
| `0` | OK. The check passed. |
| `1` | Violation. A counterexample was found, or a trace did not conform. |
| `2` | Spec error. The spec (or the trace) is invalid. |
| `3` | Tooling error. Something in VeriSpec went wrong. |

`--format json` prints a machine-readable result. `--format sarif` prints
SARIF 2.1.0 for code scanning.

## Error messages you will see

All of these are exit code 2 (the spec, or the trace, is invalid).

| Message | Cause |
| --- | --- |
| `spec has no vars` | The `vars` block is empty or missing. |
| `spec format v2 is not supported by this CLI (supported majors: [0, 1]); run 'verispec migrate'` | The declared major is unknown. |
| `init assigns unknown var 'x'` | `init` names a variable that does not exist. |
| `action transfer effect assigns unknown var 'x'` | `effects` names a variable that does not exist. |
| `action transfer: unknown field 'x' of record var 'c'; known fields: [...]` | A record effect names an unknown field. |
| `action transfer effect a: expected int[0..10], expression has type bool` | The effect expression has the wrong type. |
| `invariant i must be a boolean expression, got int` | The invariant is not a predicate. |
| `duplicate action 'transfer'` | Two actions share a name. |
| `duplicate invariant 'total'` | Two invariants share a name. |
| `action transfer: parameter amount must be a scalar type in v1` | A parameter is a collection. |
| `var m: str elements require an explicit elem_domain list (collections must be bounded)` | A collection has no finite domain. |
| `var m: domain of 1001 values exceeds the limit of 256; narrow the range` | The domain is too wide. |
| `var m: max_size must be a non-negative integer` | Bad capacity. |
| `var x: unsupported type 'frobnicate' (supported: int, bool, str, enum[...], map, set, seq, record)` | Unknown type. |
| `var x: min > max` | The declared range is inverted. |
| `init.m: key 9 is outside the declared domain` | An initial key is not in the domain. |
| `init.m: sequence of 5 exceeds max_size 4` | The initial sequence is too long. |
| `init.customer: missing field(s) ['tier']` | A record initialiser is incomplete. |
| `action a effect a: forbidden syntax Attribute in expression 'x.real': ...` | Attribute access. |
| `action a effect a: forbidden constant 1.5 in '1.5'` | A float literal. |
| `action a effect a: unknown variable 'y'` | An unknown name. |
| `action a effect m: map_get() key type mismatch: expected int, got str` | A type error inside a collection function. |
| `import ./lib/common.yaml has spec_version 0.1, which does not satisfy '^1.0'` | The version range is not satisfied. |
| `import not found: ./lib/common.yaml (resolved to /abs/path)` | The imported file is missing. |
| `keys()/values() are not supported in the Z3 translation (they have no finite symbolic domain)` | The expression cannot be checked. |

## Changes from v0.1

v0.1 specs keep working unchanged. `verispec validate`, `verispec check` and
the whole toolchain accept them as they are.

| Area | v0.1 | v1.0 |
| --- | --- | --- |
| `spec_version` | Not present. | Optional; absent means v0.1. |
| State types | `int`, `bool`, `str`, `enum[...]` only. | Adds `map`, `set`, `seq`, `record`. |
| Subscripting | Forbidden. | Allowed for maps, sequences and records. |
| Collection functions | None. | `map_get`, `map_set`, `set_add`, `set_has`, `seq_get`, `seq_append`, `keys`, `values`. |
| `key in map` / `v in set` | Not available. | Available. |
| Records | Not available. | Available; read with `x["field"]`, written field by field. |
| Modules | Not available. | `imports` with alias namespacing and semver ranges. |
| `min` / `max` on a state variable | Documented as solver bounds. | Explicitly documentation only. |
| Guarantee labels | `bounded-proved(k)`, `conformance-tested`, `signature-verified`, `violation-found`, `unknown`. | Unchanged. |

Two changes are worth calling out because they change what a green result
means:

1. **State variable bounds are no longer axioms.** In v0.1 the wording
   suggested that `min` / `max` constrained the model. They never did, and the
   v1 wording says so plainly. A violated bound can no longer hide a violated
   invariant, and the linter reports an unbounded variable as `unbounded-var`.
2. **Collections carry a finite domain.** This is what makes the collection
   encoding total, and therefore what keeps the checker free of phantom
   counterexamples.

The full v0.1 reference is in [`docs/spec-format-v0.1.md`](spec-format-v0.1.md).
