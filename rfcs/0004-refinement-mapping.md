# RFC-0004: Refinement mapping between specs

- Status: ACCEPTED
- Date: 2026-10-20
- Author: VeriSpec maintainers
- Related: RFC-0002 (temporal liveness), RFC-0003 (concurrency, independent), `verispec refines`, `src/verispec/refinement.py`

## Summary

A concrete (implementation-level) spec may declare that it **refines** an
abstract (business-level) spec. The declaration is a **refinement mapping**:
an expression for each abstract state variable that computes the abstract view
of a concrete state, and a rule for each concrete action that names either an
abstract action (with parameter expressions) or an explicit **stutter** (no
abstract step). The CLI command `verispec refines` checks, with the same
bounded Z3 encoding as `verispec check`, that every concrete execution up to
depth *k* can be matched to abstract behaviour under the mapping and that
abstract invariants hold on the coupled abstract state.

## Motivation

Teams often maintain a small abstract contract (what the business cares about)
and a richer implementation model (pending transfers, caches, retries). Today
those live in separate YAML files with no machine-checked link. Refinement
mapping makes the simulation relation explicit and checkable, with
counterexamples that show **both** concrete and abstract states.

## Detailed design

### Syntax

On the **concrete** spec only:

```yaml
refines:
  spec: path/to/abstract.yaml   # required; resolved relative to the concrete file
  state:                          # required; abstract var -> expression over concrete vars
    balance_a: "balance_a + pending_a"
    balance_b: "balance_b"
  actions:                        # required; every concrete action must appear
    initiate_transfer:
      stutter: true
    complete_transfer:
      abstract: transfer_a_to_b
      params:
        amount: amount
```

Rules:

| Field | Meaning |
| --- | --- |
| `refines.spec` | Path to the abstract spec YAML. |
| `refines.state` | Map from **abstract** variable name to an expression using **concrete** state variables. Types must match the abstract variable types. |
| `refines.actions.<concrete>` | Either `{stutter: true}` or `{abstract: <name>, params?: {...}}`. |
| `params` | Maps **abstract** parameter names to expressions over concrete parameters and concrete state. Omitted params default to same-named concrete parameters when types match. |

Exactly one of `stutter: true` or `abstract: ...` must be set per action.
`stutter` and `abstract` together is an error.

The abstract spec is unchanged: no back-link is required.

### Semantics (bounded)

Fix a depth bound *K*. A **coupled trace** is a concrete trace
`c₀ … c_K` and abstract trace `a₀ … a_K` such that:

1. **Initial coupling:** `α(c₀) = a₀` where `α` is given by `refines.state`.
2. **Abstract invariants:** every abstract invariant holds at each `aₜ`.
3. **Step simulation:** for each transition `cₜ → cₜ₊₁` labelled with concrete
   action `ac` (or stutter):
   - If `ac` maps to **stutter:** `aₜ₊₁ = aₜ` and `α(cₜ₊₁) = aₜ`.
   - If `ac` maps to abstract action `aa` with parameters `p̄:` the abstract
     machine takes `aa(p̄)` from `aₜ` to `aₜ₊₁` and `α(cₜ₊₁) = aₜ₊₁`.
   - If the concrete step is a **stutter** (no concrete action), the abstract
     step must stutter and `α(cₜ₊₁) = aₜ₊₁`.

`verispec refines` searches for a **refinement violation** within *K* concrete
steps: a coupled prefix where one of the obligations above fails. The
guarantee label is `bounded-refinement-proved(k)` when no violation is found.

### Error behaviour

| Mistake | Result |
| --- | --- |
| Missing `refines` on concrete spec | Spec error: concrete spec has no `refines` block |
| Abstract path missing | Spec error with resolved path |
| Unknown abstract var in `state` | Spec error |
| Missing concrete action in `actions` | Spec error listing missing names |
| Unknown abstract action name | Spec error |
| Expression type mismatch | Spec error at parse/validate time |

### Worked example

See `examples/bank-concrete-pending.yaml` refining `examples/bank.yaml`: two
accounts with **pending** outbound transfers; initiating a transfer stutters on
the abstract bank, completing it maps to `transfer_a_to_b` / `transfer_b_to_a`.

## Encoding and semantics impact

- **Z3:** Product of two transition systems with coupling constraints at each
  time index; action alignment is encoded with implications from the concrete
  action choice to the abstract action choice (or stutter). Uses the same
  array-plus-size collection encoding as RFC-0001.
- **TLA+:** Optional comment block in the concrete module export listing the
  refinement map (`export_refinement_tla`); full refinement proofs in TLA+ are
  out of scope for v1.
- **Expression AST:** Unchanged; mapping expressions are ordinary spec
  expressions over concrete variables.
- **Trace format / adapters:** Unchanged.

## Compatibility and migration impact

MINOR additive change to the YAML format: specs without `refines` are
unaffected. `spec_version` remains `1.0`. No migration tool change required.

Corpus: optional future entry; the worked example lives under `examples/`.

## Alternatives rejected

- **Separate refinement file:** Duplicates action names and paths; keeping the
  mapping on the concrete spec makes the implementation contract self-contained.
- **Only check α-invariants on concrete:** Does not catch wrong abstract steps;
  full simulation coupling is required.
- **Unbounded refinement proof:** Out of scope; bounded checking matches the
  rest of VeriSpec's guarantees.

## Unresolved questions

- Whether to require bijective action names by default (deferred: explicit
  `params` mapping is enough for the first release).
