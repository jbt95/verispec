# VeriSpec agent skill

VeriSpec is a human-owned YAML state machine with invariants. Agents read specs;
they do not edit specs to make checks pass.

## Common CLI

```bash
verispec validate <spec.yaml>
verispec check <spec.yaml> --bound 10
verispec check-trace <spec.yaml> <trace.json>
```

## Concurrent actors

When a spec declares `concurrency.actors` with per-actor `program` steps, explore
interleavings (with partial-order reduction on disjoint variable footprints):

```bash
verispec check-concurrent <spec.yaml> --bound 10
verispec check-concurrent <spec.yaml> --bound 10 --format json
verispec check-concurrent <spec.yaml> --invariant non_negative
```

Counterexamples label each transition with `actor`. Traces for concurrent specs
must include `actor` on every step after the initial state snapshot.

Example: `examples/bank-concurrent-race.yaml`.
