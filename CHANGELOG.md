# Changelog

All notable changes to VeriSpec are documented here. The project follows
[semantic versioning](https://semver.org/) for the package, and the spec
format has its own version, carried by the `spec_version` field (see
[`GOVERNANCE.md`](GOVERNANCE.md)).

## [1.0.0] - 2026-10-19

The first open source release of the library and the CLI. The v0.1 proof of
concept becomes a usable tool: the spec format gains bounded collections,
records and modules; the CLI gains stable exit codes, JSON and SARIF output,
an HTML report, a counterexample viewer, a linter, signing, a registry and an
MCP server; and the adapters cover Python, TypeScript, Go, Rust, C and
Java/Kotlin.

### Spec format

- **Added** `spec_version`, with a compatibility policy: the CLI accepts the
  previous MAJOR version, and `verispec migrate` upgrades a spec.
- **Added** bounded collections: `map`, `set` and `seq`, each with a finite
  domain and a capacity. They are encoded in Z3 as arrays plus a size counter,
  so every read is total and no phantom counterexample can appear.
- **Added** records, read with `x["field"]` and written field by field.
- **Added** modules: `imports` merges another spec's vars, actions and
  invariants under an alias prefix, with semver ranges.
- **Added** the collection functions `map_get`, `map_set`, `set_add`,
  `set_has`, `seq_get`, `seq_append`, `keys` and `values`, the subscript
  syntax `c[k]` and membership over maps and sets.
- **Clarified** that the `min` / `max` of a state variable are documentation,
  not solver axioms. They never were, and saying so prevents a violated
  bound from hiding a violated invariant.
- v0.1 specs keep working unchanged.

### CLI

- **Added** `verispec init` with five templates: `ledger`, `idempotency`,
  `order`, `rate-limiter` and `queue`.
- **Added** `verispec lint`: tautological invariants, actions that can never
  be enabled, actions that change nothing, unbounded variables and parameters.
- **Added** `verispec report` (HTML and JSON) and `verispec view` (the
  counterexample viewer).
- **Added** `verispec sign` and `verispec verify-signature`: the SHA-256
  digest of the spec, the approver, and an optional GPG detached signature.
- **Added** `verispec migrate`, `verispec deps` (with a lock file),
  `verispec registry`, `verispec diff-trace` (the differential oracle),
  `verispec monitor` (replay a live trace), `verispec corpus`,
  `verispec check-tlc` and `verispec gen-harness` (Kani and ESBMC).
- **Added** `verispec mcp`: an MCP server with the `verify`, `describe_spec`
  and `list_invariants` tools.
- **Added** `--format json` and `--format sarif` to the check commands, and
  stable exit codes: 0 ok, 1 violation, 2 spec error, 3 tooling error.
- Every result now carries a guarantee label: `bounded-proved(k)`,
  `conformance-tested`, `signature-verified`, `violation-found` or `unknown`.
- **Changed** an inconclusive check now says what it *did* prove: "no
  violation up to bound 6; the solver could not decide bound 7 (timeout)",
  with the guarantee label naming the depth that was reached.
- **Fixed** the TypeScript and Go generators, which dropped every update of a
  map or a set (the domain was taken from the key type instead of from the
  collection), wrote the initial state of a spec with collections as a
  literal that does not compile, and produced a TypeScript file that did not
  type check when a map holds an enum.
- **Fixed** the Python and TypeScript generators, which crashed on a spec
  with a set (the initial state was serialized as a Python `set`).

### Examples

- **Added** `examples/orders-api/`: a REST API with a database and real
  business rules (no overselling, one payment per order, revenue that
  matches the paid orders), implemented in **Python** (FastAPI + SQLite),
  **TypeScript** (`node:http` + `node:sqlite`) and **Go** (`net/http` +
  SQLite), all against **one** spec. The three implementations record the
  same session, and `verispec diff-trace` proves they agree.
- **Added** the `verispec corpus` case for the orders API, and the
  `examples/orders-api` test suite to the CI.

### Adapters

- **Added** the TypeScript adapter with rich fast-check parameters and a
  trace recorder, shipped with both an ESM and a CommonJS build.
- **Added** the JavaScript adapter: the same conformance test without the
  type syntax, generated with `verispec gen-tests --lang javascript`.
- **Added** the Go adapter.
- **Added** the Rust and C adapters.
- **Added** the Java/Kotlin adapter templates and the trace recorder SDK.
- **Added** the Python trace recorder SDK (`verispec.sdk.TraceRecorder`).

### CI and packaging

- **Added** the GitHub Action: bounded model checking, trace checking, SARIF
  for code scanning, the HTML report as an artifact, a pull request comment
  with the counterexample, and the read-only spec policy.
- **Added** the GitHub Action, the release workflow (PyPI and npm) and the
  Docker image. The CI/CD story is GitHub: the action for users, and the
  release pipeline for the project.
- **Added** the conformance corpus, which is the source of truth for the
  format.

### Project

- **Changed** the license to Apache-2.0, for the explicit patent grant.
- **Added** the governance, contributing, code of conduct and security
  policies, the issue templates, the ADR index and the RFC process.
- VeriSpec collects no telemetry.
