
import json
from .expr import SpecError, eval_python
from .model import initial_state, check_invariants
def check_trace(spec, trace_path):
    with open(trace_path) as f: trace=json.load(f)
    if not isinstance(trace,list) or not trace: return (False, "trace must be a non-empty JSON list")
    init=initial_state(spec)
    for i,step in enumerate(trace):
        if not isinstance(step,dict) or "state" not in step: return (False, f"step {i}: missing 'state'")
        state=step["state"]; action=step.get("action"); params=step.get("params") or {}
        # type/bounds
        for n,v in spec.vars.items():
            if n not in state: return (False, f"step {i}: state missing var {n}")
            val=state[n]
            if v.kind=="int":
                if not isinstance(val,int) or isinstance(val,bool): return (False, f"step {i}: var {n} expected int, got {val!r}")
                if v.min is not None and val<v.min: return (False, f"step {i}: var {n}={val} below min {v.min}")
                if v.max is not None and val>v.max: return (False, f"step {i}: var {n}={val} above max {v.max}")
            elif v.kind=="bool" and not isinstance(val,bool): return (False, f"step {i}: var {n} expected bool")
            elif v.kind=="enum" and val not in v.values: return (False, f"step {i}: var {n}={val!r} not in enum {v.values}")
        bad=check_invariants(spec, state)
        if bad: return (False, f"step {i}: invariant violated: {bad[0]['name']} ({bad[0]['expr']}) in state {state}")
        if i==0:
            if action not in (None,"init","Init"):
                # first step is treated as initial state; if it names an action we still check init equality
                pass
            if state!=init: return (False, f"step 0: initial state mismatch: expected {init}, got {state}")
            continue
        prev=trace[i-1]["state"]
        if action in (None,"init"): return (False, f"step {i}: missing action")
        try: a=spec.action(action)
        except SpecError as e: return (False, f"step {i}: {e}")
        env={**prev, **params}
        for r in a.requires:
            if not eval_python(r, env): return (False, f"step {i}: precondition violated for action {action}: {r} (prev={prev}, params={params})")
        expected=dict(prev)
        for k,e in a.effects.items(): expected[k]=eval_python(str(e), env)
        if expected!=state: return (False, f"step {i}: effect mismatch for action {action}: expected {expected}, got {state} (prev={prev}, params={params})")
    return (True, f"trace OK: {len(trace)} steps conform to spec {spec.name}")
