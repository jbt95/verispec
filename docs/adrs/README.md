# Architecture Decision Records

ADRs record the decisions that shape VeriSpec: what the core is written in,
what the spec format supports, how correctness is claimed, and which trade-offs
were accepted. They exist so that a future contributor can see why the project
looks the way it does, without having to reconstruct it from the commit history.

## Lifecycle

| Status | Meaning |
| --- | --- |
| PROPOSED | Written down, under discussion. Not binding. |
| ACCEPTED | Decided and binding. The implementation follows it. |
| SUPERSEDED | Replaced by a later ADR. The ADR stays, with a pointer to the new one. |
| DEPRECATED | No longer relevant, and not replaced (for example, an abandoned experiment). |

An ADR moves from PROPOSED to ACCEPTED when the maintainers reach lazy
consensus (see `GOVERNANCE.md`). A decision that changes the spec format also
needs the RFC process; the ADR then records the outcome and links to the RFC in
`rfcs/`.

ADRs are numbered sequentially and are **never renumbered**. A rejected or
withdrawn ADR keeps its number, so links stay valid. New ADRs take the next
free number.

## Template

```markdown
# ADR-NNNN: Title

- Status: PROPOSED | ACCEPTED | SUPERSEDED | DEPRECATED
- Date: YYYY-MM-DD
- Deciders: who took the decision
- Related: links to RFCs, issues, and other ADRs

## Context

The forces at play. What is true today, what constraints apply, and what
problem is being solved.

## Decision

The decision, in the present tense. "VeriSpec will ...".

## Consequences

What becomes easier, what becomes harder, and what is now possible or
impossible. Include the triggers that would reopen this decision.
```

## Decisions

| ADR | Title | Status | Summary |
| --- | --- | --- | --- |
| ADR-0001 | Python core vs Rust core | ACCEPTED | The core stays in Python during phases 0 and 1. Rust is only considered when a documented trigger fires. |
| ADR-0002 | Spec format v1: bounded collections | ACCEPTED | The v1 spec format adds bounded collections (lists, maps) on top of the scalar-only v0.1 format. |
| ADR-0003 | Apache-2.0 license | ACCEPTED | VeriSpec and the spec format are licensed under Apache-2.0, so that adopters are not viral. |
| ADR-0004 | Route B as the universal verification strategy | ACCEPTED | Verify the model once, then check the implementation's conformance to it. This is the default for every language. |
| ADR-0005 | Trace engine in Rust via PyO3 | PROPOSED | Move the trace checking engine to Rust, exposed to Python through PyO3, to speed up large traces. |
| ADR-0006 | Guarantee labelling | ACCEPTED | Results are labelled with the guarantee they carry: `bounded-proved(k)`, `conformance-tested`, or `signature-verified`. |
| ADR-0007 | Differential oracle for multi-language equivalence | ACCEPTED | `verispec diff-trace` compares two recorded traces and reports the first divergence, so two implementations of the same spec can be compared. |

### ADR-0006: Guarantee labelling

The label is the claim, and it is part of the machine-readable output, the
report and the MCP response. One consequence was added after the first
release: an inconclusive check no longer throws away what it *did* prove.

The checker deepens the search one bound at a time, so when the solver
cannot decide bound *k*, the bounds below it were decided. The result says
so:

```console
$ verispec check specs/orders.yaml --bound 10 --timeout 20000
no violation up to bound 6; the solver could not decide bound 7 (timeout)  (guarantee: bounded-proved(6))
```

The label names the depth that was reached, and the exit code is still `1`:
an inconclusive check must not be green.

## Notes on the open decisions

### ADR-0001: Python core vs Rust core

The core stays in Python during phases 0 and 1. The Rust rewrite is not
scheduled; it is conditional. The decision is revisited only when one of these
documented triggers fires:

- Demand for a single-binary distribution.
- Traces over 1M events or over 1GB taking more than 60 seconds.
- p95 check latency above 2 minutes.
- A need for WASM or native embedding.

Until a trigger fires, the engineering effort goes into the spec format, the
adapters and the conformance corpus.

### ADR-0005: Trace engine in Rust via PyO3

Proposed, not decided. It would keep the spec format and the public API in
Python while moving the hot loop of the trace checker to Rust. It is blocked on
ADR-0001: if the Rust trigger has not fired, there is no evidence that a Rust
trace engine is warranted.

### ADR-0007: Differential oracle for multi-language equivalence

Accepted, in the *recorded traces* form. `verispec diff-trace a.json b.json`
replays two traces and reports the first step where they differ. That is
enough for the case that matters in practice: a service rewritten in another
language, with the same session recorded on both sides.

The `examples/orders-api/` example ships three implementations of one spec
(Python, TypeScript and Go) and the three traces are byte-identical.

Still open, and deliberately out of scope: an oracle that *explores* both
implementations instead of comparing recordings. It needs a canonical state
ordering and a fair, terminating exploration strategy, and it buys little
until the implementations diverge in the first place.

## Writing a new ADR

Open a pull request adding the file to `docs/adrs/NNNN-<slug>.md` and adding a
row to the table above. The ADR starts as PROPOSED. Do not edit an accepted ADR
to reflect a new decision: supersede it instead, and say so in both files.
