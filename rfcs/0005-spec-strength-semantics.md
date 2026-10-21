# RFC-0005: Semantic spec-strength (VerusEval-style checks)

- Status: ACCEPTED
- Date: 2026-10-21
- Author: VeriSpec maintainers
- Related: `verispec spec-strength`, `verispec verify-loop`, `src/verispec/spec_semantic.py`

## Summary

Extend `spec-strength` with language-agnostic semantic diagnostics inspired by
[VerusEval](https://arxiv.org/abs/2610.26464) (ideas only; no code or dataset
porting): Z3 vacuity, concrete I/O suites, and bounded reference comparison.
A combined `semantic_verdict` is emitted in the existing JSON CLI envelope.

## Motivation

Verifier acceptance and trace conformance can succeed while a YAML contract is
vacuous, too weak, or inconsistent with intended behavior. VerusEval shows that
only a minority of accepted LLM-generated Verus contracts pass triviality, I/O,
and reference checks together. VeriSpec needs the same *classes* of evidence for
state-machine specs.

## Detailed design

### Vacuity (Z3)

Per global invariant `Q`:

- **True postcondition:** prove `Q` is valid without preconditions (tautology).
- **False precondition:** for each action, prove `requires` is unsatisfiable.
- **Post implied by pre:** with `requires` on the pre-state, prove global
  invariants already hold before the step.
- **Tautological step post:** prove invariants on the successor state are valid
  without `requires` (one-step encoding shared with BMC).

### I/O suites (JSON)

File shape:

```json
{
  "cases": [
    {
      "kind": "correct",
      "before": {"balance_a": 50, "balance_b": 50},
      "action": "transfer_a_to_b",
      "params": {"amount": 10},
      "after": {"balance_a": 40, "balance_b": 60}
    }
  ]
}
```

Each case is checked with `check_trace_data`. Kinds:

| Kind | Pass condition |
| --- | --- |
| `correct` | Trace conforms |
| `wrong_output` | Trace rejected |
| `invalid_input` | Trace rejected |

When only `correct` cases are supplied, the implementation may synthesize
wrong-output mutants by perturbing the final state.

### Reference comparison (bounded Z3)

Given candidate spec `S` and reference `R` with the same variables and action
names, check VerusEval-style obligations on global invariants (post) and per-action
`requires` (pre):

| Obligation | Meaning |
| --- | --- |
| L1 | `R.pre => S.pre` |
| L2 | `S.post => R.post` |
| L3 | `S.pre => R.pre` |
| L4 | `R.post => S.post` |

Classification:

- **equivalent:** L1–L4 proved
- **stronger:** L1 and L2 proved, not complete
- **weaker:** L3 and L4 proved, not sound
- **incomparable:** refuted implications
- **unknown:** solver timeout

Counterexamples name the first refuted obligation.

### Combined verdict

| `semantic_verdict` | When |
| --- | --- |
| `ok` | No vacuity, I/O OK, reference not weaker/incomparable, no mutation survivors |
| `vacuous` | Z3 vacuity finding proved |
| `rejects-valid-io` | A `correct` I/O case fails |
| `accepted-but-weak` | Surviving trace mutants, I/O accepts bad outputs, or reference weaker |

Trace perturbation diagnostics from StepBench remain unchanged.

### `verify-loop` integration

Optional flags `--semantic-strength`, `--io-examples`, and `--reference-spec` run
the same checks before invariant escalation; the loop stops on the first semantic
violation.

## Encoding and semantics impact

Uses existing `to_z3` and BMC transition encoding; no YAML format change.

## CLI

New `spec-strength` flags: `--io-examples`, `--reference-spec`, `--no-semantic`,
`--no-auto-io-mutate`, `--z3-bound`, `--timeout`.

Exit code `1` when `semantic_verdict != ok` or trace mutants survive.

## Examples

`examples/spec-strength/` demonstrates a weak bank spec, I/O file, and comparison
against `examples/bank.yaml`.
