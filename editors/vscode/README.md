# VeriSpec for VS Code

Verify [VeriSpec](https://github.com/verispec/verispec) formal specifications
from the editor: run bounded model checking, validate implementation traces and
read counterexamples without leaving the file you are editing.

The extension is a thin, read-only client of the `verispec` CLI. It **never
edits, rewrites or formats spec files** — a spec is owned by a human, and the
extension only reports what the CLI found.

## Requirements

- Visual Studio Code `^1.85.0`.
- The **VeriSpec CLI** available as an executable:
  - installed from PyPI (`pip install verispec`), or
  - pointed at with the `verispec.cliPath` setting.

If the CLI cannot be found the extension shows a friendly message instead of a
stack trace, and nothing is executed.

## Features

- **Syntax highlighting** for `*.verispec.yaml` and `*.verispec.yml` files:
  spec keywords (`vars`, `init`, `actions`, `requires`, `effects`,
  `invariants`, …), type names (`int`, `bool`, `str`, `enum`, …), action names
  and invariant names.
- **Inline diagnostics** — the spec is checked automatically when it is opened
  or saved. A violated invariant becomes an error diagnostic on the
  `invariants:` block, with one *related information* diagnostic per
  counterexample step (action, params and state).
- **Bounded checks** — `VeriSpec: Check spec` runs the model checker with the
  configured bound. A green result is reported with the honest wording
  *"no violation up to bound N — bounded claim only, not a proof for all
  depths"*.
- **Trace conformance** — `VeriSpec: Check trace against spec` validates a JSON
  trace (as produced by an implementation adapter) against the spec and
  reports the first failing step.
- **Agent brief** — `VeriSpec: Generate agent brief` opens the `verispec brief`
  markdown output in a read-only virtual document.
- **Report** — `VeriSpec: Show report` renders the HTML report produced by
  `verispec report` in a webview panel, with an *Open in browser* button.

## Keyboard shortcuts

| Shortcut | Command |
| --- | --- |
| `Ctrl+Alt+V` | `VeriSpec: Check spec` |
| `Ctrl+Alt+B` then `Ctrl+Alt+Shift+V` | `VeriSpec: Check spec` (alternative chord) |

The same command is available from the command palette
(`VeriSpec: …`) and from the editor context menu
(**VeriSpec: Check spec**).

## Settings

| Setting | Type | Default | Description |
| --- | --- | --- | --- |
| `verispec.cliPath` | `string` | `"verispec"` | Path to the `verispec` executable. Use an absolute path if the CLI is not on your `PATH`. |
| `verispec.bound` | `number` | `10` | Bounded model checking depth (K). Minimum `1`, maximum `1000`. |
| `verispec.traceFile` | `string` | `""` | Path to a JSON trace file to check. When empty, the active JSON file (or a file picker) is used. |
| `verispec.showInlineDiagnostics` | `boolean` | `true` | Run a bounded check on open/save and publish results as inline diagnostics. |

## Building and packaging

```bash
cd editors/vscode
npm install       # installs typescript, @types/vscode and @vscode/vsce
npm run compile   # tsc -p ./  ->  out/extension.js
npm run watch     # incremental compile
npm run lint      # eslint src --ext ts
npm run package   # vsce package -> verispec-0.1.0.vsix
```

Install the packaged extension locally with:

```bash
code --install-extension verispec-0.1.0.vsix
```

## Notes and limitations

- The extension is **read-only with respect to specs**: it never edits, moves or
  reformats a `*.verispec.yaml` file. Fix your implementation, not the spec.
- Bounded model checking is bounded: a green result is a claim about depths up
  to the bound only, never a proof for all depths.
- Automatic checks are skipped in untrusted workspaces; manual commands always
  ask the CLI for the file you selected.
- The extension depends on the CLI contract (`validate`, `check`,
  `check-trace`, `brief`, `report`); see `editors/README.md` for the shared
  contract and the exit codes.
