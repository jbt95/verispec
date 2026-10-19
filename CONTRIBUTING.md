# Contributing to VeriSpec

Thanks for helping. VeriSpec exists because a formal spec should be owned by
humans, verified by machines, and portable across languages. Contributions are
welcome in the code, the specs, the adapters, the docs and the spec format
itself.

## Development setup

VeriSpec targets Python 3.11 or newer.

```bash
git clone https://github.com/jbt95/verispec.git
cd verispec

# with uv
uv venv
uv pip install -e ".[test]"

# or with plain pip inside a virtualenv
python -m venv .venv
source .venv/bin/activate
pip install -e ".[test]"
```

This installs the `verispec` CLI and the test dependencies (pytest, hypothesis).

## Running the tests

```bash
pytest -q
```

The suite lives in `tests/` and must pass before a pull request is opened.
Tests that rely on Z3 are the slow ones; `pytest -q tests/test_verispec.py` runs
a single file when iterating.

## Code style

| Tool | Purpose | Command |
| --- | --- | --- |
| ruff | linting and formatting | `ruff check .` and `ruff format .` |
| mypy | static type checking | `mypy src/verispec` |

Both must be clean. Keep public functions typed and avoid `eval`/`exec` on spec
expressions: the expression language is an explicit AST whitelist in
`src/verispec/expr.py` and any attempt to widen it to arbitrary Python is a
security problem, not a feature request.

## Commits and pull requests

- One logical change per pull request. Do not mix a bug fix with a format
  change.
- Write commit messages in the imperative mood: `Reject unbound action params
  in the loader`.
- Reference the issue the change addresses (`Fixes #42`).
- Open a draft pull request early if the design needs discussion; convert it to
  ready for review when the tests are green.
- Continuous integration must be green before a pull request is merged.

## Proposing a spec-format change

The spec format is an open standard, not an implementation detail. Any change
that adds, removes, or alters a construct in the YAML format follows the RFC
process:

1. Open an issue using the `RFC` issue template with the title convention
   `RFC: <summary>`. Explain the motivation and the design in prose.
2. File the RFC document in `rfcs/` as `rfcs/NNNN-<slug>.md`. The issue and the
   document must say the same thing.
3. The comment period lasts a minimum of two weeks. The maintainers announce
   the start and the end of the period in the issue.
4. After the period closes, the maintainers record the decision: accepted,
   accepted with changes, or rejected. Accepted RFCs are marked accordingly in
   `rfcs/` and referenced in the changelog.

Adoption follows semantic versioning. A MINOR release adds constructs; a MAJOR
release is reserved for breaking changes (see `GOVERNANCE.md`).

## Writing or contributing a language adapter

An adapter is the small, language-specific piece that connects a real
implementation to a VeriSpec spec. It must expose `reset()`, `apply(action,
**params)` and `snapshot() -> dict` returning exactly the variables declared in
the spec.

- The adapter contract and the authoring guide live in
  `docs/adapter-authoring.md`.
- The shared, language-agnostic test scaffolding lives in
  `adapters/conformance/`. An adapter that does not pass the conformance suite
  is not accepted.
- A new adapter must also register a spec in `corpus/` so the generated
  property tests and the trace conformance checks are exercised in CI.

The generated property tests and the trace checker are the two checks an
adapter must satisfy. A counterexample that reproduces only in one language is
usually a spec bug or an adapter bug, not a VeriSpec bug: reproduce it and
report it as a false positive (see below).

## Reporting a false positive

A false positive is a counterexample that VeriSpec reports but that is not a
real violation of the spec, or a trace that VeriSpec rejects but that is valid.

- Open an issue with the `False positive` issue template.
- Paste the exact counterexample trace, the bound used, and the command you ran.
- Explain why you believe the counterexample is spurious: the invariant is
  unreachable, the precondition is satisfied, the trace does not correspond to
  a reachable state, and so on.
- State the impact: it blocks CI, it is noise, it slows down the run, and so on.

Do not work around a false positive by weakening the spec. Specs are human-owned
and reviewed; a silent edit hides the bug for everybody else.

## Compatibility policy

The spec format has its own compatibility policy, separate from the Python
package version:

- MAJOR versions are reserved for breaking changes.
- MINOR versions add constructs and are backwards compatible.
- The CLI keeps accepting at least the previous MAJOR version of the format.
- Deprecations are announced for a full MINOR release before removal, and
  `verispec migrate` upgrades specs to the current format.

`corpus/` is the source of truth for what the format supports. If a construct
is not in the corpus, it is not part of the contract yet.

## Code of conduct

Participation in this project is governed by `CODE_OF_CONDUCT.md`. By
contributing you agree to uphold it.
