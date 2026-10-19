# Pull request

## What does this change?

<!-- Describe the change in a few sentences. Link the issue it addresses, e.g. "Fixes #42". -->

## Roadmap area

<!-- Which area of the project does this belong to? One of:

- Core engine (`src/verispec/`: parser, expression AST, BMC, TLA+ export, generated tests, trace checker)
- CLI
- Language adapter (`adapters/<language>/`)
- Conformance corpus (`corpus/`)
- Spec format
- Docs
- Tooling / CI
-->

## Checklist

- [ ] Tests added or updated, covering the new behaviour
- [ ] `pytest -q` passes
- [ ] `ruff check .` and `ruff format --check .` are clean
- [ ] `mypy src/verispec` is clean
- [ ] If the spec format changed: the conformance corpus in `corpus/` is updated in the same change
- [ ] Docs updated (`docs/`, `README.md`) where behaviour or the format changed
- [ ] If the spec format changed: the PR links to an accepted RFC in `rfcs/`

## Specs are human-owned

Spec files are the contract, and they are owned by humans, not by the code and
not by the tooling. A pull request that weakens an invariant, a precondition, a
bound, or a `requires` clause in order to silence a counterexample must be
rejected by reviewers.

Before approving a spec change, check the diff for:

- a removed or relaxed invariant
- a removed or relaxed precondition
- a raised bound that hides a deeper counterexample
- a new action whose `requires` clause is trivially satisfiable

If a counterexample is real, the fix belongs in the implementation, not in the
spec. If the counterexample is a false positive, report it with the `False
positive` issue template instead of editing the spec.
