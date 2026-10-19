# VeriSpec in CI

CI is where a VeriSpec spec earns its keep. The spec is the contract; the
pipeline is what stops a change from quietly breaking it.

- [What to run](#what-to-run)
- [Exit codes](#exit-codes)
- [The VeriSpec action](#the-verispec-action)
- [GitHub Actions](#github-actions)
- [The read-only spec policy](#the-read-only-spec-policy)
- [Releasing](#releasing)
- [What to do when CI fails](#what-to do-when-ci-fails)

---

## What to run

| Command | What it checks | Typical exit |
| --- | --- | --- |
| `verispec validate <spec>` | The spec parses and type checks. | 0 or 2 |
| `verispec lint <spec>` | The spec is not vacuous. | 0 or 1 |
| `verispec check <spec> --bound N` | No invariant is violated up to depth N. | 0 or 1 |
| `verispec check-trace <spec> <trace.json>` | A recorded run of the real implementation conforms. | 0 or 1 |
| `verispec verify-signature <spec>` | The spec still matches the digest a human approved. | 0 or 1 |
| `verispec report <spec> -o report.html` | A conformance report for a human to read. | 0 or 1 |
| `verispec view <spec> -o counterexample.html` | The counterexample viewer. | 0 or 1 |

Two of these produce machine-readable output for code scanning:

```console
$ verispec check specs/bank.yaml --bound 10 --format sarif > verispec.sarif
$ verispec check specs/bank.yaml --bound 10 --format json > verispec.json
```

`--format json` prints a machine-readable result. `--format sarif` prints
SARIF 2.1.0, which GitHub code scanning consumes directly; the JSON output is
there for any other pipeline that wants to make its own decisions.

## Exit codes

The exit code is the contract between the tool and your pipeline. It is
stable, and it is the same everywhere:

| Code | Meaning | What the pipeline should do |
| --- | --- | --- |
| `0` | OK. The check passed. | Continue. |
| `1` | Violation. A counterexample was found, or a trace did not conform. | Fail the job. This is a real defect. |
| `2` | Spec error. The spec (or the trace) is invalid. | Fail the job. The contract is broken, not the code. |
| `3` | Tooling error. Something in VeriSpec went wrong. | Fail the job, but report it: this is a bug in the tool. |

An inconclusive solver result (the guarantee label `unknown`, for example when
the solver times out) also exits with `1`. A pipeline therefore treats "no
information" as a failure, which is the safe default.

```yaml
# The idiomatic way to keep going and still fail at the end.
- run: verispec check specs/bank.yaml --bound 10 --format sarif > verispec.sarif || true
- run: verispec check specs/bank.yaml --bound 10
```

The first line produces the SARIF file even when a counterexample exists (so
code scanning gets the result); the second line fails the job with the real
exit code. Never use `|| true` on the *last* check of a job: that is how a
broken contract gets merged.

## The VeriSpec action

The shortest way to add VeriSpec to a repository is the published action. It
does everything: it checks the spec, checks the traces, uploads SARIF for code
scanning, publishes the HTML report as an artifact, and comments the pull
request with the counterexample.

```yaml
name: verispec

on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read
  security-events: write   # upload SARIF to code scanning
  pull-requests: write     # comment the pull request

jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: verispec/verispec@v1
        with:
          spec: specs/bank.verispec.yaml
          traces: traces/bank.json
          bound: "10"
```

| Input | What it does |
| --- | --- |
| `spec` | The spec to check. Required. |
| `traces` | Comma separated JSON traces to replay against the spec. |
| `bound` | The bound for the bounded model checker. Default `10`. |
| `timeout-ms` | The solver timeout per bound. Default `30000`. |
| `python-version` | The Python version used to run VeriSpec. Default `3.11`. |
| `version` | The VeriSpec version to install. Empty installs this repository. |
| `comment` | Comment the pull request with the counterexample. Default `true`. |
| `report` | Publish the HTML report as an artifact. Default `true`. |
| `require-spec-approval` | Fail when the spec changed without a human approval. Default `true`. |
| `approval-label` | The label that marks an approved spec change. Default `spec-approved`. |

The action fails the job with the real exit code, so a violation is a red
build.

## GitHub Actions

Save this as `.github/workflows/verispec.yml`. It runs the checker, uploads
SARIF to code scanning, publishes the HTML report as an artifact, and comments
the pull request with the counterexample.

```yaml
name: verispec

on:
  pull_request:
    paths:
      - "specs/**"
      - "adapters/**"
      - "traces/**"
      - ".github/workflows/verispec.yml"
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: read

env:
  SPECS: >-
    specs/bank.yaml
    specs/ledger.yaml
  BOUND: "10"

jobs:
  # ---------------------------------------------------------------------
  # The spec is human owned. A pull request that changes it needs a human.
  # ---------------------------------------------------------------------
  spec-approval:
    name: Spec approval
    if: github.event_name == 'pull_request'
    runs-on: ubuntu-latest
    permissions:
      pull-requests: read
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Detect changes to the spec
        id: spec
        run: |
          changed=$(git diff --name-only \
            "${{ github.event.pull_request.base.sha }}" \
            "${{ github.event.pull_request.head.sha }}" \
            -- 'specs/**/*.yaml' 'specs/**/*.yml' 'specs/**/*.verispec.yaml')
          if [ -n "$changed" ]; then
            echo "changed=true" >> "$GITHUB_OUTPUT"
            echo "::notice::This pull request changes the spec: $changed"
          else
            echo "changed=false" >> "$GITHUB_OUTPUT"
          fi

      - name: Require the human approval label
        if: steps.spec.outputs.changed == 'true'
        env:
          GH_TOKEN: ${{ github.token }}
          PR: ${{ github.event.pull_request.number }}
        run: |
          labels=$(gh pr view "$PR" --json labels --jq '.labels[].name')
          if ! printf '%s\n' "$labels" | grep -qx 'spec-approved'; then
            echo "::error::This pull request changes a spec. A human must review the diff and add the 'spec-approved' label before it can merge."
            exit 1
          fi
          echo "The spec change carries the 'spec-approved' label."

  # ---------------------------------------------------------------------
  # The actual verification.
  # ---------------------------------------------------------------------
  verify:
    name: Verify the contract
    needs: spec-approval
    runs-on: ubuntu-latest
    permissions:
      contents: read
      security-events: write   # upload SARIF to code scanning
      pull-requests: write     # comment the pull request
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install VeriSpec
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[test]"

      - name: Validate the specs
        run: |
          for spec in $SPECS; do
            echo "::group::$spec"
            verispec validate "$spec"
            verispec lint "$spec"
            echo "::endgroup::"
          done

      - name: Model check (SARIF for code scanning)
        run: |
          status=0
          for spec in $SPECS; do
            verispec check "$spec" --bound "$BOUND" --format sarif > "${spec%.yaml}.sarif" || status=$?
          done
          cat ./*.sarif > verispec.sarif
          rm -f ./*.sarif
          echo "exit_code=$status" >> "$GITHUB_OUTPUT"
        id: check

      - name: Check the recorded traces
        run: |
          status=0
          for trace in traces/*.json; do
            [ -e "$trace" ] || continue
            spec="specs/$(basename "${trace%.json}").yaml"
            [ -f "$spec" ] || continue
            echo "::group::$trace"
            verispec check-trace "$spec" "$trace" || status=$?
            echo "::endgroup::"
          done
          exit "$status"

      - name: Build the report and the counterexample view
        if: always()
        run: |
          verispec report specs/bank.yaml -o verispec-report.html --bound "$BOUND" || true
          verispec view specs/bank.yaml -o verispec-counterexample.html --bound "$BOUND" || true

      - name: Upload SARIF to code scanning
        if: always()
        uses: github/codeql-action/upload-sarif@v3
        with:
          sarif_file: verispec.sarif
          category: verispec

      - name: Publish the report as an artifact
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: verispec-report
          path: |
            verispec-report.html
            verispec-counterexample.html
          if-no-files-found: ignore

      - name: Comment the pull request with the counterexample
        if: always() && github.event_name == 'pull_request'
        uses: actions/github-script@v7
        with:
          script: |
            const fs = require('fs');
            const {owner, repo} = context.repo;
            const issue_number = context.payload.pull_request.number;

            let body = '## VeriSpec\n\n';
            let failed = false;

            for (const spec of process.env.SPECS.trim().split(/\s+/)) {
              const out = await exec.getExecOutput('verispec', ['check', spec, '--bound', process.env.BOUND, '--format', 'json']);
              const result = JSON.parse(out.stdout);
              body += `### \`${spec}\`\n\n`;
              if (result.status === 'ok') {
                body += `No violation up to bound ${result.bound}. Guarantee: \`${result.guarantee}\`.\n\n`;
                continue;
              }
              failed = true;
              body += `**Counterexample found** at bound ${result.bound}. Guarantee: \`${result.guarantee}\`.\n\n`;
              body += '```\n';
              for (const v of result.violations) {
                body += `violated: ${v.name} (${v.kind}) ${v.expr}\n`;
                body += `  at step ${v.step}: ${JSON.stringify(v.state)}\n`;
              }
              body += '```\n\n';
              for (const step of result.trace) {
                body += `- step ${step.step}: ${step.action ?? '(init)'} ${JSON.stringify(step.params ?? {})} -> ${JSON.stringify(step.state)}\n`;
              }
              body += '\n';
            }

            if (failed) {
              body += 'Fix the implementation, not the spec. The spec is human owned.\n';
            }

            const marker = '<!-- verispec -->';
            const comments = await github.paginate(github.rest.issues.listComments, {owner, repo, issue_number});
            const existing = comments.find(c => c.body && c.body.includes(marker));
            await github.rest.issues.createOrUpdateComment({
              owner, repo, issue_number,
              body: marker + '\n' + body,
              comment_id: existing ? existing.id : undefined,
            });

      - name: Fail the job when a violation was found
        if: steps.check.outputs.exit_code != '0'
        run: |
          echo "::error::VeriSpec found a violation (exit code ${{ steps.check.outputs.exit_code }})."
          exit 1
```

### Notes on the workflow

- `spec-approval` runs before `verify`, and `verify` `needs` it. A pull
  request that changes a spec and has no `spec-approved` label never reaches
  the checker.
- The SARIF step uses `|| status=$?` rather than `|| true`, so the exit code
  survives until the last step, where the job fails with it.
- The comment step is `if: always()`, so a pull request that breaks the
  contract gets a comment saying so, instead of silence.
- `actions/upload-artifact@v4` with `if-no-files-found: ignore` keeps the
  workflow green when there is nothing to publish.

### A minimal workflow

If you only want the check and the SARIF upload:

```yaml
name: verispec

on: [pull_request, push]

permissions:
  contents: read
  security-events: write

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
      - run: pip install -e ".[test]"
      - run: verispec check specs/bank.yaml --bound 10 --format sarif > verispec.sarif || true
      - uses: github/codeql-action/upload-sarif@v3
        with:
          sarif_file: verispec.sarif
      - run: verispec check specs/bank.yaml --bound 10
```

## The read-only spec policy

**The spec is the contract, and it is owned by humans.** The tooling never
edits it, the agents never edit it, and a pull request that weakens it needs a
human to say so.

What counts as weakening a spec:

- a removed or relaxed invariant,
- a removed or relaxed precondition,
- a raised bound that hides a deeper counterexample,
- a new action whose `requires` clause is trivially satisfiable,
- a new collection domain that excludes a key the implementation can produce.

What the policy looks like in practice:

| Mechanism | What it does |
| --- | --- |
| A `spec-approval` job | Requires the `spec-approved` label on the pull request when a spec file changed. |
| `CODEOWNERS` | Lists the spec directory, so a reviewer is requested automatically. |
| `verispec sign` and `verispec verify-signature` | Record the digest a human approved, and fail when the spec changes afterwards. |

The digest is the strongest mechanical guarantee. Sign the spec once a human
has approved it:

```console
$ verispec sign specs/bank.yaml --approver alice --note "reviewed, Q3 contract"
signed Bank (sha256:9b267ba92b5b4c91e8286fed866b4cca1f029ce93734456685f07c82f71b84f1)
signature written to specs/bank.yaml.signature.json
  No cryptographic signature: the approver is a recorded claim.
```

And check it in CI:

```console
$ verispec verify-signature specs/bank.yaml
OK: spec matches its approved digest (approver: alice). No cryptographic signature: the approver is a recorded claim, not proof.
```

Add `--gpg` to `verispec sign` to attach a detached GPG signature. Then the
approver is a cryptographic fact, not a claim, and the verifier says so.

## Releasing

VeriSpec releases itself with GitHub Actions. Pushing a tag builds the
distributions, publishes them, and creates the release:

```console
$ git tag v1.0.1 && git push origin v1.0.1
```

The `release` workflow (`.github/workflows/release.yml`) has four jobs:

| Job | What it does |
| --- | --- |
| `build` | Builds the Python distribution and the npm package, checks them, and uploads them as artifacts. |
| `publish-pypi` | Publishes to PyPI with trusted publishing (no token needed, the `id-token: write` permission is enough). |
| `publish-npm` | Publishes `@verispec/adapter` to npm. Needs an `NPM_TOKEN` secret in the `npm` environment. |
| `release` | Creates the GitHub release with the generated notes and the artifacts attached. |

The npm package lives in `adapters/typescript/`, so the adapter SDK and the
Python CLI are released from the same tag.

## What to do when CI fails

| Exit code | Meaning | What to do |
| --- | --- | --- |
| `1` on `check` | A counterexample exists. | Fix the implementation. The trace in the comment and in `verispec-counterexample.html` is a reproduction recipe. |
| `1` on `check-trace` | The real implementation diverged from the spec. | Fix the implementation, or fix the recorder if it recorded the wrong state. |
| `1` on `verify-signature` | The spec changed after it was approved. | Get a human to re-approve it and re-sign. |
| `2` | The spec is invalid. | Fix the spec, with a human. |
| `3` | The tool failed. | Report it with the exact command and the output. |

Never respond to a red build by raising the bound, deleting the invariant, or
relaxing the precondition. That is the one move the policy forbids, and it is
the one that hides real defects from everybody who reads the spec later.

If the counterexample is a false positive, report it with the `False
positive` issue template. See [`docs/guarantees.md`](guarantees.md) for the
false-positive policy.
