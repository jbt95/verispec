
def make_brief(spec):
    L=[]
    L.append(f"# Agent brief — {spec.name}\n")
    L.append("> **SPEC IS READ-ONLY. Do not modify the spec to make tests pass.**")
    L.append("> If you believe the spec is wrong, stop and report it to a human. Fix code, not the spec.\n")
    L.append(f"## Context\n{spec.description}\n")
    L.append("## State (you MUST respect these types/bounds)")
    for n,v in spec.vars.items():
        b=f", min={v.min}, max={v.max}" if v.min is not None or v.max is not None else ""
        L.append(f"- `{n}`: {v.type_str}{b}")
    L.append("\n## Actions")
    for a in spec.actions:
        L.append(f"### {a.name}")
        if a.params: L.append("Params: "+", ".join(f"`{k}`: {v.type_str}" for k,v in a.params.items()))
        for r in a.requires: L.append(f"- REQUIRES (precondition): `{r}`")
        for k,e in a.effects.items(): L.append(f"- EFFECT: `{k}` := `{e}`")
    L.append("\n## Invariants — MUST / NEVER rules")
    for inv in spec.invariants: L.append(f"- MUST always hold — **{inv['name']}**: `{inv['expr']}`")
    L.append("""
## Adapter contract (any language)
Implement: `reset()`, `apply(action, **params)`, `snapshot() -> dict` whose keys/values
exactly match the spec vars above. Record traces as JSON list of
`{action, params, state}` (first entry action="init") for `verispec check-trace`.

## Counterexample etiquette
- A counterexample from `verispec check` / `check-trace` is ground truth about a
  spec violation: reproduce it, fix the implementation, re-run until green.
- Never weaken an invariant, precondition, or generated test to silence it.
- Bounded checking says "no violation up to bound K" — never claim "proven for all depths".
""")
    return "\n".join(L)+"\n"
