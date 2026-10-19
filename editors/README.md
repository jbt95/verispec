# Editor integrations

Editor integrations for VeriSpec live in this directory. Each one is a
standalone package that talks to the **VeriSpec CLI** and never edits a spec:
the spec is a human-owned artifact, an editor integration only *reads* it and
*reports* what the CLI found.

| Integration | Path | Notes |
| --- | --- | --- |
| VS Code | [`vscode/`](vscode/) | Language support for `*.verispec.yaml`, bounded checks, trace validation, agent brief, HTML report. |

## The shared CLI contract

Every integration relies on the same command line interface. Keep it in mind
when the CLI evolves — an integration is only useful if the contract below is
stable.

### Commands

| Command | Purpose | Output |
| --- | --- | --- |
| `verispec validate <spec>` | Parse and validate the spec. | `OK: spec <name> valid (<n> vars, <n> actions, <n> invariants)` |
| `verispec check <spec> --bound K [--format json]` | Bounded model checking (Z3). | Human readable text, or a JSON object with `--format json`. |
| `verispec check-trace <spec> <trace.json>` | Check an implementation trace against the spec. | `trace OK: <n> steps conform to spec <name>` or the first failing step. |
| `verispec brief <spec>` | Agent-facing markdown brief. | Markdown on stdout (or a file with `--output`). |
| `verispec report <spec> --output <file.html>` | HTML report. | Writes the report file. |
| `verispec gen-tests <spec> --lang python\|typescript --output <file>` | Generate property tests. | Writes the test file. |
| `verispec view <spec> --output <file.html>` | Interactive HTML view. | Writes the view file. |

### Exit codes

| Code | Meaning | What an integration should do |
| --- | --- | --- |
| `0` | Success / no violation found. | Report success. |
| `1` | Violation found (model check or trace check). | Report the counterexample as an error. |
| `2` | Spec error (the file is not a valid spec). | Report the parse/validation error, do not blame the implementation. |
| `3` | Tooling error (the CLI could not be run, solver failure, …). | Report a tooling problem, never a spec violation. |

### `check --format json` output shape

```jsonc
{
  "status": "ok" | "violation" | "unknown",
  "bound": 10,                       // the bound that was used
  "message": "no violation up to bound 10", // present for "ok" / "unknown"
  "violated": ["total_conserved"],   // invariant names, present for "violation"
  "trace": [                         // counterexample steps, present for "violation"
    { "step": 0, "action": "transfer_a_to_b", "params": { "amount": 50 }, "state": { "balance_a": 0 } }
  ]
}
```

A violation always carries the list of violated invariant names and the
shortest counterexample that reaches the violation. A successful check is a
**bounded** claim: "no violation up to bound K" — never "proven for all
depths".

## Adding another editor integration

1. **Create a directory** next to `vscode/`, e.g. `editors/neovim/`, with its
   own manifest and its own build. Keep it self-contained: no shared runtime
   dependency on the other integrations.
2. **Talk to the CLI, never to the spec internals.** Spawn `verispec` as a
   subprocess with an argument array (no shell interpolation) and rely on the
   exit codes above. If the CLI is missing, show a message that explains how
   to install it and how to point the integration at a custom path.
3. **Prefer machine-readable output.** Use `check --format json` and render the
   result yourself; fall back to the human readable output only when the JSON
   format is unavailable.
4. **Keep the honest wording.** A green bounded check must be reported as
   "no violation up to bound K — bounded claim only, not a proof for all
   depths", and a counterexample must be shown with the step, the action, its
   parameters and the resulting state.
5. **Never write to the spec file.** Integrations read specs and report
   violations; they never rewrite a spec, weaken an invariant or add a
   precondition to silence a counterexample.
6. **Document the integration** in the table above and add a short `README.md`
   inside the new directory explaining the requirements (CLI on `PATH` or a
   configurable path), the features and the build/packaging steps.
