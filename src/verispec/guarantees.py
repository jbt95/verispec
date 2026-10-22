"""The guarantee vocabulary, and the one place that decides what may be claimed.

Every verification result carries a label that says exactly what was
established.  This module is the *only* place those labels are defined and
the *only* place they are assembled, so that a command cannot claim more
than it checked: a label is derived from the evidence that actually exists,
never written by hand at the call site.

The vocabulary answers two questions for every label:

* what was established (the text), and
* what it is about - the *contract*, a *recorded trace*, a *digest* - because
  "bounded-proved(k)" says nothing about the implementation, and a reader
  who does not know that will read it as a statement about the code.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

__all__ = [
    "PropertyEvidence",
    "GUARANTEES",
    "GUARANTEE_SCOPES",
    "GUARANTEE_BOUNDED",
    "GUARANTEE_PROVED",
    "GUARANTEE_LIVENESS",
    "GUARANTEE_CONFORMANCE",
    "GUARANTEE_PENDING",
    "GUARANTEE_VIOLATION",
    "GUARANTEE_CONCURRENT",
    "GUARANTEE_UNKNOWN",
    "GUARANTEE_SIGNATURE",
    "combine",
    "EVIDENCE_LEVELS",
    "scope_of",
    "scope_note",
    "note_for",
    "describe",
    "evidence",
    "is_at_least",
]

#: The label of a bounded safety result.
GUARANTEE_BOUNDED = "bounded-proved(k)"
#: The label of an unbounded safety result.
GUARANTEE_PROVED = "proved"
#: The label of a bounded liveness result.
GUARANTEE_LIVENESS = "bounded-liveness-proved(k)"
#: The label of a trace that conformed.
GUARANTEE_CONFORMANCE = "conformance-tested"
#: The label of a trace that conformed but has an unsatisfied obligation.
GUARANTEE_PENDING = "pending-obligation"
#: The label of a concrete counterexample.
GUARANTEE_VIOLATION = "violation-found"
#: The label of an interleaving exploration.
GUARANTEE_CONCURRENT = "concurrent-explored(k)"
#: The label of an inconclusive result.
GUARANTEE_UNKNOWN = "unknown"
#: The label of a verified digest.
GUARANTEE_SIGNATURE = "signature-verified"

#: Every label, and exactly what it establishes.
GUARANTEES: dict[str, str] = {
    GUARANTEE_PROVED: (
        "Safety invariants were proved for all execution depths (unbounded), "
        "typically via k-induction. Temporal liveness is not included."
    ),
    GUARANTEE_LIVENESS: (
        "No liveness counterexample (lasso) up to the bound. "
        "This is a bounded claim, not a proof for all depths."
    ),
    GUARANTEE_PENDING: (
        "The trace conformed but a temporal property is not yet satisfied at trace end."
    ),
    GUARANTEE_BOUNDED: (
        "No invariant was violated in any execution up to the bound. "
        "This is a bounded claim, not a proof for all depths."
    ),
    GUARANTEE_CONFORMANCE: (
        "A recorded trace of the real implementation was replayed against the spec "
        "and every step conformed."
    ),
    GUARANTEE_SIGNATURE: ("The spec matches the digest that a human approved."),
    GUARANTEE_VIOLATION: "A concrete counterexample exists. The contract is broken.",
    GUARANTEE_CONCURRENT: (
        "No invariant was violated in any explored interleaving up to the bound. "
        "This is not a proof under all schedules."
    ),
    GUARANTEE_UNKNOWN: "The checker could not decide. Treat the result as inconclusive.",
}

#: What each label is *about*.  The distinction matters: a label about the
#: contract says nothing about the implementation.
GUARANTEE_SCOPES: dict[str, str] = {
    GUARANTEE_PROVED: "contract",
    GUARANTEE_BOUNDED: "contract",
    GUARANTEE_LIVENESS: "contract",
    GUARANTEE_CONCURRENT: "contract",
    GUARANTEE_CONFORMANCE: "implementation",
    GUARANTEE_PENDING: "implementation",
    GUARANTEE_VIOLATION: "contract",
    GUARANTEE_UNKNOWN: "contract",
    GUARANTEE_SIGNATURE: "digest",
}

#: The order in which the guarantees are earned, weakest first.  The policy
#: uses it to say "at least this good".
_ORDER: dict[str, int] = {
    GUARANTEE_UNKNOWN: 0,
    GUARANTEE_BOUNDED: 1,
    GUARANTEE_LIVENESS: 2,
    GUARANTEE_PROVED: 3,
    GUARANTEE_CONFORMANCE: 4,
}


#: The evidence levels a policy can require.  These are *requirements*,
#: not labels: "present" says "there is a spec", "signed-digest" says "the
#: spec matches the digest a human approved", and the last two say what
#: the checker must have established.
EVIDENCE_LEVELS: dict[str, str] = {
    "present": "A spec exists and is well formed.",
    "signed-digest": "The spec matches the digest that a human approved.",
    "cryptographic": "The digest is signed with a cryptographic key.",
    "bounded-proved": "The safety invariants hold in every execution up to the bound.",
    "proved": "The safety invariants hold in every execution, at every depth.",
    "bounded-liveness-proved": (
        "No liveness counterexample (lasso) up to the bound: the temporal "
        "obligations of the contract hold up to that depth."
    ),
    "conformance-tested": (
        "A recorded trace of the real implementation was replayed against the "
        "contract and every step conformed."
    ),
    "tested": "Property-based tests were generated and passed.",
    "observed": "Runtime observation recorded conformance.",
    "unchecked": "No evidence has been collected for this property.",
    "violated": "A counterexample or violation was found.",
    "concurrent-explored": "Interleavings were explored up to the bound.",
}


def combine(parts: Iterable[str | None]) -> str:
    """Assemble the label of a result from the checks that actually ran.

    This is the only place a label is put together.  A command that runs a
    safety check and a liveness check calls this with the two labels, and
    the result says both - or neither.
    """
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        if not part:
            continue
        label = str(part)
        # "bounded-proved(8)" and "bounded-proved(k)" are the same kind of
        # guarantee, so only one of them is kept - but the one that is kept
        # is the one that carries the bound.
        base = _base_label(label)
        if base in seen:
            continue
        seen.add(base)
        out.append(label)
    if not out:
        return GUARANTEE_UNKNOWN
    if len(out) == 1:
        return out[0]
    return "; ".join(out)


def _base_label(label: str) -> str:
    """The label without its bound, so that two of the same kind combine."""
    return label.split("(", 1)[0] + ("(k)" if "(" in label else "")


def scope_of(label: str) -> str:
    """What a label is about: the contract, the implementation, or a digest."""
    return GUARANTEE_SCOPES.get(_base_label(label), "contract")


def describe(label: str) -> str:
    """What a label establishes, in one sentence."""
    base = _base_label(label)
    text = GUARANTEES.get(base)
    if text is None:
        return f"Unknown guarantee: {label}."
    scope = scope_of(label)
    if scope == "contract":
        return text + " It is a statement about the contract, not about the implementation."
    if scope == "implementation":
        return text
    return text


def note_for(label: str) -> str:
    """What a label covers, in one sentence, part by part.

    A label can be several guarantees at once - "proved; bounded-liveness-
    proved(k)" - and the note has to say what each part covers, or it
    contradicts the label it explains.
    """
    parts: list[str] = []
    for part in label.split(";"):
        part = part.strip()
        if not part:
            continue
        base = _base_label(part)
        if base == GUARANTEE_PROVED:
            parts.append("the safety invariants are proved for all depths")
        elif base == GUARANTEE_LIVENESS:
            parts.append("the liveness claim is bounded")
        elif base == GUARANTEE_CONCURRENT:
            parts.append("the interleavings are explored up to the bound")
        elif base == GUARANTEE_CONFORMANCE:
            parts.append("the trace conformed")
        elif base == GUARANTEE_PENDING:
            parts.append("the trace conformed; an obligation is still pending")
        else:
            parts.append("bounded claim only, not a proof for all depths")
    return "; ".join(parts) or "bounded claim only, not a proof for all depths"


def scope_note(label: str) -> str:
    """One sentence saying what a label is about.

    "bounded-proved(k)" says nothing about the implementation, and a reader
    who does not know that will read it as a statement about the code.  The
    note is what stops that.
    """
    scope = scope_of(label)
    if scope == "contract":
        return "a statement about the contract, not about the implementation"
    if scope == "implementation":
        return "a statement about this recorded trace, not about every input"
    return "a statement about the approved digest"


def is_at_least(label: str, minimum: str) -> bool:
    """Whether a result is at least as strong as a required level."""
    return _ORDER.get(_base_label(label), 0) >= _ORDER.get(_base_label(minimum), 0)



@dataclass
class PropertyEvidence:
    """Per-property evidence record (RFC-0008)."""

    property: str
    level: str
    scope: str = "contract"
    backend: str = "none"
    artifact: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "property": self.property,
            "level": self.level,
            "scope": self.scope,
            "backend": self.backend,
            "artifact": self.artifact,
        }



def evidence(result: dict[str, Any]) -> str:
    """The label of a result dict, derived from the checks it actually ran."""
    if result.get("status") == "violation":
        return GUARANTEE_VIOLATION
    if result.get("status") == "unknown":
        return GUARANTEE_UNKNOWN
    parts: list[str | None] = [result.get("guarantee")]
    liveness = result.get("liveness")
    if isinstance(liveness, dict):
        parts.append(liveness.get("guarantee"))
    return combine(parts)
