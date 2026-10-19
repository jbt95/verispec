"""The agent brief.

The brief is what a coding agent reads.  It states the contract in a form
the agent cannot misread, and it says, out loud, that the spec is read-only.
"""

from __future__ import annotations

from .model import Spec
from .types import MapType, RecordType, SeqType, SetType

__all__ = ["make_brief"]


def make_brief(spec: Spec) -> str:
    """Render the agent brief for a spec."""
    L: list[str] = []
    L.append(f"# Agent brief - {spec.name}\n")
    L.append("> **SPEC IS READ-ONLY. Do not modify the spec to make tests pass.**")
    L.append("> If you believe the spec is wrong, stop and report it to a human. Fix code, not the spec.\n")
    L.append(f"## Context\n{spec.description or '(no description)'}\n")
    L.append(f"Spec format version: `{spec.spec_version}`")
    L.append("")
    L.append("## State (you MUST respect these types and bounds)")
    for n, v in spec.vars.items():
        L.append(f"- `{n}`: {v.describe}")
        if isinstance(v.type, MapType):
            keys = ", ".join(str(k) for k in v.type.domain)
            L.append(f"  - keys allowed: {keys} (max {v.type.max_size} entries)")
        if isinstance(v.type, SetType):
            elems = ", ".join(str(k) for k in v.type.domain)
            L.append(f"  - elements allowed: {elems} (max {v.type.max_size} entries)")
        if isinstance(v.type, SeqType):
            L.append(f"  - at most {v.type.max_size} elements")
        if isinstance(v.type, RecordType):
            fields = ", ".join(f"{fn}: {ft.kind}" for fn, ft in v.type.fields)
            L.append(f"  - fields: {fields}")
    L.append("\n## Actions")
    for a in spec.actions:
        L.append(f"### {a.name}")
        if a.params:
            L.append("Params: " + ", ".join(f"`{k}`: {v.describe}" for k, v in a.params.items()))
        for r in a.requires:
            L.append(f"- REQUIRES (precondition): `{r}`")
        for k, e in a.effects.items():
            if isinstance(e, dict):
                for fname, fexpr in e.items():
                    L.append(f"- EFFECT: `{k}.{fname}` := `{fexpr}`")
            else:
                L.append(f"- EFFECT: `{k}` := `{e}`")
    L.append("\n## Invariants - MUST / NEVER rules")
    for inv in spec.invariants:
        L.append(f"- MUST always hold - **{inv['name']}**: `{inv['expr']}`")
    L.append("""
## Adapter contract (any language)
Implement: `reset()`, `apply(action, **params)`, `snapshot() -> dict` whose keys and
values exactly match the spec state above.  Record traces as a JSON list of
`{action, params, state}` (the first entry has `action: "init"`) for
`verispec check-trace`.

## Counterexample etiquette
- A counterexample from `verispec check` / `check-trace` is ground truth about a
  spec violation: reproduce it, fix the implementation, re-run until green.
- Never weaken an invariant, precondition, or generated test to silence it.
- Bounded checking says "no violation up to bound K" - never claim "proven for all depths".
- The spec is owned by a human.  If you need to change it, ask.
""")
    return "\n".join(L) + "\n"
