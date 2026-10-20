# RFC-0002: Temporal (liveness) properties and fairness

- Status: OPEN
- Date: 2026-10-20
- Author: VeriSpec contributors
- Related: `docs/spec-format.md`, RFC-0001

## Summary

Specs may declare **temporal** properties (`eventually`, `leads_to`) and optional
**fairness** assumptions on actions (weak and strong). The bounded checker
searches for **lasso-shaped** counterexamples (bounded liveness). TLA+ export
emits the matching temporal formulas and fairness conjuncts. Finite implementation
traces report **pending** obligations when a property is not yet discharged at
trace end.

## Motivation

Safety invariants say what must hold in every state. Many contracts also need
progress: requests are eventually acknowledged, locks are eventually released,
or a pending flag eventually becomes done. Today those obligations live only in
comments or external tools. This RFC adds a first-class, checkable fragment in
the YAML spec while keeping existing specs valid unchanged.

## Detailed design

### `fairness` (optional)

```yaml
fairness:
  weak: [complete, retry]
  strong: [watchdog]
```

| Key | Meaning |
| --- | --- |
| `weak` | Weak fairness (`WF`): on each loop cycle of a counterexample, if the action is enabled at every transition in the cycle, it must be taken at least once in that cycle (bounded approximation). |
| `strong` | Strong fairness (`SF`): on each loop cycle, if the action is enabled at any transition in the cycle, it must be taken at least once in that cycle (bounded approximation). |

An action name must exist in `actions`. The same action cannot appear in both lists.

### `temporal` (optional)

```yaml
temporal:
  - name: eventually_done
    kind: eventually
    goal: 'phase == "done"'

  - name: pending_leads_done
    kind: leads_to
    when: 'phase == "pending"'
    goal: 'phase == "done"'
```

| Field | Required | Meaning |
| --- | --- | --- |
| `name` | yes | Unique property name. |
| `kind` | yes | `eventually` or `leads_to`. |
| `goal` | yes | Boolean expression (the `Q` in `<> Q` or `P ~> Q`). |
| `when` | for `leads_to` | Boolean `P` in `[] (P => <> Q)`. |

`eventually` means **eventually** `goal` holds. `leads_to` means **always**
(`when` implies **eventually** `goal`).

Expressions use the same AST as invariants.

### Bounded liveness checking

`verispec check` runs safety BMC first, then (unless `--skip-liveness`) searches
for a lasso up to `--bound`: a reachable prefix where all invariants hold,
`goal` (and `leads_to` obligations) fail on the prefix, and two indices
`loop_start < loop_end` identify equal states so the suffix can repeat forever.
Fairness constraints apply to transitions in `[loop_start, loop_end)`.

The guarantee label is `bounded-liveness-proved(k)` when no counterexample is
found up to depth *k*. This is **not** a full proof of liveness for unbounded
depth.

### Finite traces

`verispec check-trace` still validates steps and invariants. When `temporal` is
present:

- If `goal` is seen (or a `leads_to` obligation is discharged), the property is
  **satisfied** on that trace.
- Otherwise the trace ends with **pending** obligations (`guarantee:
  pending-obligation`, exit code 1). This is inconclusive, not a safety
  violation.

### TLA+ export

`export-tla` adds:

- `WF_<action> == WF_vars(<action>)` and `SF_<action> == SF_vars(<action>)`
- Temporal definitions with `<>` and `[] (P => <>)`
- `Spec` conjuncts for fairness
- `Temporal ==` conjunction of temporal property names

## Encoding and semantics impact

- New module `verispec.temporal` owns parsing, trace monitoring, liveness BMC,
  and TLA fragments.
- `model.Spec` loads `fairness` and `temporal`; imports namespace action names
  and rename expressions in temporal goals.
- Expression AST unchanged.

## Compatibility and migration impact

**MINOR** additive change to format 1.0. Specs without `fairness` or `temporal`
behave as before. No `migrate` step required.

## Alternatives rejected

- **Full LTL in YAML**: too heavy for authors and the bounded encoder; the
  two-property fragment covers common patterns.
- **Unfair liveness proofs**: without fairness, stuttering counterexamples are
  ubiquitous; fairness is explicit rather than baked into stutter steps.

## Unresolved questions

- Stronger loop semantics for `leads_to` on infinite suffixes (beyond the
  bounded prefix approximation).
- TLC configuration snippets for temporal properties in generated `.cfg` files.
