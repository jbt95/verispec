# RFC-0006: Fine-grained drift mapping

- Status: IMPLEMENTED
- Date: 2026-10-21
- Author: VeriSpec maintainers
- Related: ADR-0006 (guarantee labelling), `docs/spec-format.md`, `src/verispec/drift.py`

## Summary

The spec format gains an `implements` mapping on **invariants**, beside the
one it already has on actions, and the drift checker compares the contract of
each action and each invariant *separately* instead of comparing one digest
of the whole spec. "The implementation changed but the contract did not" is
then a statement about the code that changed, not about the file it lives in.

## Motivation

### The problem with one digest per spec

`verispec drift` answers a question that only makes sense at the level of a
single piece of contract: *did the behaviour that this contract describes
change without the contract changing?*  The current implementation answers it
with one digest of the whole spec, so the answer is "yes" for every action
whenever anything in the spec file changes — a comment, a renamed variable, a
new invariant.

```console
$ verispec drift spec/orders.yaml --range HEAD~1..HEAD
drift: spec or signature changed but mapped implementation for action 'create' did not
drift: spec or signature changed but mapped implementation for action 'cancel' did not
drift: spec or signature changed but mapped implementation for action 'refund' did not
```

None of those three findings says anything about the three actions.  The spec
changed; the actions did not.  A gate that cries wolf on every commit is a
gate that gets switched off, and the finding that *would* have mattered — the
implementation of `refund` changed and nobody touched the contract of
`refund` — is drowned in the noise.

The same coarseness hides the interesting case in the other direction.  When
the implementation of one action changes, the checker compares the digest of
the whole spec: if the spec changed *anywhere*, the finding is suppressed.
So a change to `create` and a change to `refund` cancel each other out.

### The problem with no mapping for invariants

An invariant is a claim about the implementation, and today there is no way
to say *which* code it is a claim about.  A spec with twenty invariants over
four files can map its actions to those files, but the invariant "the balance
is never negative" cannot be attached to the account service that has to
maintain it.  The only thing the checker can say is that the spec file
changed.

### What it costs

Every false finding costs a reviewer's attention, and the reviewer who learns
that the gate is noisy stops reading it.  The cost of noise is not the noise:
it is the one real finding in a hundred that nobody reads.

## Detailed design

### Syntax

An invariant may carry an `implements` mapping, with exactly the same shape
an action's mapping has today:

```yaml
actions:
  - name: refund
    implements:
      - path: src/orders/refund.ts
      - glob: src/orders/*.test.ts

invariants:
  - name: the_balance_is_never_negative
    expr: "balance >= 0"
    implements:
      - path: src/accounts/ledger.ts
```

The mapping is optional.  An invariant without one is not mapped to any
implementation, and the drift checker says nothing about it beyond the whole
spec's digest.

A mapping entry is either

- `path: <repo-relative path>` — one file, or
- `glob: <pattern>` — every file in the repository whose repo-relative path
  matches the pattern (`fnmatch` syntax, `*` and `?`).

### Semantics

The drift checker compares, for each action and each invariant, the digest of
**that element's own contract** between the base and the head:

- an action's contract digest covers its `requires`, its `effects`, and the
  types and bounds of its `params`;
- an invariant's contract digest covers its `expr`.

An element's contract "changed" when its digest differs between the two
refs.  An element that is absent at one side has an empty digest, so adding
or removing an action or an invariant is a change to that element.

Four findings replace the two of today:

| Finding | Meaning |
| --- | --- |
| `implementation_changed_without_spec` | The implementation mapped to this action or invariant changed; its contract did not. |
| `spec_changed_without_implementation` | The contract of this action or invariant changed; the implementation mapped to it did not. |
| `invariant_changed_without_implementation` | The expression of this invariant changed; the implementation mapped to it did not. |
| `implementation_changed_without_invariant` | The implementation mapped to this invariant changed; the invariant did not. |

The whole-spec digest and the signed digest keep their meaning: a change to
either still requires a human to re-approve the spec.  They are no longer
used to decide *which* action or invariant drifted.

An action with no `implements` mapping produces no per-action finding.  The
checker does not guess: an unmapped action is not evidence of drift, and a
finding that names no file cannot be acted on.

### Error behaviour

- An `implements` mapping that is neither a list nor a mapping is a spec
  error: `action 'refund': 'implements' must be a list or a mapping`.
- An `implements` entry with neither `path` nor `glob` is a spec error:
  `action 'refund': an 'implements' entry needs a 'path' or a 'glob'`.
- A `glob` that matches nothing is **not** an error.  A test file that is
  deleted later must not make the spec invalid.

### Worked example

```console
$ verispec drift spec/orders.yaml --range HEAD~1..HEAD
drift: the implementation mapped to action 'refund' changed, but the contract of 'refund' did not
  implementation: src/orders/refund.ts
drift: the contract of invariant 'the_balance_is_never_negative' changed, but the implementation mapped to it did not
  implementation: src/accounts/ledger.ts
```

## Encoding and semantics impact

None.  `implements` is metadata: it is not read by the type checker, the
interpreter, the Z3 encoding, the TLA+ export, the generated property tests
or the trace checker.  The contract's meaning, and every result derived from
it, is unchanged.

## Compatibility and migration impact

**MINOR.**  The change is additive: a spec that does not use the new mapping
behaves exactly as before, except that the drift findings are finer-grained
and the per-action "the spec changed" noise is gone.

`spec_version` goes from `1.0` to `1.1`.  `verispec migrate` is not needed:
a `1.0` spec is a valid `1.1` spec.

The corpus gains a case: a spec with a mapped invariant, a mapped action
and a mapped glob, checked exactly as an unmapped spec would be — the
mapping changes nothing about the result.  The *drift* behaviour is covered
by the drift tests instead: the corpus runs the checker, and the drift
checker compares two git refs, which is a different thing.

## Alternatives rejected

**Compare the spec digest per file.**  The spec is one file; splitting it by
file would not help, and it would make the digest depend on the file layout.

**Use the signed digest per element.**  The signature covers the whole spec
by design: a human approves the contract as a whole.  Element-level digests
would need element-level signatures, which is a different feature with a
different trust story.

**Infer the mapping from the names.**  Matching `action 'refund'` to
`src/orders/refund.ts` by convention is a guess.  A guess that is wrong
produces a false finding, which is the thing this RFC exists to reduce.

**Report every changed element without a mapping.**  A finding that names no
file cannot be acted on, and there is one per action in every spec.  The
whole-spec digest already says "something in the contract changed, re-approve
it".

## Unresolved questions

- Should the mapping be allowed on `vars` as well, so that a state variable
  can name the code that owns it?  The same argument applies, but no case has
  needed it yet.
- Should the drift checker compare the *elements* of a record or a module
  import the way it compares actions?  Same argument, same answer so far.
