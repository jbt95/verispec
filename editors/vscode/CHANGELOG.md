# Changelog

All notable changes to the VeriSpec VS Code extension are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-10-19

### Added

- Language support for `*.verispec.yaml` / `*.verispec.yml` files: dedicated
  `verispec` language id, YAML-like language configuration (comments, brackets,
  auto-closing pairs, 2-space indentation rules) and a TextMate grammar that
  highlights spec keywords, type names, action names and invariant names.
- `VeriSpec: Validate spec` — runs `verispec validate <spec>` and reports the
  result.
- `VeriSpec: Check spec (bounded model checking)` — runs
  `verispec check <spec> --bound N --format json`, publishes a violation as an
  error diagnostic on the spec file and attaches one related information
  diagnostic per counterexample step. A successful check reports the bounded
  guarantee: "no violation up to bound N — bounded claim only, not a proof for
  all depths".
- `VeriSpec: Check trace against spec` — runs `verispec check-trace <spec>
  <trace.json>` and reports the first failing step.
- `VeriSpec: Generate agent brief` — runs `verispec brief <spec>` and opens the
  result as a read-only markdown document.
- `VeriSpec: Show report` — runs `verispec report <spec> --output <file.html>`
  and opens the HTML report in a webview panel (with an "Open in browser"
  action).
- Automatic inline diagnostics: specs are checked when they are opened, saved or
  when the relevant settings change (can be disabled with
  `verispec.showInlineDiagnostics`).
- Settings: `verispec.cliPath`, `verispec.bound`, `verispec.traceFile` and
  `verispec.showInlineDiagnostics`.
- Keyboard shortcuts: `Ctrl+Alt+V` for the bounded check, and the
  `Ctrl+Alt+B` then `Ctrl+Alt+Shift+V` chord as an alternative.
- Editor context menu entry "VeriSpec: Check spec" for VeriSpec documents.
- Friendly error when the VeriSpec CLI cannot be found, pointing at
  `pip install verispec` and the `verispec.cliPath` setting.

[0.1.0]: https://github.com/verispec/verispec/releases/tag/v0.1.0
