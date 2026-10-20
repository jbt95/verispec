# Spec format RFCs

The VeriSpec spec format is an open standard, not an implementation detail.
Any change that adds, removes or alters a construct in the YAML format goes
through this process, so that the format stays stable and the decisions behind
it stay visible.

An RFC is a design document. It is written before the code, and it is the
thing the implementation is checked against.

- [When an RFC is needed](#when-an-rfc-is-needed)
- [The lifecycle](#the-lifecycle)
- [How to propose one](#how-to-propose-one)
- [The template](#the-template)
- [The decisions](#the-decisions)
- [Relationship with ADRs](#relationship-with-adrs)
- [After acceptance](#after-acceptance)

---

## When an RFC is needed

| Change | RFC? |
| --- | --- |
| A new type, function or field in the YAML format | Yes. |
| A change in the meaning of an existing construct | Yes. |
| A change in the semantics of the expression language | Yes. |
| A change to the Z3 encoding that changes what a result means | Yes. |
| A change to the trace format or the adapter contract | Yes. |
| A new template, a new CLI flag, a new export target | No. |
| A bug fix that restores the documented behaviour | No. |
| Performance work that does not change the semantics | No. |

When in doubt, write the RFC. The cost of an unnecessary RFC is an afternoon;
the cost of an unannounced format change is a broken contract in someone
else's repository.

## The lifecycle

| Status | Meaning |
| --- | --- |
| DRAFT | Being written. Not filed yet. |
| OPEN | Filed, comment period running. Not binding. |
| ACCEPTED | Decided and binding. The implementation follows it. |
| ACCEPTED WITH CHANGES | Decided, with the listed amendments applied to the document. |
| REJECTED | Not going to happen. The document stays, with the reason. |
| SUPERSEDED | Replaced by a later RFC. The document stays, with a pointer. |
| IMPLEMENTED | Accepted and shipped. The implementation and the corpus match it. |

The comment period lasts **a minimum of two weeks**. The maintainers announce
the start and the end of the period in the issue. After the period closes,
the maintainers record the decision: accepted, accepted with changes, or
rejected. Accepted RFCs are marked accordingly in this directory and
referenced in the changelog.

RFCs are numbered sequentially and are **never renumbered**. A rejected or
withdrawn RFC keeps its number, so links stay valid. New RFCs take the next
free number.

## How to propose one

1. **Open an issue** using the `RFC` issue template, with the title
   convention `RFC: <summary>`. The template asks for the summary, the
   motivation, the detailed design, the encoding and semantics impact, the
   compatibility and migration impact, the alternatives rejected, and the
   unresolved questions.
2. **File the document** in this directory as `rfcs/NNNN-<slug>.md`. The
   issue and the document must say the same thing.
3. **Announce the comment period** in the issue, with the end date.
4. **Answer the questions.** The comment period is where the open questions
   get answered; update the document as the answers come in.
5. **Record the decision** in the issue and update the status in the document.

The document is the source of truth. If the issue and the document disagree,
the document wins, and the issue is updated.

## The template

```markdown
# RFC-NNNN: Title

- Status: DRAFT | OPEN | ACCEPTED | ACCEPTED WITH CHANGES | REJECTED | SUPERSEDED | IMPLEMENTED
- Date: YYYY-MM-DD
- Author: who wrote it
- Related: issues, ADRs, other RFCs

## Summary

One paragraph, in the language of the spec format. What changes.

## Motivation

Why this is needed. What cannot be expressed or verified today, and what that
costs. Concrete examples beat abstractions.

## Detailed design

The concrete shape of the change:

- syntax: the YAML that a spec would look like,
- semantics: the evaluation rules, the defaults, the edge cases,
- error behaviour: what a user sees when they get it wrong,
- worked examples.

## Encoding and semantics impact

How the change affects the Z3 encoding, the TLA+ export, the Lean export, the
generated property tests, the code generators and the trace checker. Include
the effect on the expression AST.

## Compatibility and migration impact

Is this MAJOR or MINOR under the versioning policy? What happens to existing
specs? Does `verispec migrate` need to handle it? Which corpus entries must
be added or changed?

## Alternatives rejected

Which other designs were considered, and why they were not chosen. This is
the section future readers thank you for.

## Unresolved questions

Open questions for the maintainers and the community. Be specific.
```

## The decisions

| RFC | Title | Status | Summary |
| --- | --- | --- | --- |
| [RFC-0001](0001-bounded-collections.md) | Bounded collections, records and modules | IMPLEMENTED | The v1 format adds bounded maps, sets and sequences, records, and a module system, all with finite domains so the Z3 encoding stays total. |
| [RFC-0002](0002-refinement-mapping.md) | Refinement mapping between specs | ACCEPTED | Concrete specs may declare `refines:` with state abstraction and action simulation; `verispec refines` checks bounded refinement. |

## Relationship with ADRs

An ADR records a decision. An RFC designs a change to the spec format.

- A decision that changes the spec format needs an RFC first. The ADR then
  records the outcome and links to the RFC.
- A decision that does not touch the format (the language of the core, the
  licensing, the verification strategy) needs only an ADR.

The ADRs live in [`docs/adrs/`](../docs/adrs/README.md). RFC-0001 was
implemented and is referenced by ADR-0002.

## After acceptance

An accepted RFC is a promise, and the promise is kept in the code:

- The implementation follows the document. If it cannot, the document is
  wrong, and the document is amended.
- The conformance corpus (`corpus/`) gains the cases the RFC describes. The
  corpus is the source of truth for what the format supports: if a construct
  is not in the corpus, it is not part of the contract yet.
- The reference documentation (`docs/spec-format.md`) is updated in the same
  change.
- The `spec_version` of the format is bumped according to the policy: MINOR
  for an additive change, MAJOR for a breaking one. The MAJOR bump happens
  with the implementation, not before, so that no two tools disagree about
  what a file means.
- `verispec migrate` is extended when existing specs need to be brought
  forward.
