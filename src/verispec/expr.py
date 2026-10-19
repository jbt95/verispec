
"""Safe expression language: AST whitelist, same AST -> Python runtime and Z3.
Rejects anything outside the whitelist. No eval() of arbitrary code."""
import ast
class SpecError(Exception): pass

ALLOWED_NODES=(ast.Expression,ast.BoolOp,ast.And,ast.Or,ast.UnaryOp,ast.Not,ast.USub,ast.UAdd,ast.BinOp,ast.Add,ast.Sub,ast.Mult,ast.FloorDiv,ast.Mod,ast.Compare,ast.Eq,ast.NotEq,ast.Lt,ast.LtE,ast.Gt,ast.GtE,ast.In,ast.NotIn,ast.Name,ast.Load,ast.Constant,ast.IfExp,ast.List,ast.Tuple,ast.Call,ast.GeneratorExp,ast.comprehension)
ALLOWED_FUNCS={"len","all","any","min","max","abs"}
def parse_expr(s):
    if not isinstance(s,str): raise SpecError(f"expression must be string, got {type(s).__name__}: {s!r}")
    try: tree=ast.parse(s,mode="eval")
    except SyntaxError as e: raise SpecError(f"invalid expression {s!r}: {e}") from e
    validate_ast(tree,s)
    return tree
def validate_ast(tree,src="?"):
    for node in ast.walk(tree):
        if not isinstance(node,ALLOWED_NODES):
            raise SpecError(f"forbidden syntax {type(node).__name__} in expression {src!r}: only comparisons, and/or/not, + - * // %, in, len/all/any/min/max/abs are allowed")
        if isinstance(node,ast.Call):
            if not isinstance(node.func,ast.Name) or node.func.id not in ALLOWED_FUNCS:
                raise SpecError(f"forbidden call in expression {src!r}: only {sorted(ALLOWED_FUNCS)} allowed")
            if node.keywords: raise SpecError(f"keyword args not allowed in {src!r}")
        if isinstance(node,ast.Constant) and not isinstance(node.value,(int,bool,str,type(None))):
            raise SpecError(f"forbidden constant {node.value!r} in {src!r}")
        if isinstance(node,ast.comprehension):
            if node.is_async: raise SpecError("async comprehension forbidden")
    return tree

def eval_python(src, env):
    tree=parse_expr(src) if isinstance(src,str) else src
    return _eval(tree.body, dict(env))
def _eval(node, env):
    if isinstance(node,ast.Constant): return node.value
    if isinstance(node,ast.Name):
        if node.id in env: return env[node.id]
        if node.id=="True": return True
        if node.id=="False": return False
        raise SpecError(f"unknown variable {node.id!r}")
    if isinstance(node,ast.BoolOp):
        if isinstance(node.op,ast.And):
            r=True
            for v in node.values:
                r=_eval(v,env)
                if not r: return r
            return r
        r=False
        for v in node.values:
            r=_eval(v,env)
            if r: return r
        return r
    if isinstance(node,ast.UnaryOp):
        v=_eval(node.operand,env)
        if isinstance(node.op,ast.Not): return not v
        if isinstance(node.op,ast.USub): return -v
        return +v
    if isinstance(node,ast.BinOp):
        a=_eval(node.left,env); b=_eval(node.right,env)
        if isinstance(node.op,ast.Add): return a+b
        if isinstance(node.op,ast.Sub): return a-b
        if isinstance(node.op,ast.Mult): return a*b
        if isinstance(node.op,ast.FloorDiv): return a//b
        if isinstance(node.op,ast.Mod): return a%b
        raise SpecError(f"bad binop {node.op}")
    if isinstance(node,ast.Compare):
        left=_eval(node.left,env)
        for op,comp in zip(node.ops,node.comparators):
            right=_eval(comp,env)
            if isinstance(op,ast.Eq): ok=left==right
            elif isinstance(op,ast.NotEq): ok=left!=right
            elif isinstance(op,ast.Lt): ok=left<right
            elif isinstance(op,ast.LtE): ok=left<=right
            elif isinstance(op,ast.Gt): ok=left>right
            elif isinstance(op,ast.GtE): ok=left>=right
            elif isinstance(op,ast.In): ok=left in right
            elif isinstance(op,ast.NotIn): ok=left not in right
            else: raise SpecError("bad compare")
            if not ok: return False
            left=right
        return True
    if isinstance(node,ast.IfExp): return _eval(node.body if _eval(node.test,env) else node.orelse, env)
    if isinstance(node,(ast.List,ast.Tuple)): return [_eval(e,env) for e in node.elts]
    if isinstance(node,ast.Call):
        fname=node.func.id
        if fname=="len": return len(_eval(node.args[0],env))
        if fname in ("all","any"):
            vals=_eval_gen(node.args[0],env) if isinstance(node.args[0],ast.GeneratorExp) else _eval(node.args[0],env)
            return all(vals) if fname=="all" else any(vals)
        args=[_eval(a,env) for a in node.args]
        return {"min":min,"max":max,"abs":abs}[fname](*args)
    raise SpecError(f"cannot evaluate {type(node).__name__}")
def _eval_gen(node,env):
    # GeneratorExp: elt for target in iter if conds
    comp=node.generators[0]; out=[]
    for item in _eval(comp.iter,env):
        e2=dict(env)
        if isinstance(comp.target,ast.Name): e2[comp.target.id]=item
        else: raise SpecError("only simple generator target")
        if all(_eval(c,e2) for c in comp.ifs): out.append(_eval(node.elt,e2))
    return out

# ---- Z3 ----
def to_z3(src, z3env, enum_map=None):
    """enum_map: {enum_value_str: int_index}. z3env values are z3 exprs."""
    import z3
    enum_map=enum_map or {}
    tree=parse_expr(src) if isinstance(src,str) else src
    return _z3(tree.body, z3env, enum_map)
def _z3(node, env, enum_map):
    import z3
    if isinstance(node,ast.Constant):
        v=node.value
        if isinstance(v,bool): return z3.BoolVal(v)
        if isinstance(v,int): return z3.IntVal(v)
        if isinstance(v,str):
            if v in enum_map: return z3.IntVal(enum_map[v])
            return z3.StringVal(v)
        raise SpecError(f"bad constant {v!r}")
    if isinstance(node,ast.Name):
        if node.id in env: return env[node.id]
        raise SpecError(f"unknown variable {node.id!r} for Z3")
    if isinstance(node,ast.BoolOp):
        vals=[_z3(v,env,enum_map) for v in node.values]
        return z3.And(*vals) if isinstance(node.op,ast.And) else z3.Or(*vals)
    if isinstance(node,ast.UnaryOp):
        v=_z3(node.operand,env,enum_map)
        if isinstance(node.op,ast.Not): return z3.Not(v)
        if isinstance(node.op,ast.USub): return -v
        return v
    if isinstance(node,ast.BinOp):
        a=_z3(node.left,env,enum_map); b=_z3(node.right,env,enum_map)
        if isinstance(node.op,ast.Add): return a+b
        if isinstance(node.op,ast.Sub): return a-b
        if isinstance(node.op,ast.Mult): return a*b
        if isinstance(node.op,ast.FloorDiv): return a/b
        if isinstance(node.op,ast.Mod): return a%b
    if isinstance(node,ast.Compare):
        left=_z3(node.left,env,enum_map); parts=[]
        for op,comp in zip(node.ops,node.comparators):
            right=_z3(comp,env,enum_map)
            if isinstance(op,ast.Eq): parts.append(left==right)
            elif isinstance(op,ast.NotEq): parts.append(left!=right)
            elif isinstance(op,ast.Lt): parts.append(left<right)
            elif isinstance(op,ast.LtE): parts.append(left<=right)
            elif isinstance(op,ast.Gt): parts.append(left>right)
            elif isinstance(op,ast.GtE): parts.append(left>=right)
            elif isinstance(op,ast.In): parts.append(z3.Or(*[left==_z3(e,env,enum_map) for e in comp.elts]) if isinstance(comp,(ast.List,ast.Tuple)) else (_ for _ in ()).throw(SpecError("'in' in Z3 only over list literal")))
            elif isinstance(op,ast.NotIn): parts.append(z3.And(*[left!=_z3(e,env,enum_map) for e in comp.elts]) if isinstance(comp,(ast.List,ast.Tuple)) else (_ for _ in ()).throw(SpecError("'in' in Z3 only over list literal")))
            left=right
        return z3.And(*parts) if len(parts)>1 else parts[0]
    if isinstance(node,ast.IfExp): return z3.If(_z3(node.test,env,enum_map), _z3(node.body,env,enum_map), _z3(node.orelse,env,enum_map))
    if isinstance(node,ast.Call):
        fname=node.func.id
        if fname=="abs": return z3.If(_z3(node.args[0],env,enum_map)>=0,_z3(node.args[0],env,enum_map),-_z3(node.args[0],env,enum_map))
        if fname in ("all","any") and isinstance(node.args[0],(ast.List,ast.Tuple)):
            vals=[_z3(e,env,enum_map) for e in node.args[0].elts]
            return z3.And(*vals) if fname=="all" else z3.Or(*vals)
        if fname in ("min","max"):
            a=_z3(node.args[0],env,enum_map); b=_z3(node.args[1],env,enum_map)
            return z3.If(a<=b,a,b) if fname=="min" else z3.If(a>=b,a,b)
        raise SpecError(f"call {fname} not supported in Z3 translation (generator/len need concrete collections)")
    if isinstance(node,(ast.List,ast.Tuple)): raise SpecError("list literal only as argument to in/all/any")
    raise SpecError(f"cannot translate {type(node).__name__} to Z3")

# ---- TypeScript transpilation (for gen-tests --lang typescript) ----
def to_ts(src, state_name="state", params_name="params"):
    tree=parse_expr(src) if isinstance(src,str) else src
    return _ts(tree.body, state_name, params_name)
def _ts(node, s, p):
    if isinstance(node,ast.Constant):
        if node.value is True: return "true"
        if node.value is False: return "false"
        if node.value is None: return "null"
        if isinstance(node.value,str): return repr(node.value).replace("'",'"')
        return str(node.value)
    if isinstance(node,ast.Name):
        # caller maps names via combined lookup handled by generated helpers: state first then params
        return f"(({s} as any)[{node.id!r}] ?? ({p} as any)[{node.id!r}])".replace("'",'"')
    if isinstance(node,ast.BoolOp):
        op=" && " if isinstance(node.op,ast.And) else " || "
        return "("+op.join(_ts(v,s,p) for v in node.values)+")"
    if isinstance(node,ast.UnaryOp):
        if isinstance(node.op,ast.Not): return f"(!{_ts(node.operand,s,p)})"
        return f"(-{_ts(node.operand,s,p)})" if isinstance(node.op,ast.USub) else _ts(node.operand,s,p)
    if isinstance(node,ast.BinOp):
        op={ast.Add:"+",ast.Sub:"-",ast.Mult:"*",ast.FloorDiv:"/",ast.Mod:"%"}[type(node.op)]
        a=_ts(node.left,s,p); b=_ts(node.right,s,p)
        return f"Math.floor({a} / {b})" if isinstance(node.op,ast.FloorDiv) else f"({a} {op} {b})"
    if isinstance(node,ast.Compare):
        out=[]; left=_ts(node.left,s,p)
        mp={ast.Eq:"===",ast.NotEq:"!==",ast.Lt:"<",ast.LtE:"<=",ast.Gt:">",ast.GtE:">="}
        for op,comp in zip(node.ops,node.comparators):
            right=_ts(comp,s,p)
            if isinstance(op,(ast.In,ast.NotIn)): out.append(f"{'!' if isinstance(op,ast.NotIn) else ''}{right}.includes({left})")
            else: out.append(f"({left} {mp[type(op)]} {right})")
            left=right
        return " && ".join(out)
    if isinstance(node,(ast.List,ast.Tuple)): return "["+", ".join(_ts(e,s,p) for e in node.elts)+"]"
    if isinstance(node,ast.IfExp): return f"({_ts(node.test,s,p)} ? {_ts(node.body,s,p)} : {_ts(node.orelse,s,p)})"
    if isinstance(node,ast.Call):
        if node.func.id=="len": return f"{_ts(node.args[0],s,p)}.length"
        if node.func.id=="abs": return f"Math.abs({_ts(node.args[0],s,p)})"
        if node.func.id in ("min","max"): return f"Math.{node.func.id}("+", ".join(_ts(a,s,p) for a in node.args)+")"
        if node.func.id in ("all","any"):
            arr=_ts(node.args[0],s,p); return f"{arr}.every((x:boolean)=>x)" if node.func.id=="all" else f"{arr}.some((x:boolean)=>x)"
    raise SpecError(f"cannot transpile {type(node).__name__} to TypeScript")
