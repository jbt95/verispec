
import z3
from .expr import to_z3, SpecError
from .model import initial_state

def _z3_var(spec, base, step):
    v=spec.vars[base]
    name=f"{base}_{step}"
    if v.kind=="bool": return z3.Bool(name)
    return z3.Int(name)  # int and enum (as index)

def check(spec, bound=10, invariant_filter=None, timeout_ms=30000):
    # iterative deepening: smallest violating bound first -> shortest counterexample
    for k in range(0, bound+1):
        r=_check_one(spec, bound=k, invariant_filter=invariant_filter, timeout_ms=timeout_ms)
        if r["status"]=="violation":
            r["bound"]=bound; return r
        if r["status"]=="unknown":
            return r
    return {"status":"ok","bound":bound,"message":f"no violation up to bound {bound}"}
def _check_one(spec, bound=10, invariant_filter=None, timeout_ms=30000):
    invs=[i for i in spec.invariants if not invariant_filter or i["name"]==invariant_filter]
    if invariant_filter and not invs: raise SpecError(f"invariant {invariant_filter!r} not found")
    solver=z3.Solver(); solver.set("timeout", timeout_ms)
    # state vars [step][varname]
    states=[]
    for t in range(bound+1):
        states.append({n:_z3_var(spec,n,t) for n in spec.vars})
    # bounds + enum range
    for t,st in enumerate(states):
        for n,v in spec.vars.items():
            # NOTE: var min/max are documented modelling bounds, NOT enforced as
            # solver axioms on states -- otherwise e.g. min:0 would axiomatically
            # hide the very non_negative violations BMC must find. Param bounds
            # (below) are enforced; state reachability is left to actions+invariants.
            if v.kind=="enum": solver.add(st[n]>=0, st[n]<len(v.values))
    # init
    init=initial_state(spec)
    for n,v in spec.vars.items():
        val=init[n]
        if v.kind=="bool": solver.add(states[0][n]==z3.BoolVal(bool(val)))
        elif v.kind=="enum": solver.add(states[0][n]==spec.enum_map[val])
        elif v.kind=="str": pass
        else: solver.add(states[0][n]==int(val))
    nact=len(spec.actions)
    # per step action choice and params
    for t in range(bound):
        choice=z3.Int(f"__action_{t}"); solver.add(choice>=-1, choice<nact)
        cur, nxt = states[t], states[t+1]
        # build per-action condition
        action_conds=[]
        for ai,a in enumerate(spec.actions):
            # param z3 vars
            penv={}
            for pn,pv in a.params.items():
                pz=z3.Bool(f"{a.name}_{pn}_{t}") if pv.kind=="bool" else z3.Int(f"{a.name}_{pn}_{t}")
                penv[pn]=pz
                if pv.kind=="int":
                    if pv.min is not None: solver.add(pz>=pv.min)
                    if pv.max is not None: solver.add(pz<=pv.max)
                if pv.kind=="enum": solver.add(pz>=0, pz<len(pv.values))
            env={**cur, **penv}
            req=z3.And(*[to_z3(r, env, spec.enum_map) for r in a.requires]) if a.requires else z3.BoolVal(True)
            eff=[]
            for vn,v in spec.vars.items():
                if vn in a.effects: eff.append(nxt[vn]==to_z3(str(a.effects[vn]), env, spec.enum_map))
                else: eff.append(nxt[vn]==cur[vn])
            cond=z3.And(req, *eff)
            action_conds.append(cond)
            solver.add(z3.Implies(choice==ai, cond))
        # stutter if no action enabled: choice==-1 implies all requires false and state unchanged
        if action_conds:
            # need requires-only for stutter: recompute quickly via choice==-1 -> state unchanged (sound for BMC search: dead-end paths stutter)
            solver.add(z3.Implies(choice==-1, z3.And(*[nxt[n]==cur[n] for n in spec.vars])))
        else:
            solver.add(z3.And(*[nxt[n]==cur[n] for n in spec.vars]))
    # violation: any invariant false at any step
    viols=[]
    for t,st in enumerate(states):
        for inv in invs:
            viols.append(z3.Not(to_z3(inv["expr"], st, spec.enum_map)))
    solver.add(z3.Or(*viols)) if viols else None
    res=solver.check()
    if res==z3.unsat:
        return {"status":"ok","bound":bound,"message":f"no violation up to bound {bound}"}
    if res!=z3.sat:
        return {"status":"unknown","bound":bound,"message":f"solver returned {res} up to bound {bound}"}
    m=solver.model()
    def decode(t):
        st={}
        for n,v in spec.vars.items():
            val=m.eval(states[t][n], model_completion=True)
            if v.kind=="bool": st[n]=z3.is_true(val)
            elif v.kind=="enum":
                idx=val.as_long(); st[n]=v.values[idx] if 0<=idx<len(v.values) else idx
            else: st[n]=val.as_long()
        return st
    trace=[]
    for t in range(bound+1):
        st=decode(t)
        entry={"step":t,"state":st}
        if t<bound:
            ch=m.eval(z3.Int(f"__action_{t}"), model_completion=True).as_long()
            if 0<=ch<nact:
                a=spec.actions[ch]; entry["action"]=a.name
                params={}
                for pn,pv in a.params.items():
                    pz=z3.Bool(f"{a.name}_{pn}_{t}") if pv.kind=="bool" else z3.Int(f"{a.name}_{pn}_{t}")
                    val=m.eval(pz, model_completion=True)
                    if pv.kind=="bool": params[pn]=z3.is_true(val)
                    elif pv.kind=="enum": params[pn]=pv.values[val.as_long()]
                    else: params[pn]=val.as_long()
                entry["params"]=params
            else: entry["action"]="(stutter)"; entry["params"]={}
        trace.append(entry)
    # trim to first violating step
    from .model import check_invariants
    cut=bound
    for i,e in enumerate(trace):
        if check_invariants(spec, e["state"]): cut=i; break
    trace=trace[:cut+1]
    bad=check_invariants(spec, trace[-1]["state"]) if trace else []
    return {"status":"violation","bound":bound,"trace":trace,"violated":[b["name"] for b in bad]}

def format_result(spec,result):
    if result["status"]=="ok": return result["message"]+"  (bounded claim only, not a proof for all depths)"
    if result["status"]!="violation": return result["message"]
    lines=[f"COUNTEREXAMPLE found within bound {result['bound']}: invariant(s) violated: {', '.join(result.get('violated',[]))}"]
    for e in result["trace"]:
        lines.append(f"  step {e['step']}: state={e['state']}")
        if "action" in e: lines.append(f"    -> action {e['action']} params={e.get('params',{})}")
    return "\n".join(lines)
