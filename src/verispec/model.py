
import re, yaml
from .expr import parse_expr, SpecError
class Var:
    def __init__(self,name,raw):
        if isinstance(raw,str): raw={"type":raw}
        self.name=name; self.type_str=raw.get("type","int"); self.min=raw.get("min"); self.max=raw.get("max"); self.raw=raw
        m=re.fullmatch(r"enum\[(.*)\]",self.type_str)
        if m: self.kind="enum"; self.values=[v.strip() for v in m.group(1).split(",") if v.strip()]
        elif self.type_str in ("int","bool","str"): self.kind=self.type_str; self.values=[]
        else: raise SpecError(f"var {name}: unsupported type {self.type_str!r} (scalar int/bool/str/enum[...] only)")
class Action:
    def __init__(self,raw):
        self.name=raw["name"]; self.params={}
        for pn,pr in (raw.get("params") or {}).items():
            if isinstance(pr,str): pr={"type":pr}
            self.params[pn]=Var(pn,pr)
        self.requires=raw.get("requires") or []
        self.effects=raw.get("effects") or {}
class Spec:
    def __init__(self,data,path="<spec>"):
        if not isinstance(data,dict): raise SpecError("spec must be a mapping")
        self.name=data.get("name","Unnamed"); self.description=data.get("description",""); self.path=path
        self.vars={n:Var(n,r) for n,r in (data.get("vars") or {}).items()}
        self.init=data.get("init") or {}
        self.actions=[Action(a) for a in (data.get("actions") or [])]
        self.invariants=[]
        for inv in (data.get("invariants") or []):
            if isinstance(inv,str): self.invariants.append({"name":inv,"expr":inv})
            else: self.invariants.append({"name":inv.get("name","inv"),"expr":inv.get("expr")})
        self.enum_map={}
        for v in list(self.vars.values())+[p for a in self.actions for p in a.params.values()]:
            if v.kind=="enum":
                for i,val in enumerate(v.values): self.enum_map[val]=i
    def action(self,name):
        for a in self.actions:
            if a.name==name: return a
        raise SpecError(f"unknown action {name!r}")
    def validate(self):
        if not self.vars: raise SpecError("spec has no vars")
        known=set(self.vars)
        for a in self.actions: known_a=known|set(a.params)
        # init
        for k,e in self.init.items():
            if k not in self.vars: raise SpecError(f"init assigns unknown var {k}")
            parse_expr(str(e))
        for a in self.actions:
            for e in a.requires: parse_expr(e)
            for k,e in a.effects.items():
                if k not in self.vars: raise SpecError(f"action {a.name} effect assigns unknown var {k}")
                parse_expr(str(e))
        for inv in self.invariants: parse_expr(inv["expr"])
        # bounds sanity
        for v in self.vars.values():
            if v.kind=="int" and v.min is not None and v.max is not None and v.min>v.max: raise SpecError(f"var {v.name} min>max")
        return True
def load_spec(path):
    with open(path) as f: data=yaml.safe_load(f)
    s=Spec(data,str(path)); s.validate(); return s
def initial_state(spec):
    from .expr import eval_python
    st={}
    for name,v in spec.vars.items():
        if name in spec.init: st[name]=eval_python(str(spec.init[name]), dict(st))
        elif v.kind=="bool": st[name]=False
        elif v.kind=="enum": st[name]=v.values[0]
        elif v.kind=="int": st[name]=v.min if v.min is not None else 0
        else: st[name]=""
    return st
def apply_action(spec, state, action_name, params):
    from .expr import eval_python
    a=spec.action(action_name); env={**state, **params}
    for r in a.requires:
        if not eval_python(r, env): raise SpecError(f"precondition failed for {action_name}: {r}")
    nxt=dict(state)
    for k,e in a.effects.items(): nxt[k]=eval_python(str(e), env)
    return nxt
def check_invariants(spec, state):
    from .expr import eval_python
    bad=[]
    for inv in spec.invariants:
        if not eval_python(inv["expr"], dict(state)): bad.append(inv)
    return bad
