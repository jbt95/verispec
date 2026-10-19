"""Safe expression language for VeriSpec.

One AST, several backends:

* a **concrete interpreter** (used by the trace engine, `init`/`effects`
  evaluation and the generated Python tests),
* a **Z3 translation** (used by the bounded model checker),
* **code generators** for TypeScript/Go/Rust/C/Java/TLA+ (see `verispec.codegen`).

The language is an AST whitelist: anything not explicitly allowed is rejected
with a clear error. There is no `eval()` of arbitrary code anywhere.

Expression language v1
----------------------
    comparisons      == != < <= > >= in
    boolean          and / or / not
    arithmetic       + - * // %
    conditional      if ... else
    functions        len all any min max abs
                     map_get map_set set_add set_has seq_get seq_append
                     keys values
    subscript        c[k]   (map key, seq index, record field)
    membership       k in map, v in set, v in list, v in str

Records are read with the same subscript syntax (`r["field"]`), so attribute
access stays forbidden.
"""

from __future__ import annotations

import ast
from typing import Any

from . import types as T
from .errors import SpecError
from .types import (
    BoolType,
    EnumType,
    IntType,
    ListType,
    MapType,
    RecordType,
    SeqType,
    SetType,
    StrType,
    TypeSpec,
    default_value,
    type_name,
)

__all__ = [
    "SpecError",
    "parse_expr",
    "eval_python",
    "to_z3",
    "infer_type",
    "check_types",
    "ALLOWED_FUNCS",
    "Z3Map",
    "Z3Set",
    "Z3Seq",
    "Z3Record",
    "Z3Binding",
]

ALLOWED_NODES = (
    ast.Expression,
    ast.BoolOp,
    ast.And,
    ast.Or,
    ast.UnaryOp,
    ast.Not,
    ast.USub,
    ast.UAdd,
    ast.BinOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.FloorDiv,
    ast.Mod,
    ast.Compare,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.In,
    ast.NotIn,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.IfExp,
    ast.List,
    ast.Tuple,
    ast.Call,
    ast.GeneratorExp,
    ast.comprehension,
    ast.Subscript,
)
ALLOWED_FUNCS = {"len", "all", "any", "min", "max", "abs"}
COLLECTION_FUNCS = {
    "map_get",
    "map_set",
    "set_add",
    "set_has",
    "seq_get",
    "seq_append",
    "keys",
    "values",
}
ALL_FUNCS = ALLOWED_FUNCS | COLLECTION_FUNCS

_FUNCS_WITH_TYPES = {
    "len": 1,
    "abs": 1,
    "all": 1,
    "any": 1,
    "map_get": 2,
    "map_set": 3,
    "set_add": 2,
    "set_has": 2,
    "seq_get": 2,
    "seq_append": 2,
    "keys": 1,
    "values": 1,
}


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------


def parse_expr(s: Any) -> ast.Expression:
    """Parse an expression string into a validated AST."""
    if not isinstance(s, str):
        raise SpecError(f"expression must be string, got {type(s).__name__}: {s!r}")
    try:
        tree = ast.parse(s, mode="eval")
    except SyntaxError as e:
        raise SpecError(f"invalid expression {s!r}: {e}") from e
    validate_ast(tree, s)
    return _normalize(tree)


class _BoolNormalizer(ast.NodeTransformer):
    """Accept YAML-style `true` / `false` as boolean literals."""

    def visit_Name(self, node: ast.Name):  # noqa: N802
        if node.id == "true":
            return ast.copy_location(ast.Constant(value=True), node)
        if node.id == "false":
            return ast.copy_location(ast.Constant(value=False), node)
        return node


def _normalize(tree: ast.Expression) -> ast.Expression:
    return _BoolNormalizer().visit(tree)


def validate_ast(tree: ast.AST, src: str = "?") -> ast.AST:
    """Reject anything outside the whitelist."""
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            raise SpecError(
                f"forbidden syntax {type(node).__name__} in expression {src!r}: "
                "only comparisons, and/or/not, + - * // %, in, subscript, "
                f"len/all/any/min/max/abs and {sorted(COLLECTION_FUNCS)} are allowed"
            )
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in ALL_FUNCS:
                raise SpecError(
                    f"forbidden call in expression {src!r}: only {sorted(ALL_FUNCS)} allowed"
                )
            if node.keywords:
                raise SpecError(f"keyword args not allowed in {src!r}")
            arity = _FUNCS_WITH_TYPES.get(node.func.id)
            if arity is not None and len(node.args) != arity:
                raise SpecError(
                    f"{node.func.id}() takes {arity} argument(s) in {src!r}, "
                    f"got {len(node.args)}"
                )
        if isinstance(node, ast.Constant) and not isinstance(
            node.value, (int, bool, str, type(None))
        ):
            raise SpecError(f"forbidden constant {node.value!r} in {src!r}")
        if isinstance(node, ast.comprehension) and node.is_async:
            raise SpecError("async comprehension forbidden")
    return tree


def _slice_of(node: ast.Subscript) -> ast.expr:
    """Return the subscript expression (Python 3.9+ stores it directly)."""
    sl = node.slice
    if sl.__class__.__name__ == "Index":  # pragma: no cover - legacy ast layout
        return sl.value  # type: ignore[attr-defined]
    return sl  # type: ignore[return-value]


# --------------------------------------------------------------------------
# type inference
# --------------------------------------------------------------------------


def infer_type(node: ast.AST, tenv: dict[str, TypeSpec]) -> TypeSpec:
    """Infer the type of an expression, raising SpecError on a type error."""
    if isinstance(node, ast.Constant):
        v = node.value
        if isinstance(v, bool):
            return BoolType()
        if isinstance(v, int):
            return IntType()
        if isinstance(v, str):
            return StrType()
        raise SpecError(f"unsupported constant {v!r} in expression")
    if isinstance(node, ast.Name):
        if node.id not in tenv:
            raise SpecError(f"unknown variable {node.id!r}")
        return tenv[node.id]
    if isinstance(node, ast.BoolOp):
        for v in node.values:
            infer_type(v, tenv)
        return BoolType()
    if isinstance(node, ast.UnaryOp):
        t = infer_type(node.operand, tenv)
        if isinstance(node.op, ast.Not):
            return BoolType()
        if t.kind != "int":
            raise SpecError(f"unary '-' needs an int, got {type_name(t)}")
        return IntType()
    if isinstance(node, ast.BinOp):
        lt = infer_type(node.left, tenv)
        rt = infer_type(node.right, tenv)
        if lt.kind != "int" or rt.kind != "int":
            raise SpecError(
                f"arithmetic needs ints, got {type_name(lt)} and {type_name(rt)}"
            )
        return IntType()
    if isinstance(node, ast.Compare):
        left = infer_type(node.left, tenv)
        for op, comp in zip(node.ops, node.comparators):
            right = infer_type(comp, tenv)
            if isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
                if left.kind != "int" or right.kind != "int":
                    raise SpecError(
                        f"ordering comparison needs ints, got {type_name(left)} and "
                        f"{type_name(right)}"
                    )
            elif isinstance(op, (ast.In, ast.NotIn)):
                if right.kind not in ("set", "map", "str", "list"):
                    raise SpecError(
                        f"'in' needs a set, map, list or str on the right, got {type_name(right)}"
                    )
            else:  # == / !=
                _check_comparable(left, right)
            left = right
        return BoolType()
    if isinstance(node, ast.IfExp):
        infer_type(node.test, tenv)
        bt = infer_type(node.body, tenv)
        ot = infer_type(node.orelse, tenv)
        if not T.same_type(bt, ot):
            raise SpecError(
                f"if/else branches have different types: {type_name(bt)} vs {type_name(ot)}"
            )
        return bt
    if isinstance(node, (ast.List, ast.Tuple)):
        elems = [infer_type(e, tenv) for e in node.elts]
        if not elems:
            return ListType(IntType())
        for e in elems[1:]:
            if not T.same_type(elems[0], e):
                raise SpecError("list literal elements must all have the same type")
        return ListType(elems[0])
    if isinstance(node, ast.Call):
        return _infer_call(node, tenv)
    if isinstance(node, ast.Subscript):
        base = infer_type(node.value, tenv)
        idx = infer_type(_slice_of(node), tenv)
        if isinstance(base, MapType):
            if not T.same_type(base.key, idx):
                raise SpecError(
                    f"map key type mismatch: map expects {type_name(base.key)}, got {type_name(idx)}"
                )
            return base.value
        if isinstance(base, SeqType):
            if idx.kind != "int":
                raise SpecError(f"sequence index must be an int, got {type_name(idx)}")
            return base.elem
        if isinstance(base, RecordType):
            if idx.kind != "str":
                raise SpecError(f"record field name must be a str, got {type_name(idx)}")
            field = _const_str(_slice_of(node))
            if field is None:
                raise SpecError(
                    "record fields must be read with a constant name, e.g. customer[\"active\"]"
                )
            for name, ft in base.fields:
                if name == field:
                    return ft
            names = [n for n, _ in base.fields]
            raise SpecError(f"unknown record field {field!r}; known fields: {names}")
        raise SpecError(f"type {type_name(base)} cannot be subscripted")
    if isinstance(node, ast.GeneratorExp):
        if len(node.generators) != 1:
            raise SpecError("only single-generator comprehensions are supported")
        comp = node.generators[0]
        it = infer_type(comp.iter, tenv)
        if it.kind != "list":
            raise SpecError(
                f"comprehension can only iterate over lists, got {type_name(it)}"
            )
        if not isinstance(comp.target, ast.Name):
            raise SpecError("only simple generator targets are supported")
        if comp.is_async:
            raise SpecError("async comprehension forbidden")
        return ListType(it.elem)
    raise SpecError(f"cannot type expression node {type(node).__name__}")


def _const_str(node: ast.AST) -> str | None:
    """Return the value of a string constant node, or None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _check_comparable(lt: TypeSpec, rt: TypeSpec) -> None:
    if lt.kind == rt.kind:
        return
    # an enum value may be compared with a string literal
    if {lt.kind, rt.kind} == {"enum", "str"}:
        return
    raise SpecError(f"cannot compare {type_name(lt)} with {type_name(rt)}")


def _arg_compatible(expected: TypeSpec, got: TypeSpec) -> bool:
    """Are the types compatible as a function argument?

    An enum value *is* a string in the concrete world (and in Z3), so an enum
    is accepted where a string is expected and vice versa.
    """
    if T.same_type(expected, got):
        return True
    if {expected.kind, got.kind} == {"enum", "str"}:
        return True
    return False


def _infer_call(node: ast.Call, tenv: dict[str, TypeSpec]) -> TypeSpec:
    fname = node.func.id  # type: ignore[union-attr]
    args = node.args
    if fname == "len":
        t = infer_type(args[0], tenv)
        if t.kind not in ("map", "set", "seq", "str", "list"):
            raise SpecError(f"len() needs a collection or str, got {type_name(t)}")
        return IntType()
    if fname == "abs":
        t = infer_type(args[0], tenv)
        if t.kind != "int":
            raise SpecError(f"abs() needs an int, got {type_name(t)}")
        return IntType()
    if fname in ("min", "max"):
        for a in args:
            t = infer_type(a, tenv)
            if t.kind != "int":
                raise SpecError(f"{fname}() needs ints, got {type_name(t)}")
        return IntType()
    if fname in ("all", "any"):
        arg = args[0]
        if not isinstance(arg, ast.GeneratorExp):
            raise SpecError(f"{fname}() needs a generator over a list")
        t = infer_type(arg, tenv)
        if t.kind != "list":
            raise SpecError(f"{fname}() needs a generator over a list")
        return BoolType()
    if fname == "map_get":
        mt = infer_type(args[0], tenv)
        if not isinstance(mt, MapType):
            raise SpecError(f"map_get() needs a map, got {type_name(mt)}")
        kt = infer_type(args[1], tenv)
        if not _arg_compatible(mt.key, kt):
            raise SpecError(
                f"map_get() key type mismatch: expected {type_name(mt.key)}, got {type_name(kt)}"
            )
        return mt.value
    if fname == "map_set":
        mt = infer_type(args[0], tenv)
        if not isinstance(mt, MapType):
            raise SpecError(f"map_set() needs a map, got {type_name(mt)}")
        kt = infer_type(args[1], tenv)
        vt = infer_type(args[2], tenv)
        if not _arg_compatible(mt.key, kt):
            raise SpecError(
                f"map_set() key type mismatch: expected {type_name(mt.key)}, got {type_name(kt)}"
            )
        if not _arg_compatible(mt.value, vt):
            raise SpecError(
                f"map_set() value type mismatch: expected {type_name(mt.value)}, got {type_name(vt)}"
            )
        return mt
    if fname == "set_add":
        st = infer_type(args[0], tenv)
        if not isinstance(st, SetType):
            raise SpecError(f"set_add() needs a set, got {type_name(st)}")
        et = infer_type(args[1], tenv)
        if not _arg_compatible(st.elem, et):
            raise SpecError(
                f"set_add() element type mismatch: expected {type_name(st.elem)}, got {type_name(et)}"
            )
        return st
    if fname == "set_has":
        st = infer_type(args[0], tenv)
        if not isinstance(st, SetType):
            raise SpecError(f"set_has() needs a set, got {type_name(st)}")
        return BoolType()
    if fname == "seq_get":
        st = infer_type(args[0], tenv)
        if not isinstance(st, SeqType):
            raise SpecError(f"seq_get() needs a seq, got {type_name(st)}")
        it = infer_type(args[1], tenv)
        if it.kind != "int":
            raise SpecError(f"seq_get() index must be an int, got {type_name(it)}")
        return st.elem
    if fname == "seq_append":
        st = infer_type(args[0], tenv)
        if not isinstance(st, SeqType):
            raise SpecError(f"seq_append() needs a seq, got {type_name(st)}")
        et = infer_type(args[1], tenv)
        if not _arg_compatible(st.elem, et):
            raise SpecError(
                f"seq_append() element type mismatch: expected {type_name(st.elem)}, got {type_name(et)}"
            )
        return st
    if fname == "keys":
        mt = infer_type(args[0], tenv)
        if not isinstance(mt, MapType):
            raise SpecError(f"keys() needs a map, got {type_name(mt)}")
        return ListType(mt.key)
    if fname == "values":
        mt = infer_type(args[0], tenv)
        if not isinstance(mt, MapType):
            raise SpecError(f"values() needs a map, got {type_name(mt)}")
        return ListType(mt.value)
    raise SpecError(f"unknown function {fname!r}")


def check_types(node: ast.AST, tenv: dict[str, TypeSpec]) -> TypeSpec:
    """Type check an expression (same as `infer_type`, kept for clarity)."""
    return infer_type(node, tenv)


# --------------------------------------------------------------------------
# concrete interpreter
# --------------------------------------------------------------------------


def eval_python(src: Any, env: dict[str, Any], types: dict[str, TypeSpec] | None = None) -> Any:
    """Evaluate an expression against a concrete environment.

    `types` is required only when a collection is subscripted (the default
    value of a missing key depends on the value type).
    """
    tree = parse_expr(src) if isinstance(src, str) else src
    return _eval(tree.body, dict(env), types or {})


def _eval(node: ast.AST, env: dict[str, Any], types: dict[str, TypeSpec]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        if node.id == "True":
            return True
        if node.id == "False":
            return False
        raise SpecError(f"unknown variable {node.id!r}")
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            r = True
            for v in node.values:
                r = _eval(v, env, types)
                if not r:
                    return r
            return r
        r = False
        for v in node.values:
            r = _eval(v, env, types)
            if r:
                return r
        return r
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, env, types)
        if isinstance(node.op, ast.Not):
            return not v
        if isinstance(node.op, ast.USub):
            return -v
        return +v
    if isinstance(node, ast.BinOp):
        a = _eval(node.left, env, types)
        b = _eval(node.right, env, types)
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Sub):
            return a - b
        if isinstance(node.op, ast.Mult):
            return a * b
        if isinstance(node.op, ast.FloorDiv):
            if b == 0:
                raise SpecError("division by zero")
            return a // b
        if isinstance(node.op, ast.Mod):
            if b == 0:
                raise SpecError("modulo by zero")
            return a % b
        raise SpecError(f"bad binop {node.op}")
    if isinstance(node, ast.Compare):
        left = _eval(node.left, env, types)
        for op, comp in zip(node.ops, node.comparators):
            right = _eval(comp, env, types)
            if isinstance(op, ast.Eq):
                ok = left == right
            elif isinstance(op, ast.NotEq):
                ok = left != right
            elif isinstance(op, ast.Lt):
                ok = left < right
            elif isinstance(op, ast.LtE):
                ok = left <= right
            elif isinstance(op, ast.Gt):
                ok = left > right
            elif isinstance(op, ast.GtE):
                ok = left >= right
            elif isinstance(op, ast.In):
                ok = _membership(left, right)
            elif isinstance(op, ast.NotIn):
                ok = not _membership(left, right)
            else:
                raise SpecError("bad compare")
            if not ok:
                return False
            left = right
        return True
    if isinstance(node, ast.IfExp):
        return _eval(node.body if _eval(node.test, env, types) else node.orelse, env, types)
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval(e, env, types) for e in node.elts]
    if isinstance(node, ast.Subscript):
        base = _eval(node.value, env, types)
        idx = _eval(_slice_of(node), env, types)
        return _subscript(base, idx, node, types)
    if isinstance(node, ast.Call):
        return _eval_call(node, env, types)
    raise SpecError(f"cannot evaluate {type(node).__name__}")


def _membership(left: Any, right: Any) -> bool:
    if isinstance(right, dict):
        return left in right
    if isinstance(right, set):
        return left in right
    if isinstance(right, (list, tuple)):
        return left in right
    if isinstance(right, str):
        return isinstance(left, str) and left in right
    raise SpecError(f"'in' is not supported on {type(right).__name__}")


def _subscript(base: Any, idx: Any, node: ast.Subscript, types: dict[str, TypeSpec]) -> Any:
    base_type = _type_of(node.value, types)
    if isinstance(base, dict) and base_type is not None and isinstance(base_type, MapType):
        from .values import map_get

        return map_get(base, idx, base_type)
    if isinstance(base, list) and base_type is not None and isinstance(base_type, SeqType):
        from .values import seq_get

        return seq_get(base, idx, base_type)
    if isinstance(base, dict) and base_type is not None and isinstance(base_type, RecordType):
        if not isinstance(idx, str):
            raise SpecError(f"record field name must be a str, got {idx!r}")
        for name, ft in base_type.fields:
            if name == idx:
                return base.get(idx, default_value(ft))
        raise SpecError(
            f"unknown record field {idx!r}; known fields: {[n for n, _ in base_type.fields]}"
        )
    if isinstance(base, dict):
        return base.get(idx)
    if isinstance(base, list):
        if isinstance(idx, bool) or not isinstance(idx, int):
            raise SpecError(f"sequence index must be an int, got {idx!r}")
        if 0 <= idx < len(base):
            return base[idx]
        return None
    raise SpecError(f"cannot subscript {type(base).__name__}")


def _type_of(node: ast.AST, types: dict[str, TypeSpec]) -> TypeSpec | None:
    try:
        return infer_type(node, types)
    except SpecError:
        return None


def _eval_call(node: ast.Call, env: dict[str, Any], types: dict[str, TypeSpec]) -> Any:
    fname = node.func.id  # type: ignore[union-attr]
    if fname == "len":
        v = _eval(node.args[0], env, types)
        if isinstance(v, (set, dict, list, tuple, str)):
            return len(v)
        raise SpecError(f"len() is not supported on {type(v).__name__}")
    if fname == "abs":
        return abs(_eval(node.args[0], env, types))
    if fname in ("min", "max"):
        vals = [_eval(a, env, types) for a in node.args]
        return min(vals) if fname == "min" else max(vals)
    if fname in ("all", "any"):
        arg = node.args[0]
        if isinstance(arg, ast.GeneratorExp):
            vals = _eval_gen(arg, env, types)
        else:
            vals = _eval(arg, env, types)
        return all(vals) if fname == "all" else any(vals)
    if fname in ("map_get", "map_set", "set_add", "set_has", "seq_get", "seq_append", "keys", "values"):
        from . import values as V

        if fname in ("map_get", "map_set"):
            m = _eval(node.args[0], env, types)
            mt = _type_of(node.args[0], types)
            if not isinstance(mt, MapType):
                raise SpecError(f"{fname}() needs a map")
            k = _eval(node.args[1], env, types)
            if fname == "map_get":
                return V.map_get(m, k, mt)
            v = _eval(node.args[2], env, types)
            return V.map_set(m, k, v, mt)
        if fname in ("set_add", "set_has"):
            s = _eval(node.args[0], env, types)
            st = _type_of(node.args[0], types)
            if not isinstance(st, SetType):
                raise SpecError(f"{fname}() needs a set")
            v = _eval(node.args[1], env, types)
            if fname == "set_add":
                return V.set_add(s, v, st)
            return V.set_has(s, v, st)
        if fname in ("seq_get", "seq_append"):
            q = _eval(node.args[0], env, types)
            qt = _type_of(node.args[0], types)
            if not isinstance(qt, SeqType):
                raise SpecError(f"{fname}() needs a seq")
            if fname == "seq_get":
                i = _eval(node.args[1], env, types)
                return V.seq_get(q, i, qt)
            v = _eval(node.args[1], env, types)
            return V.seq_append(q, v, qt)
        if fname in ("keys", "values"):
            m = _eval(node.args[0], env, types)
            mt = _type_of(node.args[0], types)
            if not isinstance(mt, MapType):
                raise SpecError(f"{fname}() needs a map")
            if fname == "keys":
                return sorted(m.keys(), key=lambda x: (0, x) if isinstance(x, int) and not isinstance(x, bool) else (1, str(x)))
            return [m[k] for k in sorted(m.keys(), key=lambda x: (0, x) if isinstance(x, int) and not isinstance(x, bool) else (1, str(x)))]
    raise SpecError(f"unknown function {fname!r}")


def _eval_gen(node: ast.GeneratorExp, env: dict[str, Any], types: dict[str, TypeSpec]) -> list:
    comp = node.generators[0]
    out = []
    for item in _eval(comp.iter, env, types):
        e2 = dict(env)
        if isinstance(comp.target, ast.Name):
            e2[comp.target.id] = item
        else:
            raise SpecError("only simple generator targets are supported")
        if all(_eval(c, e2, types) for c in comp.ifs):
            out.append(_eval(node.elt, e2, types))
    return out


# --------------------------------------------------------------------------
# Z3 translation
# --------------------------------------------------------------------------


class Z3Coll:
    """Base class for collection-valued Z3 terms."""


class Z3Map(Z3Coll):
    def __init__(self, values: Any, present: Any, size: Any, type: MapType):
        self.values = values
        self.present = present
        self.size = size
        self.type = type


class Z3Set(Z3Coll):
    def __init__(self, present: Any, size: Any, type: SetType):
        self.present = present
        self.size = size
        self.type = type


class Z3Seq(Z3Coll):
    def __init__(self, items: Any, size: Any, type: SeqType):
        self.items = items
        self.size = size
        self.type = type


class Z3Record(Z3Coll):
    def __init__(self, fields: dict[str, Any], type: RecordType):
        self.fields = fields
        self.type = type


class Z3Binding:
    """A named Z3 value together with its spec type."""

    def __init__(self, value: Any, type: TypeSpec):
        self.value = value
        self.type = type


def z3_sort_for(t: TypeSpec):
    """Z3 sort used to represent values of type `t`.

    Enums are represented as strings restricted to their value set, which keeps
    the Z3 semantics identical to the concrete interpreter (an enum value *is*
    a string).
    """
    import z3

    kind = getattr(t, "kind", None)
    if kind == "int":
        return z3.IntSort()
    if kind == "bool":
        return z3.BoolSort()
    if kind in ("str", "enum"):
        return z3.StringSort()
    raise SpecError(f"no Z3 sort for type {type_name(t)}")


def z3_array_sort(domain_sort, range_sort):
    """Z3 array sort (compatible with both the 4.x and 5.x z3 APIs)."""
    import z3

    if hasattr(z3, "ArraySort"):
        return z3.ArraySort(domain_sort, range_sort)
    return z3.Array(domain_sort, range_sort)  # pragma: no cover - z3 4.x fallback


def z3_default(t: TypeSpec):
    """Z3 term for the default value of type `t`."""
    import z3

    kind = getattr(t, "kind", None)
    if kind == "int":
        return z3.IntVal(default_value(t))
    if kind == "bool":
        return z3.BoolVal(False)
    if kind in ("str", "enum"):
        return z3.StringVal(default_value(t))
    raise SpecError(f"no Z3 default for type {type_name(t)}")


def domain_in_z3(t: TypeSpec, domain: tuple[Any, ...], key):
    """Z3 term: is `key` inside the finite domain?"""
    import z3

    kind = getattr(t, "kind", None)
    if kind == "int":
        return z3.And(key >= domain[0], key <= domain[-1])
    if kind in ("enum", "str"):
        return z3.Or(*[key == z3.StringVal(d) for d in domain])
    raise SpecError(f"no domain encoding for type {type_name(t)}")


def domain_index_z3(t: TypeSpec, domain: tuple[Any, ...], key):
    """Z3 term: position of `key` in the domain (-1 when outside)."""
    import z3

    kind = getattr(t, "kind", None)
    inside = domain_in_z3(t, domain, key)
    if kind == "int":
        return z3.If(inside, key - domain[0], z3.IntVal(-1))
    if kind in ("enum", "str"):
        expr = z3.IntVal(-1)
        for i in reversed(range(len(domain))):
            expr = z3.If(key == z3.StringVal(domain[i]), z3.IntVal(i), expr)
        return expr
    raise SpecError(f"no domain encoding for type {type_name(t)}")


def to_z3(
    src: Any,
    zenv: dict[str, Z3Binding],
    types: dict[str, TypeSpec] | None = None,
    expected: TypeSpec | None = None,
):
    """Translate an expression into Z3.

    Scalar expressions translate to Z3 terms; collection expressions translate
    to `Z3Map` / `Z3Set` / `Z3Seq` / `Z3Record` values.
    """
    tree = parse_expr(src) if isinstance(src, str) else src
    tenv = types if types is not None else {n: b.type for n, b in zenv.items()}
    return _z3(tree.body, zenv, tenv, expected)


def _z3_scalar(v: Any, what: str = "value"):

    if isinstance(v, Z3Coll):
        raise SpecError(f"a collection cannot be used as {what}")
    return v


def _z3(node: ast.AST, zenv: dict[str, Z3Binding], tenv: dict[str, TypeSpec], expected: TypeSpec | None = None):
    import z3

    if isinstance(node, ast.Constant):
        v = node.value
        if isinstance(v, bool):
            return z3.BoolVal(v)
        if isinstance(v, int):
            return z3.IntVal(v)
        if isinstance(v, str):
            if isinstance(expected, EnumType):
                # enums are strings in Z3 as well, so that `enum == "gold"` and
                # `str_var == enum_var` behave exactly like the interpreter.
                return z3.StringVal(v)
            return z3.StringVal(v)
        raise SpecError(f"bad constant {v!r}")
    if isinstance(node, ast.Name):
        if node.id not in zenv:
            raise SpecError(f"unknown variable {node.id!r} for Z3")
        return zenv[node.id].value
    if isinstance(node, ast.BoolOp):
        vals = [_z3(v, zenv, tenv) for v in node.values]
        return z3.And(*vals) if isinstance(node.op, ast.And) else z3.Or(*vals)
    if isinstance(node, ast.UnaryOp):
        v = _z3(node.operand, zenv, tenv)
        if isinstance(node.op, ast.Not):
            return z3.Not(v)
        if isinstance(node.op, ast.USub):
            return -v
        return v
    if isinstance(node, ast.BinOp):
        a = _z3_scalar(_z3(node.left, zenv, tenv), "left operand")
        b = _z3_scalar(_z3(node.right, zenv, tenv), "right operand")
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Sub):
            return a - b
        if isinstance(node.op, ast.Mult):
            return a * b
        if isinstance(node.op, ast.FloorDiv):
            return a / b
        if isinstance(node.op, ast.Mod):
            return a % b
        raise SpecError(f"bad binop {node.op}")
    if isinstance(node, ast.Compare):
        left_node = node.left
        parts = []
        for op, comp in zip(node.ops, node.comparators):
            right_type = infer_type(comp, tenv)
            if isinstance(op, (ast.Eq, ast.NotEq)):
                left = _z3(left_node, zenv, tenv)
                right = _z3(comp, zenv, tenv)
                parts.append(left == right if isinstance(op, ast.Eq) else left != right)
            elif isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
                left = _z3_scalar(_z3(left_node, zenv, tenv))
                right = _z3_scalar(_z3(comp, zenv, tenv))
                parts.append(
                    {
                        ast.Lt: left < right,
                        ast.LtE: left <= right,
                        ast.Gt: left > right,
                        ast.GtE: left >= right,
                    }[type(op)]
                )
            elif isinstance(op, (ast.In, ast.NotIn)):
                term = _z3_in(left_node, comp, zenv, tenv, right_type)
                parts.append(term if isinstance(op, ast.In) else z3.Not(term))
            else:
                raise SpecError("bad compare")
            left_node = comp
        return z3.And(*parts) if len(parts) > 1 else parts[0]
    if isinstance(node, ast.IfExp):
        return z3.If(
            _z3(node.test, zenv, tenv),
            _z3(node.body, zenv, tenv, expected),
            _z3(node.orelse, zenv, tenv, expected),
        )
    if isinstance(node, ast.Subscript):
        return _z3_subscript(node, zenv, tenv)
    if isinstance(node, ast.Call):
        return _z3_call(node, zenv, tenv)
    if isinstance(node, (ast.List, ast.Tuple)):
        raise SpecError("list literal only as argument to in/all/any")
    raise SpecError(f"cannot translate {type(node).__name__} to Z3")


def _z3_in(left_node: ast.AST, right_node: ast.AST, zenv, tenv, right_type: TypeSpec):
    import z3

    if isinstance(right_type, (SetType, MapType)):
        left = _z3_scalar(_z3(left_node, zenv, tenv), "membership key")
        if isinstance(right_type, SetType):
            coll = _z3(right_node, zenv, tenv)
            assert isinstance(coll, Z3Set)
            inside = domain_in_z3(right_type.elem, right_type.domain, left)
            idx = domain_index_z3(right_type.elem, right_type.domain, left)
            return z3.And(inside, z3.Select(coll.present, idx))
        coll = _z3(right_node, zenv, tenv)
        assert isinstance(coll, Z3Map)
        inside = domain_in_z3(right_type.key, right_type.domain, left)
        idx = domain_index_z3(right_type.key, right_type.domain, left)
        return z3.And(inside, z3.Select(coll.present, idx))
    if isinstance(right_type, StrType):
        left = _z3_scalar(_z3(left_node, zenv, tenv), "membership needle")
        right = _z3_scalar(_z3(right_node, zenv, tenv), "membership haystack")
        if left.sort().kind() != z3.Z3_STRING_SORT:
            raise SpecError("'in' on a str needs a str on the left")
        return z3.Contains(right, left)
    if isinstance(right_type, ListType):
        raise SpecError(
            "keys()/values() are not supported in the Z3 translation "
            "(they have no finite symbolic domain)"
        )
    # list literal
    right = _z3(right_node, zenv, tenv)
    left = _z3_scalar(_z3(left_node, zenv, tenv), "membership key")
    if not isinstance(right_node, (ast.List, ast.Tuple)):
        raise SpecError("'in' in Z3 only over list literals, sets and maps")
    return z3.Or(*[left == _z3(e, zenv, tenv) for e in right_node.elts])


def _z3_subscript(node: ast.Subscript, zenv, tenv):
    import z3

    base_type = infer_type(node.value, tenv)
    idx_node = _slice_of(node)
    base = _z3(node.value, zenv, tenv)
    if isinstance(base_type, MapType):
        assert isinstance(base, Z3Map)
        key = _z3_scalar(_z3(idx_node, zenv, tenv), "map key")
        inside = domain_in_z3(base_type.key, base_type.domain, key)
        idx = domain_index_z3(base_type.key, base_type.domain, key)
        value = z3.Select(base.values, idx)
        present = z3.Select(base.present, idx)
        return z3.If(z3.And(inside, present), value, z3_default(base_type.value))
    if isinstance(base_type, SeqType):
        assert isinstance(base, Z3Seq)
        i = _z3_scalar(_z3(idx_node, zenv, tenv), "sequence index")
        return z3.If(
            z3.And(i >= 0, i < base.size), z3.Select(base.items, i), z3_default(base_type.elem)
        )
    if isinstance(base_type, RecordType):
        assert isinstance(base, Z3Record)
        field = _const_str(idx_node)
        if field is None:
            raise SpecError(
                "record fields must be read with a constant name, e.g. customer[\"active\"]"
            )
        for name, ft in base_type.fields:
            if name == field:
                return base.fields[name]
        raise SpecError(
            f"unknown record field {field!r}; known fields: {[n for n, _ in base_type.fields]}"
        )
    raise SpecError(f"type {type_name(base_type)} cannot be subscripted")


def _z3_call(node: ast.Call, zenv, tenv):
    import z3

    fname = node.func.id  # type: ignore[union-attr]
    if fname == "len":
        t = infer_type(node.args[0], tenv)
        arg = _z3(node.args[0], zenv, tenv)
        if isinstance(arg, (Z3Map, Z3Set, Z3Seq)):
            return arg.size
        if isinstance(t, StrType):
            return z3.Length(arg)
        raise SpecError("len() needs a collection or str")
    if fname == "abs":
        v = _z3_scalar(_z3(node.args[0], zenv, tenv))
        return z3.If(v >= 0, v, -v)
    if fname in ("min", "max"):
        vals = [_z3_scalar(_z3(a, zenv, tenv)) for a in node.args]
        expr = vals[0]
        for v in vals[1:]:
            expr = z3.If(expr <= v, expr, v) if fname == "min" else z3.If(expr >= v, expr, v)
        return expr
    if fname in ("all", "any"):
        arg = node.args[0]
        if not isinstance(arg, ast.GeneratorExp):
            raise SpecError(f"{fname}() needs a generator over a list")
        if not isinstance(arg.generators[0].iter, (ast.List, ast.Tuple)):
            raise SpecError(
                f"{fname}() over keys()/values() is not supported in the Z3 translation"
            )
        vals = [_z3(e, zenv, tenv) for e in arg.generators[0].iter.elts]
        return z3.And(*vals) if fname == "all" else z3.Or(*vals)
    if fname == "map_get":
        mt = infer_type(node.args[0], tenv)
        assert isinstance(mt, MapType)
        coll = _z3(node.args[0], zenv, tenv)
        assert isinstance(coll, Z3Map)
        key = _z3_scalar(_z3(node.args[1], zenv, tenv), "map key")
        inside = domain_in_z3(mt.key, mt.domain, key)
        idx = domain_index_z3(mt.key, mt.domain, key)
        return z3.If(
            z3.And(inside, z3.Select(coll.present, idx)),
            z3.Select(coll.values, idx),
            z3_default(mt.value),
        )
    if fname == "map_set":
        mt = infer_type(node.args[0], tenv)
        assert isinstance(mt, MapType)
        coll = _z3(node.args[0], zenv, tenv)
        assert isinstance(coll, Z3Map)
        key = _z3_scalar(_z3(node.args[1], zenv, tenv), "map key")
        value = _z3_scalar(_z3(node.args[2], zenv, tenv), "map value")
        inside = domain_in_z3(mt.key, mt.domain, key)
        idx = domain_index_z3(mt.key, mt.domain, key)
        return Z3Map(
            z3.If(inside, z3.Store(coll.values, idx, value), coll.values),
            z3.If(inside, z3.Store(coll.present, idx, z3.BoolVal(True)), coll.present),
            z3.If(
                inside,
                z3.If(z3.Select(coll.present, idx), coll.size, coll.size + 1),
                coll.size,
            ),
            mt,
        )
    if fname == "set_add":
        st = infer_type(node.args[0], tenv)
        assert isinstance(st, SetType)
        coll = _z3(node.args[0], zenv, tenv)
        assert isinstance(coll, Z3Set)
        value = _z3_scalar(_z3(node.args[1], zenv, tenv), "set element")
        inside = domain_in_z3(st.elem, st.domain, value)
        idx = domain_index_z3(st.elem, st.domain, value)
        return Z3Set(
            z3.If(inside, z3.Store(coll.present, idx, z3.BoolVal(True)), coll.present),
            z3.If(
                inside,
                z3.If(z3.Select(coll.present, idx), coll.size, coll.size + 1),
                coll.size,
            ),
            st,
        )
    if fname == "set_has":
        st = infer_type(node.args[0], tenv)
        assert isinstance(st, SetType)
        coll = _z3(node.args[0], zenv, tenv)
        assert isinstance(coll, Z3Set)
        value = _z3_scalar(_z3(node.args[1], zenv, tenv), "set element")
        inside = domain_in_z3(st.elem, st.domain, value)
        idx = domain_index_z3(st.elem, st.domain, value)
        return z3.And(inside, z3.Select(coll.present, idx))
    if fname == "seq_get":
        qt = infer_type(node.args[0], tenv)
        assert isinstance(qt, SeqType)
        coll = _z3(node.args[0], zenv, tenv)
        assert isinstance(coll, Z3Seq)
        i = _z3_scalar(_z3(node.args[1], zenv, tenv), "sequence index")
        return z3.If(
            z3.And(i >= 0, i < coll.size), z3.Select(coll.items, i), z3_default(qt.elem)
        )
    if fname == "seq_append":
        qt = infer_type(node.args[0], tenv)
        assert isinstance(qt, SeqType)
        coll = _z3(node.args[0], zenv, tenv)
        assert isinstance(coll, Z3Seq)
        value = _z3_scalar(_z3(node.args[1], zenv, tenv), "sequence element")
        return Z3Seq(z3.Store(coll.items, coll.size, value), coll.size + 1, qt)
    if fname in ("keys", "values"):
        raise SpecError(
            "keys()/values() are not supported in the Z3 translation "
            "(they have no finite symbolic domain)"
        )
    raise SpecError(f"call {fname!r} not supported in Z3 translation")
