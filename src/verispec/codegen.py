"""Code generation: turn spec expressions into target language code.

The spec language is small, so every backend is a direct translation of the
same AST.  Each language gets a tiny runtime with the same helper contract:

    len(x)                 length of a collection or string
    map_get(m, k)          read a key (default when missing)
    map_set(m, k, v)       new map with the key set
    set_add(s, v)          new set with the element added
    set_has(s, v)          element membership
    seq_get(q, i)          element at an index (default when out of range)
    seq_append(q, v)       new sequence
    keys(m) / values(m)    keys / values of a map
    contains_key(m, k)     key membership in a map
    contains(l, v)         element membership in a list

Maps are represented as JSON objects (string keys), sets and sequences as
JSON arrays, records as JSON objects keyed by field name.  That keeps the
generated code, the trace format and the adapter contract in sync.
"""

from __future__ import annotations

import ast
from typing import Any

from .errors import SpecError
from .expr import _const_str, _slice_of, infer_type, parse_expr
from .types import (
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

__all__ = ["to_ts", "to_go", "to_tla"]


# --------------------------------------------------------------------------
# shared analysis helpers
# --------------------------------------------------------------------------


def _domain_literal(collection: TypeSpec, lang: str) -> str:
    """Literal for the finite domain of a collection.

    The domain belongs to the *collection* (the map or the set), not to the
    key or element type: passing the element type here would silently
    produce an empty domain, and every update would be dropped.
    """
    domain = getattr(collection, "domain", None)
    if domain is None:
        return "[]"
    if lang == "ts":
        return "[" + ", ".join(_ts_literal(d) for d in domain) + "]"
    if lang == "go":
        return "[]any{" + ", ".join(_go_literal(d) for d in domain) + "}"
    return "{" + ", ".join(str(d) for d in domain) + "}"


def _ts_literal(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    return '"' + str(v).replace('"', '\\"') + '"'


def _go_literal(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    return '"' + str(v).replace('"', '\\"') + '"'


def _default_literal(t: TypeSpec, lang: str) -> str:
    """Literal for the default value of a type (missing key, bad index)."""
    v = default_value(t)
    if lang == "ts":
        return _ts_literal(v)
    if lang == "go":
        return _go_literal(v)
    return str(v)


def _is_map(t: TypeSpec) -> bool:
    return isinstance(t, MapType)


def _is_set(t: TypeSpec) -> bool:
    return isinstance(t, SetType)


def _is_seq(t: TypeSpec) -> bool:
    return isinstance(t, SeqType)


def _is_record(t: TypeSpec) -> bool:
    return isinstance(t, RecordType)


def _collection_kind(t: TypeSpec) -> str:
    if _is_map(t):
        return "map"
    if _is_set(t):
        return "set"
    if _is_seq(t):
        return "seq"
    if isinstance(t, ListType):
        return "list"
    if isinstance(t, StrType):
        return "str"
    return "scalar"


# --------------------------------------------------------------------------
# TypeScript
# --------------------------------------------------------------------------


def to_ts(src: Any, tenv: dict[str, TypeSpec], var_names: set[str]) -> str:
    """Translate an expression into TypeScript.

    State variables are read from `state`, action parameters from `params`.
    """
    tree = parse_expr(src) if isinstance(src, str) else src
    return _ts(tree.body, tenv, var_names)


def _render_binary(node: ast.BinOp, render, floor_div: str, modulo: str) -> str:
    left, right = render(node.left), render(node.right)
    if isinstance(node.op, ast.FloorDiv):
        return f"{floor_div}({left}, {right})"
    if isinstance(node.op, ast.Mod):
        return f"{modulo}({left}, {right})"
    op = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*"}[type(node.op)]
    return f"({left} {op} {right})"


def _ts(node: ast.AST, tenv: dict[str, TypeSpec], var_names: set[str]) -> str:
    if isinstance(node, ast.Constant):
        v = node.value
        if v is True:
            return "true"
        if v is False:
            return "false"
        if v is None:
            return "null"
        if isinstance(v, str):
            return _ts_literal(v)
        return str(v)
    if isinstance(node, ast.Name):
        base = "state" if node.id in var_names else "params"
        return f"{base}[{_ts_literal(node.id)}]"
    if isinstance(node, ast.BoolOp):
        op = " && " if isinstance(node.op, ast.And) else " || "
        return "(" + op.join(_ts(v, tenv, var_names) for v in node.values) + ")"
    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.Not):
            return f"(!{_ts(node.operand, tenv, var_names)})"
        if isinstance(node.op, ast.USub):
            return f"(-{_ts(node.operand, tenv, var_names)})"
        return _ts(node.operand, tenv, var_names)
    if isinstance(node, ast.BinOp):
        return _render_binary(
            node, lambda child: _ts(child, tenv, var_names), "veriFloorDiv", "veriMod"
        )
    if isinstance(node, ast.Compare):
        out = []
        left = _ts(node.left, tenv, var_names)
        mp = {
            ast.Eq: "===",
            ast.NotEq: "!==",
            ast.Lt: "<",
            ast.LtE: "<=",
            ast.Gt: ">",
            ast.GtE: ">=",
        }
        for op, comp in zip(node.ops, node.comparators):
            right = _ts(comp, tenv, var_names)
            if isinstance(op, (ast.In, ast.NotIn)):
                term = _ts_membership(node.left, comp, tenv, var_names)
                out.append(f"(!{term})" if isinstance(op, ast.NotIn) else term)
            else:
                out.append(f"({left} {mp[type(op)]} {right})")
            left = right
        return " && ".join(out)
    if isinstance(node, (ast.List, ast.Tuple)):
        return "[" + ", ".join(_ts(e, tenv, var_names) for e in node.elts) + "]"
    if isinstance(node, ast.IfExp):
        return (
            f"({_ts(node.test, tenv, var_names)} ? {_ts(node.body, tenv, var_names)} "
            f": {_ts(node.orelse, tenv, var_names)})"
        )
    if isinstance(node, ast.Subscript):
        return _ts_subscript(node, tenv, var_names)
    if isinstance(node, ast.Call):
        return _ts_call(node, tenv, var_names)
    raise SpecError(f"cannot transpile {type(node).__name__} to TypeScript")


def _ts_membership(left: ast.AST, right: ast.AST, tenv, var_names) -> str:
    rt = infer_type(right, tenv)
    r = _ts(right, tenv, var_names)
    left_expr = _ts(left, tenv, var_names)
    if isinstance(rt, MapType):
        return f"veriContainsKey({r}, {left_expr})"
    if isinstance(rt, SetType):
        return f"veriSetHas({r}, {left_expr})"
    if isinstance(rt, ListType):
        return f"veriContains({r}, {left_expr})"
    if isinstance(rt, StrType):
        return f"({r} as string).includes({left_expr} as string)"
    raise SpecError(f"'in' is not supported on {type_name(rt)} in TypeScript")


def _ts_subscript(node: ast.Subscript, tenv, var_names) -> str:
    base_t = infer_type(node.value, tenv)
    base = _ts(node.value, tenv, var_names)
    idx = _ts(_slice_of(node), tenv, var_names)
    if isinstance(base_t, MapType):
        return f"veriMapGet({base}, {idx}, {_default_literal(base_t.value, 'ts')})"
    if isinstance(base_t, SeqType):
        return f"veriSeqGet({base}, {idx}, {_default_literal(base_t.elem, 'ts')})"
    if isinstance(base_t, RecordType):
        field = _const_str(_slice_of(node))
        if field is None:
            raise SpecError("record fields must be read with a constant name")
        return f"({base} as any)[{_ts_literal(field)}]"
    raise SpecError(f"type {type_name(base_t)} cannot be subscripted")


def _ts_call(node: ast.Call, tenv, var_names) -> str:
    fname = node.func.id  # type: ignore[union-attr]
    args = [_ts(a, tenv, var_names) for a in node.args]
    if fname == "len":
        t = infer_type(node.args[0], tenv)
        if isinstance(t, MapType):
            return f"veriMapSize({args[0]})"
        if isinstance(t, (SetType, SeqType, ListType)):
            return f"veriLen({args[0]})"
        if isinstance(t, StrType):
            return f"({args[0]} as string).length"
        raise SpecError(f"len() needs a collection, got {type_name(t)}")
    if fname == "abs":
        return f"Math.abs({args[0]})"
    if fname in ("min", "max"):
        return f"Math.{fname}({', '.join(args)})"
    if fname in ("all", "any"):
        return _ts_quantifier(node, tenv, var_names)
    if fname == "count_values":
        return f"veriCountValues({args[0]}, {args[1]})"
    if fname == "same_keys":
        return f"veriSameKeys({args[0]}, {args[1]})"
    if fname == "map_get":
        mt = infer_type(node.args[0], tenv)
        assert isinstance(mt, MapType)
        return f"veriMapGet({args[0]}, {args[1]}, {_default_literal(mt.value, 'ts')})"
    if fname == "map_set":
        mt = infer_type(node.args[0], tenv)
        assert isinstance(mt, MapType)
        return f"veriMapSet({args[0]}, {args[1]}, {args[2]}, {_domain_literal(mt, 'ts')})"
    if fname == "set_add":
        st = infer_type(node.args[0], tenv)
        assert isinstance(st, SetType)
        return f"veriSetAdd({args[0]}, {args[1]}, {_domain_literal(st, 'ts')})"
    if fname == "set_has":
        return f"veriSetHas({args[0]}, {args[1]})"
    if fname == "seq_get":
        qt = infer_type(node.args[0], tenv)
        assert isinstance(qt, SeqType)
        return f"veriSeqGet({args[0]}, {args[1]}, {_default_literal(qt.elem, 'ts')})"
    if fname == "seq_append":
        return f"veriSeqAppend({args[0]}, {args[1]})"
    if fname == "keys":
        return f"veriKeys({args[0]})"
    if fname == "values":
        mt = infer_type(node.args[0], tenv)
        assert isinstance(mt, MapType)
        return f"veriValues({args[0]}, {_default_literal(mt.value, 'ts')})"
    raise SpecError(f"unknown function {fname!r}")


def _ts_quantifier(node: ast.Call, tenv, var_names) -> str:
    fname = node.func.id  # type: ignore[union-attr]
    gen = node.args[0]
    if not isinstance(gen, ast.GeneratorExp) or len(gen.generators) != 1:
        raise SpecError(f"{fname}() needs a generator over a list")
    comp = gen.generators[0]
    iter_t = infer_type(comp.iter, tenv)
    if not isinstance(iter_t, ListType):
        raise SpecError(f"{fname}() can only iterate over list literals")
    target = comp.target.id  # type: ignore[union-attr]
    inner_tenv = dict(tenv)
    inner_tenv[target] = iter_t.elem
    body = _ts(gen.elt, inner_tenv, var_names)
    items = "[" + ", ".join(_ts(e, tenv, var_names) for e in comp.iter.elts) + "]"
    method = "every" if fname == "all" else "some"
    return f"({items} as any[]).{method}(({target}: any) => ({body}))"


# --------------------------------------------------------------------------
# Go
# --------------------------------------------------------------------------


def to_go(src: Any, tenv: dict[str, TypeSpec], var_names: set[str]) -> str:
    """Translate an expression into Go (state/params are map[string]any)."""
    tree = parse_expr(src) if isinstance(src, str) else src
    return _go(tree.body, tenv, var_names)


def _go_coerce(expr: str, t: TypeSpec) -> str:
    """Go type assertion for a value that is statically typed but held as `any`."""
    if t.kind == "int":
        return f"{expr}.(int)"
    if t.kind == "bool":
        return f"{expr}.(bool)"
    if t.kind in ("str", "enum"):
        return f"{expr}.(string)"
    if t.kind == "map":
        return f"{expr}.(map[string]any)"
    if t.kind in ("set", "seq"):
        return f"{expr}.([]any)"
    if t.kind == "record":
        return f"{expr}.(map[string]any)"
    return expr


def _go(node: ast.AST, tenv: dict[str, TypeSpec], var_names: set[str]) -> str:
    if isinstance(node, ast.Constant):
        v = node.value
        if v is True:
            return "true"
        if v is False:
            return "false"
        if isinstance(v, str):
            return _go_literal(v)
        return str(v)
    if isinstance(node, ast.Name):
        base = "state" if node.id in var_names else "params"
        return _go_coerce(f"{base}[{_go_literal(node.id)}]", tenv[node.id])
    if isinstance(node, ast.BoolOp):
        op = " && " if isinstance(node.op, ast.And) else " || "
        return "(" + op.join(_go(v, tenv, var_names) for v in node.values) + ")"
    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.Not):
            return f"(!{_go(node.operand, tenv, var_names)})"
        if isinstance(node.op, ast.USub):
            return f"(-{_go(node.operand, tenv, var_names)})"
        return _go(node.operand, tenv, var_names)
    if isinstance(node, ast.BinOp):
        return _render_binary(
            node, lambda child: _go(child, tenv, var_names), "veriFloorDiv", "veriMod"
        )
    if isinstance(node, ast.Compare):
        out = []
        left = _go(node.left, tenv, var_names)
        mp = {ast.Eq: "==", ast.NotEq: "!=", ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">="}
        for op, comp in zip(node.ops, node.comparators):
            right = _go(comp, tenv, var_names)
            if isinstance(op, (ast.In, ast.NotIn)):
                term = _go_membership(node.left, comp, tenv, var_names)
                out.append(f"(!{term})" if isinstance(op, ast.NotIn) else term)
            else:
                out.append(f"({left} {mp[type(op)]} {right})")
            left = right
        return " && ".join(out)
    if isinstance(node, (ast.List, ast.Tuple)):
        return "[]any{" + ", ".join(_go(e, tenv, var_names) for e in node.elts) + "}"
    if isinstance(node, ast.IfExp):
        return (
            f"veriIf({_go(node.test, tenv, var_names)}, "
            f"{_go(node.body, tenv, var_names)}, {_go(node.orelse, tenv, var_names)})"
        )
    if isinstance(node, ast.Subscript):
        return _go_subscript(node, tenv, var_names)
    if isinstance(node, ast.Call):
        return _go_call(node, tenv, var_names)
    raise SpecError(f"cannot transpile {type(node).__name__} to Go")


def _go_membership(left: ast.AST, right: ast.AST, tenv, var_names) -> str:
    rt = infer_type(right, tenv)
    r = _go(right, tenv, var_names)
    left_expr = _go(left, tenv, var_names)
    if isinstance(rt, MapType):
        return f"veriContainsKey({r}, {left_expr})"
    if isinstance(rt, SetType):
        return f"veriSetHas({r}, {left_expr})"
    if isinstance(rt, ListType):
        return f"veriContains({r}, {left_expr})"
    if isinstance(rt, StrType):
        return f"strings.Contains({r}, {left_expr})"
    raise SpecError(f"'in' is not supported on {type_name(rt)} in Go")


def _go_subscript(node: ast.Subscript, tenv, var_names) -> str:
    base_t = infer_type(node.value, tenv)
    base = _go(node.value, tenv, var_names)
    idx = _go(_slice_of(node), tenv, var_names)
    if isinstance(base_t, MapType):
        return _go_coerce(
            f"veriMapGet({base}, {idx}, {_default_literal(base_t.value, 'go')})", base_t.value
        )
    if isinstance(base_t, SeqType):
        return _go_coerce(
            f"veriSeqGet({base}, {idx}, {_default_literal(base_t.elem, 'go')})", base_t.elem
        )
    if isinstance(base_t, RecordType):
        field = _const_str(_slice_of(node))
        if field is None:
            raise SpecError("record fields must be read with a constant name")
        ft = dict(base_t.fields)[field]
        return _go_coerce(f"veriRecordGet({base}, {_go_literal(field)})", ft)
    raise SpecError(f"type {type_name(base_t)} cannot be subscripted")


def _go_call(node: ast.Call, tenv, var_names) -> str:
    fname = node.func.id  # type: ignore[union-attr]
    args = [_go(a, tenv, var_names) for a in node.args]
    if fname == "len":
        t = infer_type(node.args[0], tenv)
        if isinstance(t, MapType):
            return f"veriMapSize({args[0]})"
        if isinstance(t, (SetType, SeqType, ListType)):
            return f"veriLen({args[0]})"
        if isinstance(t, StrType):
            return f"len({args[0]})"
        raise SpecError(f"len() needs a collection, got {type_name(t)}")
    if fname == "abs":
        return f"veriAbs({args[0]})"
    if fname == "count_values":
        return f"veriCountValues({args[0]}, {args[1]})"
    if fname == "same_keys":
        return f"veriSameKeys({args[0]}, {args[1]})"
    if fname in ("min", "max"):
        return f"veri{fname.capitalize()}({', '.join(args)})"
    if fname in ("all", "any"):
        return _go_quantifier(node, tenv, var_names)
    if fname == "map_get":
        mt = infer_type(node.args[0], tenv)
        assert isinstance(mt, MapType)
        return _go_coerce(
            f"veriMapGet({args[0]}, {args[1]}, {_default_literal(mt.value, 'go')})", mt.value
        )
    if fname == "map_set":
        mt = infer_type(node.args[0], tenv)
        assert isinstance(mt, MapType)
        return f"veriMapSet({args[0]}, {args[1]}, {args[2]}, {_domain_literal(mt, 'go')})"
    if fname == "set_add":
        st = infer_type(node.args[0], tenv)
        assert isinstance(st, SetType)
        return f"veriSetAdd({args[0]}, {args[1]}, {_domain_literal(st, 'go')})"
    if fname == "set_has":
        return f"veriSetHas({args[0]}, {args[1]})"
    if fname == "seq_get":
        qt = infer_type(node.args[0], tenv)
        assert isinstance(qt, SeqType)
        return _go_coerce(
            f"veriSeqGet({args[0]}, {args[1]}, {_default_literal(qt.elem, 'go')})", qt.elem
        )
    if fname == "seq_append":
        return f"veriSeqAppend({args[0]}, {args[1]})"
    if fname == "keys":
        return f"veriKeys({args[0]})"
    if fname == "values":
        mt = infer_type(node.args[0], tenv)
        assert isinstance(mt, MapType)
        return f"veriValues({args[0]}, {_default_literal(mt.value, 'go')})"
    raise SpecError(f"unknown function {fname!r}")


def _go_quantifier(node: ast.Call, tenv, var_names) -> str:
    fname = node.func.id  # type: ignore[union-attr]
    gen = node.args[0]
    if not isinstance(gen, ast.GeneratorExp) or len(gen.generators) != 1:
        raise SpecError(f"{fname}() needs a generator over a list")
    comp = gen.generators[0]
    iter_t = infer_type(comp.iter, tenv)
    if not isinstance(iter_t, ListType):
        raise SpecError(f"{fname}() can only iterate over list literals")
    target = comp.target.id  # type: ignore[union-attr]
    inner_tenv = dict(tenv)
    inner_tenv[target] = iter_t.elem
    body = _go(gen.elt, inner_tenv, var_names)
    items = "[]any{" + ", ".join(_go(e, tenv, var_names) for e in comp.iter.elts) + "}"
    return f"veri{fname.capitalize()}({items}, func({target} any) bool {{ return {body} }})"


# --------------------------------------------------------------------------
# TLA+
# --------------------------------------------------------------------------


def to_tla(src: Any, tenv: dict[str, TypeSpec]) -> str:
    """Translate an expression into TLA+."""
    tree = parse_expr(src) if isinstance(src, str) else src
    return _tla(tree.body, tenv)


def _tla(node: ast.AST, tenv: dict[str, TypeSpec]) -> str:
    if isinstance(node, ast.Constant):
        v = node.value
        if v is True:
            return "TRUE"
        if v is False:
            return "FALSE"
        if isinstance(v, str):
            return '"' + v.replace('"', '\\"') + '"'
        return str(v)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.BoolOp):
        op = " /\\ " if isinstance(node.op, ast.And) else " \\/ "
        return "(" + op.join(_tla(v, tenv) for v in node.values) + ")"
    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.Not):
            return f"(~{_tla(node.operand, tenv)})"
        if isinstance(node.op, ast.USub):
            return f"(-{_tla(node.operand, tenv)})"
        return _tla(node.operand, tenv)
    if isinstance(node, ast.BinOp):
        a = _tla(node.left, tenv)
        b = _tla(node.right, tenv)
        if isinstance(node.op, ast.Add):
            return f"({a} + {b})"
        if isinstance(node.op, ast.Sub):
            return f"({a} - {b})"
        if isinstance(node.op, ast.Mult):
            return f"({a} * {b})"
        if isinstance(node.op, ast.FloorDiv):
            return f"({a} \\div {b})"
        if isinstance(node.op, ast.Mod):
            return f"({a} % {b})"
    if isinstance(node, ast.Compare):
        parts = []
        left = node.left
        for op, comp in zip(node.ops, node.comparators):
            a = _tla(left, tenv)
            b = _tla(comp, tenv)
            if isinstance(op, ast.Eq):
                parts.append(f"({a} = {b})")
            elif isinstance(op, ast.NotEq):
                parts.append(f"({a} # {b})")
            elif isinstance(op, ast.Lt):
                parts.append(f"({a} < {b})")
            elif isinstance(op, ast.LtE):
                parts.append(f"({a} <= {b})")
            elif isinstance(op, ast.Gt):
                parts.append(f"({a} > {b})")
            elif isinstance(op, ast.GtE):
                parts.append(f"({a} >= {b})")
            elif isinstance(op, (ast.In, ast.NotIn)):
                term = _tla_membership(left, comp, tenv)
                parts.append(f"(~{term})" if isinstance(op, ast.NotIn) else term)
            left = comp
        return " /\\ ".join(parts)
    if isinstance(node, ast.IfExp):
        return (
            f"(IF {_tla(node.test, tenv)} THEN {_tla(node.body, tenv)} "
            f"ELSE {_tla(node.orelse, tenv)})"
        )
    if isinstance(node, ast.Subscript):
        base_t = infer_type(node.value, tenv)
        base = _tla(node.value, tenv)
        idx = _tla(_slice_of(node), tenv)
        if isinstance(base_t, RecordType):
            field = _const_str(_slice_of(node))
            if field is None:
                raise SpecError("record fields must be read with a constant name")
            return f"{base}.{field}"
        if isinstance(base_t, SeqType):
            # TLA+ sequences are indexed from 1
            return f"{base}[{idx} + 1]"
        return f"{base}[{idx}]"
    if isinstance(node, ast.Call):
        return _tla_call(node, tenv)
    raise SpecError(f"cannot transpile {type(node).__name__} to TLA+")


def _tla_membership(left: ast.AST, right: ast.AST, tenv) -> str:
    rt = infer_type(right, tenv)
    r = _tla(right, tenv)
    left_expr = _tla(left, tenv)
    if isinstance(rt, MapType):
        return f"({left_expr} \\in DOMAIN {r})"
    if isinstance(rt, (SetType, ListType)):
        return f"({left_expr} \\in {r})"
    if isinstance(rt, StrType):
        raise SpecError(
            "'in' on a str has no direct TLA+ translation (substring test); "
            "model it with an explicit set of allowed values"
        )
    raise SpecError(f"'in' is not supported on {type_name(rt)} in TLA+")


def _tla_call(node: ast.Call, tenv) -> str:
    fname = node.func.id  # type: ignore[union-attr]
    args = [_tla(a, tenv) for a in node.args]
    if fname == "len":
        t = infer_type(node.args[0], tenv)
        if isinstance(t, (MapType, SetType, ListType)):
            return f"Cardinality({args[0]})"
        if isinstance(t, SeqType):
            return f"Len({args[0]})"
        if isinstance(t, StrType):
            return f"StringLength({args[0]})"
        raise SpecError(f"len() needs a collection, got {type_name(t)}")
    if fname == "abs":
        return f"Abs({args[0]})"
    if fname == "count_values":
        return f"Cardinality({{k \\in DOMAIN {args[0]} : {args[0]}[k] = {args[1]}}})"
    if fname == "same_keys":
        return f"(DOMAIN {args[0]} = DOMAIN {args[1]})"
    if fname in ("min", "max"):
        return f"{fname.capitalize()}({{{', '.join(args)}}})"
    if fname in ("all", "any"):
        gen = node.args[0]
        if not isinstance(gen, ast.GeneratorExp) or len(gen.generators) != 1:
            raise SpecError(f"{fname}() needs a generator over a list")
        comp = gen.generators[0]
        iter_t = infer_type(comp.iter, tenv)
        if not isinstance(iter_t, ListType):
            raise SpecError(f"{fname}() can only iterate over list literals")
        target = comp.target.id  # type: ignore[union-attr]
        inner_tenv = dict(tenv)
        inner_tenv[target] = iter_t.elem
        body = _tla(gen.elt, inner_tenv)
        items = "{" + ", ".join(_tla(e, tenv) for e in comp.iter.elts) + "}"
        op = "\\A" if fname == "all" else "\\E"
        return f"({op} {target} \\in {items} : {body})"
    if fname == "count_values":
        return f"Cardinality({{k \\in DOMAIN {args[0]} : {args[0]}[k] = {args[1]}}})"
    if fname == "same_keys":
        return f"(DOMAIN {args[0]} = DOMAIN {args[1]})"
    if fname == "map_get":
        mt = infer_type(node.args[0], tenv)
        assert isinstance(mt, MapType)
        default = _tla_default(mt.value)
        return f"(IF {args[1]} \\in DOMAIN {args[0]} THEN {args[0]}[{args[1]}] ELSE {default})"
    if fname == "map_set":
        mt = infer_type(node.args[0], tenv)
        assert isinstance(mt, MapType)
        dom = _tla_domain(mt)
        return (
            f"(IF {args[1]} \\in {dom} "
            f"THEN [{args[0]} EXCEPT ![{args[1]}] = {args[2]}] ELSE {args[0]})"
        )
    if fname == "set_add":
        st = infer_type(node.args[0], tenv)
        assert isinstance(st, SetType)
        dom = _tla_domain(st)
        return f"(IF {args[1]} \\in {dom} THEN {args[0]} \\cup {{{args[1]}}} ELSE {args[0]})"
    if fname == "set_has":
        return f"({args[1]} \\in {args[0]})"
    if fname == "seq_get":
        qt = infer_type(node.args[0], tenv)
        assert isinstance(qt, SeqType)
        default = _tla_default(qt.elem)
        return f"(IF {args[1]} < Len({args[0]}) THEN {args[0]}[{args[1]} + 1] ELSE {default})"
    if fname == "seq_append":
        return f"Append({args[0]}, {args[1]})"
    if fname == "keys":
        return f"DOMAIN {args[0]}"
    if fname == "values":
        raise SpecError("values() has no direct TLA+ translation")
    raise SpecError(f"unknown function {fname!r}")


def _tla_domain(collection: TypeSpec) -> str:
    """TLA+ expression for the finite domain of a map or set."""
    domain = getattr(collection, "domain", None)
    if domain is None:
        return "{}"
    elem = collection.key if isinstance(collection, MapType) else collection.elem
    if elem.kind == "int":
        return f"{domain[0]}..{domain[-1]}"
    return "{" + ", ".join(_ts_literal(d) for d in domain) + "}"


def _tla_default(t: TypeSpec) -> str:
    v = default_value(t)
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, str):
        return '"' + v.replace('"', '\\"') + '"'
    return str(v)
