# Governance

VeriSpec is a small, permissive project with an open specification format. The
governance rules below exist to keep the format open and the project moving,
not to create bureaucracy.

## Mission

VeriSpec exists to make formal, machine-checkable specifications practical for
ordinary software projects, in any language:

- One human-owned spec artifact produces a bounded model-checking result, a
  generated property-test suite, a trace conformance check for a real
  implementation, and a brief that coding agents can read.
- The spec is owned by humans. Agents read it, they never rewrite it to make a
  check pass.
- The spec format is an open standard. Any implementation may be written
  against it, in any language, without asking permission.

## Non-goals

- VeriSpec is not a general theorem prover and does not claim to be one. The
  bounded model checking result is a bounded claim, never a proof for all
  depths.
- VeriSpec is not a replacement for human review of the spec itself. A
  verified spec can still be a wrong spec.
- VeriSpec is not a hosted service. There is no multi-tenant backend and no
  telemetry.
- VeriSpec does not define a project management or issue-tracking process for
  its users. It defines a spec format, a reference implementation and a
  conformance corpus.

## The spec format is an open standard

The YAML spec format is the normative artifact of this project. The
implementation in `src/verispec/` is the reference implementation, not the
definition. Anyone may implement the format, write an adapter, or reimplement
the checker in another language.

The format changes only through the RFC process described in `CONTRIBUTING.md`:
an issue using the `RFC` template, a document filed in `rfcs/`, and a comment
period of at least two weeks. A change to the format that arrives without an
RFC is not merged, no matter how good the code is.

## Maintainers and roles

| Role | Responsibility |
| --- | --- |
| Initial maintainer | Jordi Bermejo Tornero (`@jbt95`). Final say on releases, security, and licence. |
| Maintainers | 2 to 3 people with merge rights. Review, triage, and steward the RFC process. |
| Per-language adapter maintainers | Own an `adapters/<language>/` directory and its conformance suite. Listed in `.github/CODEOWNERS`. |
| Contributors | Everyone else. Propose, review, and implement. |

The goal is 2 to 3 maintainers with merge rights. Until that is reached, the
initial maintainer holds the rights, and the invitation to join is made openly
rather than by private arrangement.

`CODEOWNERS` is how a per-language adapter maintainer gets review rights on
their directory. Adding a team to `CODEOWNERS` and to the maintainer list is
the same action.

## How decisions are made

Lazy consensus is the default:

1. The author opens an issue or a pull request and states the proposal.
2. Maintainers review. Silence for a reasonable period counts as agreement.
3. If there is no consensus, the initial maintainer decides and records the
   reason in the issue.

Lazy consensus is not used for changes to the spec format. Those require an
RFC, a comment period, and an explicit accept/reject decision. A merged pull
request is not, by itself, approval of a format change.

## Spec-format versioning

The format is versioned separately from the Python package. A spec declares
its version with the `spec_version` field:

```yaml
spec_version: "1.0"
```

The rules:

- The format follows semantic versioning.
- MINOR versions add constructs. Existing specs keep working unchanged.
- MAJOR versions are reserved for breaking changes: removing or renaming a
  construct, changing the meaning of an existing one, or making a previously
  accepted spec invalid.
- A MAJOR bump is a deliberate, rare event. When it happens, the previous
  MAJOR version keeps working for at least one further release cycle.

## Backward compatibility

- The CLI accepts at least the previous MAJOR version of the format. A user who
  pins an old spec is never broken without a MAJOR release of the tool.
- Deprecations are announced for a full MINOR release before the deprecated
  construct is removed. A deprecated construct still works and produces a
  warning, not an error.
- `verispec migrate` upgrades a spec from an older format version to the
  current one. Migration is a mechanical, reviewable transformation: the
  migrated spec is a normal pull request and is reviewed like any other spec
  change.
- The conformance corpus in `corpus/` is the source of truth for what the
  format supports. A construct that is not exercised by the corpus is not
  covered by the compatibility promise, and the RFC that introduces it must
  add corpus entries in the same change.

## Contributing and conduct

Everyone who contributes agrees to `CODE_OF_CONDUCT.md`. The initial
maintainer is the enforcement contact until an enforcement committee is named.

Participation in the RFC process is not gated by affiliation, employer, or
commercial interest. Academic, hobbyist, and commercial use of the format are
all welcome.

## Not a foundation yet

VeriSpec is not a foundation, and the initial maintainer is the steward of the
process above. That is a deliberate choice at the current stage: a foundation
adds legal and administrative overhead that a project with this size does not
need yet.

A neutral foundation would be considered when at least one of the following
becomes true:

- The format is used in production by organisations that are not connected to
  the maintainer and need an independent steward.
- A language adapter is maintained by a team that the initial maintainer does
  not control, and decisions about the format start to affect them directly.
- There is a real need for a neutral place to hold the trademark, the domain
  name, and the grant of the specification itself.

Until then, the governance above is the governance of the project, and anyone
who wants to change it can do so through the same public, written process used
for the spec format.
