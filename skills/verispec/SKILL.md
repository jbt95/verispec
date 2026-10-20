---
name: verispec-cli
description: >-
  Install and run the VeriSpec CLI: validate YAML state-machine specs, bounded Z3
  model checking with counterexamples, trace conformance, generated property tests,
  reports, and the MCP server. Use when verifying implementations against a formal
  spec, debugging counterexamples, or wiring VeriSpec into CI or agent workflows.
---

# VeriSpec CLI

VeriSpec is a free, open-source Python toolkit. One human-owned YAML spec drives
bounded model checking (Z3), trace replay, exports, and generated tests. **Agents
must treat the spec as read-only**; fix the implementation when checks fail.

## Install

Requires **Python 3.11+**. Z3 ships as the **`z3-solver`** PyPI dependency (no
separate Z3 install).

```bash
git clone https://github.com/verispec/verispec.git
cd verispec
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[test]"
verispec --version
```

| Extra | Install | Purpose |
| --- | --- | --- |
| `test` | `pip install -e ".[test]"` | pytest, hypothesis (default for development) |
| `dev` | `pip install -e ".[dev]"` | ruff, mypy, plus test deps |
| `api` | `pip install -e ".[api]"` | FastAPI/httpx for the orders-api **example** only |

From PyPI (when published): `pip install verispec` with the same optional extras.

## Specs

- **Format:** YAML state machine (`spec_version`, `vars`, `init`, `actions`,
  `invariants`). Reference: `docs/spec-format.md`.
- **Examples:** `examples/bank.yaml`, `examples/ledger.yaml`, `examples/orders-api/orders.verispec.yaml`.
- **Scaffold:** `verispec init <dir> --template ledger --lang python --name mysvc`
  Templates: `ledger`, `idempotency`, `order`, `rate-limiter`, `queue`.
- **Validate before checking:** `verispec validate path/to/spec.yaml`

## Exit codes (stable for CI)

| Code | Meaning |
| --- | --- |
| `0` | OK (check passed, validate OK, etc.) |
| `1` | Violation (counterexample, bad trace, lint findings, signature mismatch) |
| `2` | Spec or trace error (invalid YAML, bad expression, missing file) |
| `3` | Tooling error (unexpected failure, interrupt) |

Many commands accept `--format text|json|sarif` (default `text`). `report` defaults
to HTML when `--format` is omitted.

## Guarantees (read CLI output)

| Label | Meaning |
| --- | --- |
| `bounded-proved(k)` | No invariant violated in any execution up to depth *k* (not a full proof) |
| `bounded-liveness-proved(k)` | No liveness counterexample (lasso) up to depth *k* (not a full proof) |
| `conformance-tested` | Recorded trace replayed and every step conformed |
| `pending-obligation` | Trace conforms but a temporal property is not yet satisfied at trace end |
| `violation-found` | Concrete counterexample |
| `unknown` | Solver or checker could not decide |

## Commands

Global: `verispec --help`, `verispec --version`.

### `version`

```bash
verispec version
verispec version --format json
```

### `validate` — parse and type-check a spec

```bash
verispec validate examples/bank.yaml
verispec validate examples/bank.yaml --format json
```

### `lint` — vacuity / trivial invariants on the spec itself

```bash
verispec lint examples/bank.yaml
```

Exit `1` if there are error-level findings.

### `check` — bounded model check (Z3)

```bash
verispec check examples/bank.yaml --bound 10
verispec check examples/bank-buggy.yaml --bound 10 --invariant non_negative
verispec check examples/bank.yaml --bound 8 --timeout 30000 --format json
```

| Flag | Default | Role |
| --- | --- | --- |
| `--bound` | `10` | Max transition steps |
| `--invariant` | — | Only search violations of this invariant name |
| `--timeout` | `30000` | Z3 timeout (ms) |
| `--format` | `text` | `text`, `json`, or `sarif` |
| `--skip-liveness` | off | Safety invariants only (skip temporal liveness) |
| `--temporal` | — | Only check this named temporal property |
| `--cache-dir` | — | Incremental result cache (or set `VERISPEC_CACHE_DIR`) |
| `--no-cache` | off | Skip reading/writing the check cache |
| `--wall-timeout` | — | Wall-clock cap for the whole check (ms); yields `unknown` |
| `--memory-mb` | — | Process memory cap (MiB); yields `unknown` if exceeded |
| `--solver-memory-mb` | — | Z3 `max_memory` (MiB) |
| `--parallel-invariants` | off | Check each invariant in a separate worker (one spec) |

When the spec declares `temporal` properties, `check` also runs **bounded
liveness** (lasso-shaped counterexamples) after safety, unless
`--skip-liveness`. Guarantees may include `bounded-liveness-proved(k)`.

On violation, text output includes step-by-step state, action, params, and
which invariant broke.

### `check-suite` — many specs (cache + parallelism)

```bash
verispec check-suite examples/bank.yaml examples/ledger.yaml --bound 8 --jobs 4
verispec check-suite corpus/*/spec.yaml --bound 4 --cache-dir .verispec-cache --format json
```

| Flag | Default | Role |
| --- | --- | --- |
| `--jobs` | `1` | Parallel workers across specs (output order stays deterministic) |
| (scale flags) | — | Same cache, timeout, and memory flags as `check` |

Cache keys include the resolved spec digest (imports included), checker options
(including `--skip-liveness` and `--temporal`), and the VeriSpec version.
Unchanged entries are skipped on reruns.

### `check-trace` — replay an implementation trace

Trace JSON: list of `{"action", "params", "state"}` steps; first step is `init`.

```bash
verispec check-trace examples/bank.yaml examples/traces/bank-valid.json
verispec check-trace examples/bank.yaml examples/traces/bank-invalid.json --format json
```

### `gen-tests` — property tests for an adapter

```bash
verispec gen-tests examples/bank.yaml --lang python -o tests/test_bank_verispec.py
verispec gen-tests examples/bank.yaml --lang typescript -o src/bank.verispec.test.ts
verispec gen-tests examples/bank.yaml --lang go --package bank -o bank/bank_verispec_test.go
```

`--lang`: `python`, `typescript`, `javascript`, `go`, `rust`, `c`, `java`.
Optional: `--adapter`, `--package` (Go).

### `export-tla` / `export-lean`

```bash
verispec export-tla examples/bank.yaml -o Bank.tla
verispec export-lean examples/bank.yaml -o Bank.lean
```

### `brief` — agent-readable contract summary

```bash
verispec brief examples/bank.yaml -o AGENT_BRIEF.md
```

### `report` / `view` — HTML or JSON reports

```bash
verispec report examples/bank.yaml --bound 10 -o report.html
verispec report examples/bank.yaml --trace examples/traces/bank-valid.json --format json
verispec view examples/bank-buggy.yaml --bound 10 -o counterexample.html
```

`report` flags: `-o`, `--trace`, `--bound`, `--invariant`, `--format` (default html).

### `init` — template scaffold

```bash
verispec init ./my-service --template ledger --lang python --name ledger
verispec init --template queue --lang go --force
```

`--lang`: `python`, `typescript`, `go`. `--force` overwrites existing files.

### `sign` / `verify-signature`

```bash
verispec sign examples/bank.yaml --approver "Jane Doe" --note "v1 review"
verispec sign examples/bank.yaml --approver "Jane" --gpg --gpg-key ABCD1234
verispec verify-signature examples/bank.yaml
verispec verify-signature examples/bank.yaml --require-cryptographic
```

### `migrate` — bump `spec_version` in file

```bash
verispec migrate old.yaml --in-place
verispec migrate old.yaml   # prints migrated YAML to stdout
```

### `deps` — spec imports lockfile

```bash
verispec deps examples/bank.yaml
verispec deps examples/bank.yaml --lock specs.lock
verispec deps examples/bank.yaml --check-lock specs.lock
```

### `registry` — browse bundled reusable specs

```bash
verispec registry
verispec registry ledger
verispec registry --name ledger
```

### `diff-trace` — compare two traces

```bash
verispec diff-trace examples/traces/bank-valid.json examples/traces/bank-valid.json
```

Exit `1` if traces diverge.

### `monitor` — check a trace file (optional follow)

```bash
verispec monitor traces/live.json --spec examples/bank.yaml
verispec monitor traces/live.json --spec examples/bank.yaml --follow --interval 1.0
```

### `corpus` — run conformance fixtures

```bash
verispec corpus corpus --bound 8
verispec corpus --bound 4 --format json
```

### `check-tlc` — TLA+ export via TLC (if `tla2tools.jar` installed)

```bash
verispec check-tlc examples/bank.yaml --timeout 120 -o /tmp/Bank.tla
```

### `gen-harness` — Kani / ESBMC skeleton

```bash
verispec gen-harness examples/bank.yaml --target kani -o harness.rs
verispec gen-harness examples/bank.yaml --target esbmc -o harness.c
```

## MCP server (`verispec mcp`)

Stdio JSON-RPC (Model Context Protocol). Tools: **`verify`**, **`describe_spec`**,
**`list_invariants`**.

```bash
verispec mcp
```

Example initialize (one line on stdin):

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}' | verispec mcp
```

**`verify`** arguments: `spec_path` (required), optional `bound`, `trace_path`,
`invariant`. Returns JSON with `status`, `guarantee`, and on violation `violations`,
`trace`, and `guidance`. MCP `isError` is true when status is not `ok`.

See `docs/agent-integration.md` for full tool schemas and agent loop patterns.

## Common errors

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| `error: ...` on stderr, exit `2` | Invalid spec expression, unknown action, bad trace JSON | Run `verispec validate`; fix spec or trace shape |
| `solver returned unknown`, exit `1` | Z3 timeout or hard query | Increase `--timeout` or `--bound`; treat as inconclusive (`unknown` guarantee) |
| `invariant ... not found` | Wrong `--invariant` name | `verispec brief` or `list_invariants` via MCP |
| `trace file does not exist` | Bad path for `check-trace` / `monitor` | Pass path to JSON trace list |
| Import error installing | Python &lt; 3.11 | Upgrade Python |

## Agent workflow (minimal)

1. `verispec validate` + `verispec brief` on the human-owned spec.
2. `verispec check` on the spec; if implementing, `verispec check-trace` on adapter traces.
3. On exit `1`, read the counterexample or trace failure; **change code, not the spec**.
4. Optionally run `verispec mcp` and call `verify` from the IDE agent.

## Further reading

- `README.md`, `docs/feature-tour.md`, `docs/ci.md`, `docs/adapter-authoring.md`

## Escalating verification (`verify-loop`, `spec-strength`)

### `verify-loop` — tri-state checks with cheap-then-Z3 escalation

Runs trace monitor and bounded random walks first, then Z3 for unresolved
invariants. Stops at the first violation. Default **2 rounds**.

```bash
verispec verify-loop examples/bank.yaml --trace examples/traces/bank-valid.json
verispec verify-loop examples/bank-buggy.yaml --rounds 2 --z3-bound 12 --format json
```

| Flag | Default | Role |
| --- | --- | --- |
| `--trace` | — | JSON trace for cheap trace-monitor evidence |
| `--rounds` | `2` | Escalation rounds |
| `--fuzz-bound` | `6` | Max steps per fuzz trial |
| `--fuzz-trials` | `40` | Fuzz trials per invariant |
| `--z3-bound` | `12` | Z3 depth for unresolved checks |
| `--timeout` | `30000` | Z3 timeout (ms) |
| `--seed` | `0` | RNG seed for fuzz walks |
| `--format` | `text` | `text`, `json`, or `sarif` |

Exit `0` only when verdict is `success`. `failure` or `uncertain` → exit `1`.

### `spec-strength` — spec vacuity via trace perturbations

Mutates a known-good trace (omit step, swap steps, replace action) and reports
mutants the spec still accepts (**survived** = possibly too weak).

```bash
verispec spec-strength examples/bank.yaml --trace examples/traces/bank-valid.json
verispec spec-strength examples/bank.yaml --walk-length 4 --seed 0 -v
```

| Flag | Default | Role |
| --- | --- | --- |
| `--trace` | — | Known-good trace (else random model walk) |
| `--walk-length` | `4` | Steps when synthesising a base trace |
| `--seed` | `0` | RNG seed |
| `--max-mutants` | — | Cap number of mutants |
| `-v` / `--verbose` | off | Also list caught mutants |
| `--format` | `text` | `text`, `json`, or `sarif` |

Exit `1` if any mutant **survived** (spec may be too weak).

### `coverage` — spec coverage from traces and test events

Reports which actions, guard branches (`requires`), invariants, and parameter
boundaries were exercised.  Traces mark successful guard paths; optional
coverage-events JSON can record rejected guards from property tests.

```bash
verispec coverage examples/bank.yaml --trace examples/traces/bank-valid.json
verispec coverage examples/bank.yaml --trace a.json,b.json --events run.json --format json
verispec coverage examples/bank.yaml --trace traces.json --fail-under 80
```

| Flag | Default | Role |
| --- | --- | --- |
| `--trace` | — | Comma-separated JSON trace files |
| `--events` | — | Comma-separated coverage-events JSON files |
| `--fail-under` | — | Exit `1` when overall coverage % is below this value |
| `--format` | `text` | `text`, `json`, or `sarif` |

Action `implements` mappings are not required for coverage.

### `drift` — spec vs implementation drift in git

Each action may declare where its implementation lives:

```yaml
actions:
  - name: transfer
    implements:
      - path: src/transfer.py
      - glob: "pkg/**/adapter*.go"
```

Given a git range, flags implementation changes without a spec/signature digest
change, and the reverse.

```bash
verispec drift examples/bank.yaml --base main --head HEAD
verispec drift svc.yaml --range origin/main..HEAD --format sarif
```

| Flag | Role |
| --- | --- |
| `--base` / `--head` | Git refs to compare |
| `--range` | `BASE..HEAD` (alternative to `--base`/`--head`) |
| `--format` | `text`, `json`, or `sarif` |

Exit `1` when drift findings are present. The GitHub Action exposes this as
optional input `drift-check: true`.
