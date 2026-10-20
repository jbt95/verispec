# RFC-0003: Concurrent actors and interleaving exploration

- Status: ACCEPTED
- Date: 2026-10-20
- Author: VeriSpec maintainers
- Related: RFC-0001, `docs/spec-format.md`, trace format

## Summary

Specs may optionally declare **concurrent actors** (processes or threads) with
fixed **programs** of spec actions. A dedicated checker explores reachable
**interleavings** up to a step bound, using **partial-order reduction** when
two enabled steps touch disjoint state variables. Counterexamples label each
transition with the **actor** that performed it. The JSON trace format gains an
optional `actor` field per step; existing traces without it remain valid.

## Motivation

Bounded model checking explores one transition at a time. Real systems run many
clients, workers or goroutines whose steps can interleave. Race bugs often need
two actors to overlap (for example check-then-act transfers). Without a way to
declare actors and search interleavings, the contract cannot be exercised under
concurrency without encoding every process as a separate copy of the state
machine.

## Detailed design

### Syntax

Optional top-level mapping `concurrency`:

```yaml
concurrency:
  actors:
    - id: client_a
      program:
        - action: reserve
          params: {amount: 25}
        - action: commit
          params: {amount: 25}
    - id: client_b
      program:
        - action: reserve
          params: {amount: 25}
        - action: commit
          params: {amount: 25}
```

- `id` is a non-empty string, unique among actors. It is reported in
  counterexamples and may appear in traces as `actor`.
- `program` is a non-empty list of steps. Each step has `action` (name of a
  spec action) and optional `params` (concrete parameter values). When `params`
  is omitted for an action that has parameters, the checker enumerates a finite
  set of assignments derived from each parameter's declared `min` / `max` /
  `values` (same finite domains as bounded model checking).
- An actor that finishes its program is **done** and takes no further steps.
- Specs without `concurrency` are unchanged.

### Semantics

- **Global state** is the ordinary spec state (one copy of all `vars`).
- A **configuration** is global state plus each actor's program index (how many
  steps that actor has already taken).
- A **step** is one actor executing the next action in its program with a
  concrete parameter assignment. Preconditions and effects are evaluated as in
  the sequential semantics (`apply_action`).
- **Exploration** is depth-first (or breadth-first) search over configurations
  up to `bound` total steps (transitions), starting from the initial state and
  index zero for every actor.
- **Partial-order reduction**: at a configuration, let `E` be the set of enabled
  steps. Build an **ample set** `A ⊆ E` using the standard ample-set
  construction for variable-level independence: two steps are independent when
  their read/write footprints on spec state variables are disjoint. Only steps
  in `A` are expanded at this branching point; omitted independent steps are
  explored via equivalent orderings elsewhere.
- **Violation**: any state where an invariant fails or a collection exceeds
  capacity. The search returns the shortest trace found (by number of steps).

### Trace format

Each step object may include:

```json
{"action": "transfer", "params": {"amount": 1}, "actor": "client_a", "state": {...}}
```

- `actor` is optional. When present it must be a string.
- If the spec defines `concurrency`, trace checking **requires** `actor` on every
  step after the initial state (step 0 remains the init snapshot without
  `action`).
- When `actor` is present on any step, it must name a declared actor if the
  spec defines `concurrency`.

### CLI

New subcommand `verispec check-concurrent <spec> --bound N` runs the
interleaving checker. JSON output matches the shape of `verispec check`, with
`actor` on each trace entry after step 0.

### Error behaviour

- Duplicate actor `id`: spec error at load/validate.
- Unknown action in a program step: spec error.
- Missing or invalid `params` for a step: spec error.
- `check-concurrent` on a spec without `concurrency`: spec error with a clear
  message.

## Encoding and semantics impact

- Z3 bounded model checking is unchanged (single-threaded transition system).
- TLA+ / Lean exports are unchanged; concurrency is a client-side exploration
  mode over the same sequential action semantics.
- Generated property tests are unchanged.
- Trace checker accepts optional `actor` and enforces it when `concurrency` is
  declared.

## Compatibility and migration impact

MINOR additive change to the YAML format (`concurrency` is optional). No
`spec_version` bump required. Existing specs and traces validate unchanged.
`verispec migrate` does not need changes.

## Alternatives rejected

- **Encoding N actors as N copies of state variables**: explodes spec size and
  duplicates invariants; rejected.
- **Full Z3 encoding of interleaving**: state explosion and duplicate work with
  BMC; rejected in favour of an explicit explorer with POR.
- **Only trace checking without exploration**: does not find races without a
  recorded trace; insufficient for the motivating use case.

## Unresolved questions

- Fairness or scheduling policies beyond ample-set POR (left to future work).
- Unbounded or dynamically spawned actors (not in scope; actors are fixed at
  spec load time).
