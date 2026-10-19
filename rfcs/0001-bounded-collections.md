# RFC-0001: Bounded collections, records and modules

- Status: IMPLEMENTED
- Date: 2026-06-15
- Author: VeriSpec maintainers
- Related: ADR-0002 (spec format v1), ADR-0004 (Route B), ADR-0006 (guarantee labelling), `docs/spec-format.md`, `docs/spec-format-v0.1.md`

## Summary

The spec format gains bounded collections (`map`, `set`, `seq`), records
(`record`), and a module system (`imports`). Every collection carries an
explicit **finite domain**, which is what makes the Z3 encoding total: every
possible read is a concrete slot, so the checker can never invent a key that
no implementation could produce. The format version goes from 0.1 to 1.0.

## Motivation

### The problem with the scalar-only format

A v0.1 spec can only hold `int`, `bool`, `str` and `enum[...]` values. A
state machine about a ledger, a cache, a queue or a session store needs
collections, and a v0.1 spec has to model them with counters:

```yaml
# v0.1: "a map from account id to balance", written without maps
vars:
  balance_0: {type: int, min: 0, max: 1000}
  balance_1: {type: int, min: 0, max: 1000}
  balance_2: {type: int, min: 0, max: 1000}
  balance_3: {type: int, min: 0, max: 1000}
  count: {type: int, min: 0, max: 4}
```

This is not a style preference. It is a loss of expressive power that shows
up in three concrete ways.

**The invariants become about the encoding, not about the domain.** The
interesting property of a ledger is "the sum of the balances is conserved".
With a counter encoding, the invariant is
`balance_0 + balance_1 + balance_2 + balance_3 == total`, which is a
different statement, and it stops being the right statement the moment
 somebody adds a fifth account. Every account added is a change to the
invariant, to the effects, and to the adapter.

**The adapter has to invent the structure.** A v0.1 adapter for the spec
above reports four separate integers. The real implementation has a map. The
adapter therefore has to flatten a map into four fields and reassemble it,
and the flattening is not part of the contract, so the contract does not
describe what the implementation actually does.

**Quantified properties cannot be written.** "Every account's balance is
non-negative" is one invariant over a collection. With counters, it is *n*
invariants, and the author has to remember to write all of them. An invariant
that is easy to forget is an invariant that is not checked.

### The problem with unbounded collections

The obvious fix is to add `map`, `set` and `seq` without a domain. That does
not work, and the reason is worth stating precisely, because it is the
constraint that shapes the whole design.

Z3 has no map or set sort that can be constrained to "the keys an
implementation can actually produce". A map is encoded as an array plus a
presence array, and the solver is free to choose any index as present:

```python
# what an unconstrained encoding looks like
values  = Array(Int, Int)     # the solver picks ANY index
present = Array(Int, Bool)    # including index 999999999
```

The consequence is a class of counterexamples that are **not defects**: the
solver reports that `balances[7]` is `-5`, at a key that the implementation
can never produce, because nothing in the model says that key does not exist.
Every such report costs an engineer an hour of "but that is impossible",
and the first engineer who believes one of them ships a bug.

The alternative is to model the collection as a set of key-value pairs with
an unconstrained cardinality, which is worse: the encoding becomes
second-order, the solver times out, and the counterexamples are just as
spurious.

So the domain cannot be optional. If the domain is declared, the encoding is
total, and every counterexample corresponds to a real execution. If it is not
declared, the format cannot express the state at all.

**The domain is therefore part of the contract, and the author writes it
down.** A ledger with four accounts says it has four accounts. That is
information the spec should carry, not information the checker should guess.

### Why records

The same argument applies to structured state. A customer is not three
variables; it is one value with three fields, and the spec should say so:

```yaml
# v0.1
vars:
  customer_name: {type: str}
  customer_tier: {type: "enum[gold,silver]"}
  customer_active: {type: bool}
```

With separate variables, "the customer is active" and "the order is active"
are different variables that happen to share a type, and nothing stops the
spec from updating one of them by accident. A record makes the grouping
explicit, and the partial-update rule makes it impossible to change a field
you did not mean to change.

### Why modules

A real spec set has shared vocabulary. Every service in a company has an
`idempotency` spec, a `ledger` spec, a `rate_limit` spec. Without modules,
either every spec redefines them (and they drift) or a spec has to be
duplicated. The requirement is modest: a spec can import another one, and
the imported names are namespaced so they cannot collide.

What is deliberately **not** in scope: cross-module references. An imported
spec must be self-contained, because a module that can see its importer's
variables is a module whose meaning depends on the importer. That turns a
library into a program.

## Detailed design

### Grammar

The format is YAML. The additions are in four places: the `vars` block, the
`init` block, the `effects` block of an action, and the expression language.

```yaml
spec_version: "1.0"           # new: optional, absent means v0.1
name: Bank
description: ...
imports:                      # new: optional module list
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
  balances: {0: 10, 1: 20}     # new: collections take concrete data
  customer: {name: alice, tier: gold, active: true}
actions:
  - name: transfer
    params:
      amount: {type: int, min: 1, max: 50}
    requires: ["amount > 0", "balance_a >= amount"]
    effects:
      balance_a: "balance_a - amount"
      customer:                  # new: records update field by field
        active: "false"
invariants:
  - {name: total_conserved, expr: "balance_a + balance_b == 100"}
```

#### Types

| Kind | Declaration | Domain rule |
| --- | --- | --- |
| `int` | `{type: int, min: 0, max: 100}` | `min`/`max` are documentation, not axioms. |
| `bool` | `{type: bool}` | |
| `str` | `{type: str}` | |
| `enum[...]` | `{type: "enum[a,b]"}` | The values are the domain. |
| `map` | `{type: map, key: T, value: T, ...}` | `key_min`/`key_max` for `int` keys, `key_domain` for `str` keys; enum keys use the enum values. |
| `set` | `{type: set, of: T, ...}` | `elem_min`/`elem_max` for `int`, `elem_domain` for `str`; enum elements use the enum values. |
| `seq` | `{type: seq, of: T, max_size: N}` | The capacity is the bound. |
| `record` | `{type: record, fields: {name: T, ...}}` | Field names are identifiers; fields are scalars. |

`bool` is rejected as a map key and as a set element in v1: the domain would
be two values that behave like integers in some backends and like booleans
in others, and the ambiguity is not worth the expressiveness.

The domain of a collection is capped at 256 values. A wider domain is an
error. The cap bounds the encoding size and keeps the checker fast; a
collection that genuinely needs more than 256 keys is a sign that the spec is
modelling the wrong thing.

#### The expression language

The v0.1 language is a subset of the v1 language. The additions:

| Construct | Meaning |
| --- | --- |
| `c[k]` | Read a map (by key), a sequence (by index) or a record (by field name). |
| `map_get(m, k)` | The same as `m[k]`, spelled as a function. |
| `map_set(m, k, v)` | A copy of `m` with `k` set to `v`. |
| `set_add(s, v)` | A copy of `s` with `v` added. |
| `set_has(s, v)` | Whether `v` is in `s`. |
| `seq_get(q, i)` | The element at index `i`. |
| `seq_append(q, v)` | A copy of `q` with `v` appended. |
| `keys(m)`, `values(m)` | The keys or the values of a map, as a list. |
| `k in m`, `v in s` | Membership. On a map, `in` tests the **key**. |

Attribute access stays forbidden. Records are read with `r["field"]` and
nothing else, so there is exactly one way to read a record and no way to
call a method on a value.

`keys()` and `values()` are part of the language but **not** of the Z3
translation. They have no finite symbolic domain: a Z3 model does not
enumerate the keys of a map, and a translation that pretended otherwise would
have to guess. The checker rejects them with an explicit message rather than
producing a wrong answer:

```
keys()/values() are not supported in the Z3 translation (they have no finite symbolic domain)
```

They remain available in the concrete interpreter and in the generated code,
where the map is concrete and the keys can be enumerated.

#### The module system

```yaml
imports:
  - path: ./lib/common.yaml
    as: common
    version: "^1.0"
```

The imported spec's `vars`, `actions` and `invariants` are merged into the
importing spec, and every name is prefixed with the alias and an underscore:

| Imported | Merged |
| --- | --- |
| `counter` | `common_counter` |
| `bump` | `common_bump` |
| `counter_non_negative` | `common_counter_non_negative` |

Expressions inside the imported spec are rewritten to the prefixed names, so
the module keeps referring to its own variables and the merge is mechanical.

Cross-module references are not allowed. An imported spec cannot name a
variable of the importing spec, and vice versa. A module is a closed unit.

The `version` field is a semver range checked against the imported spec's
`spec_version`. The supported syntax is `1.0`, `^1.0`, `~1.0`,
`>=1.0,<2.0` and `1.x`.

## Semantics

### The finite domain rule

Every collection has a finite domain, and the domain is the index space of
the encoding. The rules:

- A `map_set` with a key outside the domain is a **no-op**. It returns a
  copy of the map, unchanged.
- A `set_add` with an element outside the domain is a **no-op**.
- A read of a key that is not present returns the **value type default**:
  `0`, `false`, `""` or the first enum value. Never an error.
- A read of a sequence index outside the sequence returns the **element type
  default**.
- A collection that grows past `max_size` is a **violation**, reported like
  an invariant violation, named `<variable>_capacity`. It is never silently
  truncated: the point of the capacity is to make the overflow visible.

The no-op rule and the default rule exist for the same reason: the
interpreter and the Z3 translation must agree on every input, including the
degenerate ones. A rule that is "whatever is convenient" in one backend and
"whatever is convenient" in another is two different semantics.

### Records

A record is read with a constant field name: `customer["active"]`. The field
name must be a string constant, because the field set is fixed by the type.

A record effect is a mapping of field name to expression, and it updates
**only** the named fields. Every other field, and every other variable, keeps
its value. This is the same frame rule that applies to variables, applied
one level down.

The initial value of a record must list every field and no other. A missing
field is an error, not a default: the initial state is the state the
implementation starts in, and a silently defaulted field is a difference
between the implementation and the contract that nobody would notice.

### Modules

Imports are resolved when the spec is loaded, before validation. The merge
order is: imported modules first, then the local spec. Imported invariants
are checked like local ones.

The digest that `verispec sign` records is computed over the **resolved**
spec, so a change in an imported module breaks the signature of the
importing spec. `verispec deps --lock` records the digest of every module
separately, so CI can tell which dependency changed.

## Encoding and semantics impact

### The Z3 encoding

Each variable, at each step, becomes a small group of Z3 constants. A scalar
is one constant. A collection becomes:

| Kind | Constants | Sorts |
| --- | --- | --- |
| `map` | `<n>_v_<t>`, `<n>_p_<t>`, `<n>_n_<t>` | `Array(Int, value)`, `Array(Int, Bool)`, `Int` |
| `set` | `<n>_p_<t>`, `<n>_n_<t>` | `Array(Int, Bool)`, `Int` |
| `seq` | `<n>_i_<t>`, `<n>_n_<t>` | `Array(Int, elem)`, `Int` |
| `record` | `<n>_<field>_<t>` | the field's own sort |

The domain slot of a key is `domain_index(k)`: `k - key_min` for an integer
domain, and the position of the key in the declared list for a string or
enum domain. A key outside the domain maps to `-1`, which is never a valid
slot, and the reads guard on it explicitly.

A map read becomes:

```
If(and(inside_domain(k), present[idx(k)]), values[idx(k)], default(value_type))
```

A map write becomes:

```
values'  = If(inside(k), Store(values, idx(k), v), values)
present' = If(inside(k), Store(present, idx(k), true), present)
size'    = If(inside(k), If(present[idx(k)], size, size + 1), size)
```

The size counter is incremented only when the key was not already present,
which is what keeps `len(m)` equal to the number of entries.

The capacity bound is a constraint on the size counter, checked at every step
like an invariant:

```
size > max_size   ->   violation, named <variable>_capacity
```

Enums are encoded as Z3 strings constrained to the enum's value set, so an
enum value *is* a string in every backend, and `status == "pending"` means
the same thing in the interpreter and in the model.

### The concrete interpreter

The interpreter has one implementation per collection operation, and the Z3
translation mirrors it operation by operation. The shared rules are the
default values, the no-op for out-of-domain keys, and the partial update for
records. There is no case where one backend does something the other does
not, because the cases are written down and the corpus checks both.

### The other backends

| Backend | Impact |
| --- | --- |
| TLA+ export | Collections become TLA+ functions (`[D -> V]`), sets (`SUBSET D`) and sequences (`Seq(S)`). Records become TLA+ records. |
| Lean export | The skeleton gains a state structure per collection type. The theorems are still `sorry`. |
| Generated tests (Python) | The generated test uses the VeriSpec runtime, so the semantics is shared, not reimplemented. |
| Generated tests (TypeScript, Go) | The transpiler gains the collection operations and the collection runtime helpers. |
| Trace checker | The JSON representation of a collection is fixed: maps are objects with stringified keys, sets and sequences are arrays, records are objects. |
| Code generators (Kani, ESBMC) | The harness gains the collection state; the scalar-only harnesses are unaffected. |

### The effect on the expression AST

The AST gains:

- a `Subscript` node, which v0.1 rejected. The subscripted expression is
  typed against the base type: a map expects a key of the key type, a
  sequence expects an `int` index, a record expects a string constant.
- the collection functions, with fixed arity: `map_get` 2, `map_set` 3,
  `set_add` 2, `set_has` 2, `seq_get` 2, `seq_append` 2, `keys` 1,
  `values` 1.
- `ast.In` and `ast.NotIn` over sets and maps, which v0.1 already parsed
  for list literals.

The AST does **not** gain: attribute access, calls to anything outside the
whitelist, floats, comprehensions over collections, lambdas, slices, or any
form of `eval`. The language stays closed.

## Compatibility and migration impact

**This is a MAJOR change.** The format version goes from `0.1` to `1.0`.

| Question | Answer |
| --- | --- |
| Do v0.1 specs still load? | Yes, unchanged. A spec with no `spec_version` is treated as v0.1. |
| Do v0.1 specs still check? | Yes. The scalar encoding is unchanged. |
| Does `verispec migrate` handle it? | Yes. It adds `spec_version: "1.0"` and changes nothing else. |
| Do v1 specs load on a v0.1 CLI? | No. A v0.1 CLI does not know the `1` major, and says so. |
| Is the MAJOR bump before or after the implementation? | With the implementation, so that no two tools disagree about a file. |

The CLI keeps accepting the previous MAJOR alongside the current one, for
one MINOR release after the MAJOR lands. A v0.1 spec is therefore still a
first-class citizen, and the tooling tells the user to migrate:

```
INFO: no-spec-version: no spec_version field: the spec is treated as format v0.1; run `verispec migrate` to move it to v1.0
```

Two semantic clarifications ship with the MAJOR, because the v0.1 wording was
misleading:

- **`min` and `max` on a state variable are documentation, not axioms.** They
  never were axioms, but the v0.1 wording suggested otherwise. A violated
  bound must not be able to hide a violated invariant, and the v1 wording says
  so plainly. Parameter bounds are different, and they *are* enforced by the
  solver.
- **The guarantee labels are unchanged.** `bounded-proved(k)`,
  `conformance-tested`, `signature-verified`, `violation-found` and `unknown`
  mean the same thing in v0.1 and in v1. Adding collections does not make a
  bounded claim unbounded.

### The corpus

The corpus gains the cases that pin down the new behaviour:

| Case | What it pins down |
| --- | --- |
| `bank` | The scalar behaviour is unchanged. |
| `bank-buggy` | A counterexample is still found, and at the same bound. |
| `collections` | Maps, sets, sequences, records, the defaults and the capacity bounds. |
| `ledger` | Maps, sets, `map_get`, `map_set`, `set_add`, membership. |
| `ledger-buggy` | A collection spec still produces a counterexample. |
| `trace-valid` | A conforming trace is accepted. |
| `trace-invalid` | A non-conforming trace is rejected. |

The corpus is the source of truth for what the format supports. If a
construct is not in the corpus, it is not part of the contract yet.

## Alternatives rejected

### Unbounded maps, sets and sequences

Rejected. The encoding is not total, the solver picks keys that no
implementation can produce, and the resulting counterexamples are not
defects. A tool that reports impossible states erodes trust faster than a
tool that cannot express them. This is the reason the domain is mandatory
rather than optional.

### A domain inferred from the initial state

Rejected. The initial state is one state; the domain has to cover every
reachable state. Inferring the domain from `init` would silently shrink it
the first time an action adds a key, and the shrinking would be invisible
until a counterexample turned into a phantom. The domain is declared, and
the declaration is checked.

### A `list` type as the universal collection

Rejected in favour of `map`, `set` and `seq`. A list with duplicate elements
and a set without them have different semantics, and a language with only
one of them forces the author to encode the other with a flag. Three
collection types, each with the operation it actually needs, is a smaller
language than one type with a mode flag.

### Unbounded sequences, with `max_size` as a hint

Rejected. A sequence without a capacity has an unbounded length, and the
size counter in the encoding becomes unbounded, and the "is the sequence
full?" question has no answer in the spec. The capacity is part of the
contract: exceeding it is a violation, named `<variable>_capacity`.

### A `default:` key on a map

Rejected. The default for a missing key is already determined by the value
type (`0`, `false`, `""`, the first enum value). A second, author-declared
default would give a map two plausible missing-key semantics, and the
implementer would have to guess which one the checker used. The type
determines it, and the type is always visible.

### Attribute access for records (`customer.active`)

Rejected. Attribute access is ambiguous with method calls, and the language
has no method calls. More importantly, `customer["active"]` and
`customer.active` would be two spellings of the same thing, and every
backend would have to support both. One spelling, one meaning.

### `keys()` and `values()` in the Z3 translation

Rejected. A map in Z3 is an array plus a presence array; the set of present
indices is not a first-order object the translation can produce. The
alternatives are worse: enumerate the domain and filter by presence (which
requires a quantifier over the whole domain, and blows up the encoding), or
approximate. The checker rejects them with an explicit message instead, and
the message names the reason.

### Cross-module references

Rejected. A module that can read its importer's variables is not a
library; it is a fragment of a program. It also makes the digest of the
importing spec depend on the importer in a way that is hard to reason about,
and it makes "import the ledger spec into the order spec" mean something
different in every context. Namespacing plus a closed module is enough for
the reuse cases the format needs.

### A `forall` quantifier over a collection

Rejected for v1. A quantifier over a collection needs the collection to be
enumerable in the solver, which is exactly the property the domain does not
give us for a symbolic map. `all(...)` and `any(...)` over list literals are
supported, which covers the common case ("every element of this declared
list satisfies this"), and a quantifier over a map is left for a future
format version, if it is ever needed.

### Bumping the MAJOR now, before the implementation

Rejected. The MAJOR bump ships with the implementation and the corpus, so
that a tool built from this commit and a tool built from the previous one
never disagree about what a file means. The RFC describes the target; the
version number moves when the target is reached.

## Unresolved questions

These are open. They did not block the implementation, and they are the
places where a future format version is most likely to go.

1. **Should the domain be derived from the value type when the value type is
   an enum?** Today an enum key uses the enum values as its domain, which is
   right. Should a *sub-range* of an enum be expressible, for example
   `enum[a,b,c]` keys restricted to `[a,b]`? The current answer is no: the
   domain of an enum is the enum.

2. **Should `max_size` be allowed to exceed the domain size?** Today a map
   with a 4-key domain and `max_size: 8` is accepted, and the capacity can
   never be exceeded, so the capacity check is dead code. The current rule is
   to accept it, because `max_size` and the domain are independent
   intentions. The alternative is to reject it, which would catch a
   copy-paste error but would also forbid a spec that wants to say "this map
   grows to at most 8 entries, and the domain may grow later".

3. **Should a collection be allowed to have a `default` element value?** For
   maps, the missing-key default is the value type's default. A spec that
   wants "an unknown account is worth 0" is fine; a spec that wants "an
   unknown account is worth -1" cannot say it. Adding a `default:` key is a
   MINOR change, and it interacts with the "one missing-key semantics" rule
   that was used to reject it the first time.

4. **Should `keys()` and `values()` be checkable in a restricted form?** For
   example, `len(keys(m)) == len(m)` is not expressible today, because
   `keys()` is not supported in Z3 at all. A restricted form ("the number of
   keys is the size counter") would be checkable, but it is a second, partial
   semantics for the same function, and two semantics for one function is
   the thing this RFC was designed to avoid.

5. **Should modules be able to export a named constant?** Today a module
   exports variables, actions and invariants. A shared constant (a limit, a
   currency) has to be duplicated or modelled as a frozen variable. The
   mechanism would be a `const:` block, and the question is whether it is
   worth a new top-level key.

6. **Should the domain be allowed to be a separate file?** A spec with 256
   keys writes 256 lines. Sharing a domain between specs would need a
   reference to another file, which is a second resolution mechanism next to
   `imports`, and the value of it is unclear until somebody asks for it.

7. **What is the migration story for a v0.1 spec that modelled a map with
   counters?** `verispec migrate` cannot rewrite a spec's structure, only
   its version. Converting a counter-encoded map into a real map is a
   redesign, and a human has to do it. The current answer is that `migrate`
   does the mechanical part and the documentation explains the manual part.

## Implementation notes

The implementation of this RFC lives in:

| File | What it implements |
| --- | --- |
| `src/verispec/types.py` | The type system, the domains, the defaults. |
| `src/verispec/values.py` | The concrete value model and the JSON representation. |
| `src/verispec/expr.py` | The AST, the type inference, the interpreter and the Z3 translation. |
| `src/verispec/model.py` | Loading, validation, imports, the concrete semantics. |
| `src/verispec/bmc.py` | The state encoding and the bounded search. |
| `src/verispec/trace.py` | The trace checker. |
| `src/verispec/codegen.py` | The TypeScript, Go and TLA+ transpilers. |
| `corpus/` | The cases that pin the behaviour down. |
| `docs/spec-format.md` | The reference. |
