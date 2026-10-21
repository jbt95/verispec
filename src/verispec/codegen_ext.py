"""Code generation for the Rust and C backends.

The spec language is small, so every backend is a direct translation of the
same AST.  This module adds two backends on top of `verispec.codegen`:

* **Rust**  - `to_rust(src, tenv, var_names)`.  The state and the action
  parameters are `serde_json::Value` objects, so the generated code works with
  any spec type (scalars, maps, sets, sequences and records).  The runtime
  helpers (`veri_len`, `veri_map_get`, ...) live in the `verispec-adapter`
  crate.
* **C**     - `to_c(src, tenv, var_names)`.  C supports **scalar state only**
  (`int`, `bool`, `str`, `enum[...]`); a spec that uses a map, a set, a
  sequence or a record is rejected with a clear error instead of being
  silently mistranslated.

Both backends share the same runtime vocabulary, so a reader who knows one
knows the other:

    len(x)                 length of a collection or a string
    map_get(m, k)          read a key (the value type default when missing)
    map_set(m, k, v)       a new map with the key set
    set_add(s, v)          a new set with the element added
    set_has(s, v)          element membership
    seq_get(q, i)          element at an index (the element default when out
                           of range)
    seq_append(q, v)       a new sequence
    keys(m) / values(m)    keys / values of a map
    contains_key(m, k)     key membership in a map
    contains(l, v)         element membership in a list

Maps are JSON objects (string keys), sets and sequences are JSON arrays and
records are JSON objects keyed by field name.  That keeps the generated code,
the trace format and the adapter contract in sync.
"""

from __future__ import annotations

import ast
import copy
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

__all__ = ["to_rust", "to_c"]


# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------


def _body(src: Any) -> ast.AST:
    """The expression node of a parsed expression."""
    tree = parse_expr(src) if isinstance(src, str) else src
    if isinstance(tree, ast.Expression):
        return tree.body
    return tree


def _is_scalar_kind(t: TypeSpec) -> bool:
    return getattr(t, "kind", None) in ("int", "bool", "str", "enum")


def _rust_str(value: str) -> str:
    """A `&str` literal."""
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


def _rust_string(value: str) -> str:
    """A `String` literal."""
    return f"{_rust_str(value)}.to_string()"


def _c_str_literal(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    return f'"{escaped}"'


class _Substituter(ast.NodeTransformer):
    """Replace every free occurrence of a name with a copy of an expression.

    Used to expand `all(...)` / `any(...)` over a list literal: the predicate
    is duplicated once per element, with the loop variable replaced by the
    element expression.  Nested generators keep their own scope.
    """

    def __init__(self, name: str, replacement: ast.AST) -> None:
        self.name = name
        self.replacement = replacement
        self.shadowed: set[str] = set()

    def visit_Name(self, node: ast.Name) -> ast.AST:  # noqa: N802
        if node.id == self.name and node.id not in self.shadowed:
            return copy.deepcopy(self.replacement)
        return node

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> ast.AST:  # noqa: N802
        node.iter = self.visit(node.iter)
        target = node.generators[0].target
        name = target.id if isinstance(target, ast.Name) else None
        if name is not None:
            self.shadowed.add(name)
        node.elt = self.visit(node.elt)
        if name is not None:
            self.shadowed.discard(name)
        return node


def _substitute(node: ast.AST, name: str, replacement: ast.AST) -> ast.AST:
    """A copy of `node` with every free `name` replaced by `replacement`."""
    return _Substituter(name, replacement).visit(copy.deepcopy(node))


def _quantifier_parts(node: ast.Call, tenv: dict[str, TypeSpec]) -> tuple[str, list[list[ast.AST]]]:
    """Split `all(f(x) for x in items)` into the per-element bodies.

    Returns the function name and, for every element of the list literal, the
    list of expressions that must hold (the predicate plus the `if` guards).
    An element whose guard is false does not contribute.
    """
    fname = node.func.id  # type: ignore[union-attr]
    gen = node.args[0]
    if not isinstance(gen, ast.GeneratorExp) or len(gen.generators) != 1:
        raise SpecError(f"{fname}() needs a generator over a list")
    comp = gen.generators[0]
    if not isinstance(comp.iter, (ast.List, ast.Tuple)):
        raise SpecError(f"{fname}() can only iterate over list literals")
    if not isinstance(comp.target, ast.Name):
        raise SpecError("only simple generator targets are supported")
    target = comp.target.id
    per_element: list[list[ast.AST]] = []
    for element in comp.iter.elts:
        parts = [_substitute(gen.elt, target, element)]
        for guard in comp.ifs:
            parts.append(_substitute(guard, target, element))
        per_element.append(parts)
    return fname, per_element


# --------------------------------------------------------------------------
# Rust
# --------------------------------------------------------------------------


def to_rust(src: Any, tenv: dict[str, TypeSpec], var_names: set[str]) -> str:
    """Translate a spec expression into Rust.

    State variables are read from `state`, action parameters from `params`;
    both are `&serde_json::Value` objects in the generated code.
    """
    return _rust(_body(src), tenv, var_names)


def _rust_value_literal(value: Any) -> str:
    """A `serde_json::Value` expression for a concrete JSON value."""
    if value is True:
        return "Value::from(true)"
    if value is False:
        return "Value::from(false)"
    if value is None:
        return "Value::Null"
    if isinstance(value, str):
        return f"Value::from({_rust_str(value)})"
    if isinstance(value, int):
        return f"Value::from({value}i64)"
    if isinstance(value, list):
        return "Value::from(vec![" + ", ".join(_rust_value_literal(v) for v in value) + "])"
    if isinstance(value, dict):
        items = ", ".join(
            f"({_rust_str(k)}.to_string(), {_rust_value_literal(v)})" for k, v in value.items()
        )
        return "Value::Object(Map::from_iter([" + items + "]))"
    raise SpecError(f"cannot express {value!r} as a JSON value in Rust")


def _rust_domain_literal(t: TypeSpec) -> str:
    """The finite domain of a map key / set element type."""
    domain = getattr(t, "domain", None) or ()
    return "&[" + ", ".join(_rust_value_literal(d) for d in domain) + "]"


def _rust_default(t: TypeSpec) -> Any:
    return default_value(t)


def _rust_coerce(expr: str, t: TypeSpec) -> str:
    """Convert a `Value` expression into the static type of `t`."""
    kind = getattr(t, "kind", None)
    if kind == "int":
        return f"veri_as_int(&({expr}), {_rust_default(t)}i64)"
    if kind == "bool":
        return f"veri_as_bool(&({expr}), {'true' if _rust_default(t) else 'false'})"
    if kind in ("str", "enum"):
        return f"veri_as_str(&({expr}), {_rust_str(str(_rust_default(t)))})"
    return expr


def _rust_read(base: str, name: str, t: TypeSpec) -> str:
    """Read a state variable or an action parameter."""
    raw = f'veri_get({base}, "{name}")'
    kind = getattr(t, "kind", None)
    if kind == "int":
        return f"veri_as_int({raw}, {_rust_default(t)}i64)"
    if kind == "bool":
        return f"veri_as_bool({raw}, {'true' if _rust_default(t) else 'false'})"
    if kind in ("str", "enum"):
        return f"veri_as_str({raw}, {_rust_str(str(_rust_default(t)))})"
    return f"veri_as_value({raw})"


def _rust_key(expr: str, t: TypeSpec) -> str:
    """A map key as the string the JSON object uses."""
    if getattr(t, "kind", None) == "int":
        return f"({expr}).to_string()"
    return f"({expr}).clone()"


def _rust_to_value(expr: str, t: TypeSpec) -> str:
    """A scalar expression as a `serde_json::Value` expression."""
    kind = getattr(t, "kind", None)
    if kind in ("int", "bool"):
        return f"Value::from({expr})"
    if kind in ("str", "enum"):
        return f"Value::from(({expr}).clone())"
    return f"({expr})"


def _rust(node: ast.AST, tenv: dict[str, TypeSpec], var_names: set[str]) -> str:
    if isinstance(node, ast.Constant):
        value = node.value
        if value is True:
            return "true"
        if value is False:
            return "false"
        if value is None:
            return "Value::Null"
        if isinstance(value, str):
            return _rust_string(value)
        return f"{int(value)}i64"
    if isinstance(node, ast.Name):
        base = "state" if node.id in var_names else "params"
        return _rust_read(base, node.id, tenv[node.id])
    if isinstance(node, ast.BoolOp):
        op = " && " if isinstance(node.op, ast.And) else " || "
        return "(" + op.join(f"({_rust(v, tenv, var_names)})" for v in node.values) + ")"
    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.Not):
            return f"(!{_rust(node.operand, tenv, var_names)})"
        if isinstance(node.op, ast.USub):
            return f"(-{_rust(node.operand, tenv, var_names)})"
        return _rust(node.operand, tenv, var_names)
    if isinstance(node, ast.BinOp):
        left = _rust(node.left, tenv, var_names)
        right = _rust(node.right, tenv, var_names)
        if isinstance(node.op, ast.FloorDiv):
            return f"veri_floordiv({left}, {right})"
        if isinstance(node.op, ast.Mod):
            return f"veri_mod({left}, {right})"
        op = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*"}[type(node.op)]
        return f"(({left}) {op} ({right}))"
    if isinstance(node, ast.Compare):
        parts: list[str] = []
        left_node: ast.AST = node.left
        for op, comparator in zip(node.ops, node.comparators):
            left = _rust(left_node, tenv, var_names)
            right = _rust(comparator, tenv, var_names)
            if isinstance(op, ast.Eq):
                parts.append(_rust_eq(left, right, left_node, comparator, tenv))
            elif isinstance(op, ast.NotEq):
                parts.append(f"(!{_rust_eq(left, right, left_node, comparator, tenv)})")
            elif isinstance(op, ast.In):
                parts.append(_rust_membership(left_node, comparator, tenv, var_names))
            elif isinstance(op, ast.NotIn):
                parts.append(f"(!{_rust_membership(left_node, comparator, tenv, var_names)})")
            else:
                sym = {
                    ast.Lt: "<",
                    ast.LtE: "<=",
                    ast.Gt: ">",
                    ast.GtE: ">=",
                }[type(op)]
                parts.append(f"(({left}) {sym} ({right}))")
            left_node = comparator
        return "(" + " && ".join(parts) + ")"
    if isinstance(node, ast.IfExp):
        test = _rust(node.test, tenv, var_names)
        body = _rust(node.body, tenv, var_names)
        orelse = _rust(node.orelse, tenv, var_names)
        return f"if {test} {{ ({body}) }} else {{ ({orelse}) }}"
    if isinstance(node, (ast.List, ast.Tuple)):
        items = ", ".join(_rust(e, tenv, var_names) for e in node.elts)
        return f"vec![{items}]"
    if isinstance(node, ast.Subscript):
        return _rust_subscript(node, tenv, var_names)
    if isinstance(node, ast.Call):
        return _rust_call(node, tenv, var_names)
    raise SpecError(f"cannot transpile {type(node).__name__} to Rust")


def _rust_eq(
    left: str,
    right: str,
    left_node: ast.AST,
    right_node: ast.AST,
    tenv: dict[str, TypeSpec],
) -> str:
    kind = infer_type(left_node, tenv).kind
    if kind in ("int", "bool", "str", "enum"):
        return f"(({left}) == ({right}))"
    return f"veri_value_eq(&({left}), &({right}))"


def _rust_membership(
    left_node: ast.AST,
    right_node: ast.AST,
    tenv: dict[str, TypeSpec],
    var_names: set[str],
) -> str:
    right_type = infer_type(right_node, tenv)
    right = _rust(right_node, tenv, var_names)
    left = _rust(left_node, tenv, var_names)
    left_type = infer_type(left_node, tenv)
    if isinstance(right_type, MapType):
        key = _rust_key(left, left_type)
        return f"veri_contains_key(&({right}), &({key}))"
    if isinstance(right_type, SetType):
        return f"veri_set_has(&({right}), &{_rust_to_value(left, left_type)})"
    if isinstance(right_type, ListType):
        return f"veri_contains(&({right}), &{_rust_to_value(left, left_type)})"
    if isinstance(right_type, StrType):
        return f"(({right}).contains(({left}).as_str()))"
    raise SpecError(f"'in' is not supported on {type_name(right_type)} in Rust")


def _rust_subscript(node: ast.Subscript, tenv: dict[str, TypeSpec], var_names: set[str]) -> str:
    base_type = infer_type(node.value, tenv)
    base = _rust(node.value, tenv, var_names)
    index_node = _slice_of(node)
    index = _rust(index_node, tenv, var_names)
    if isinstance(base_type, MapType):
        key = _rust_key(index, infer_type(index_node, tenv))
        default = _rust_value_literal(_rust_default(base_type.value))
        raw = f"veri_map_get(&({base}), &({key}), &{default})"
        return _rust_coerce(raw, base_type.value)
    if isinstance(base_type, SeqType):
        default = _rust_value_literal(_rust_default(base_type.elem))
        raw = f"veri_seq_get(&({base}), ({index}), &{default})"
        return _rust_coerce(raw, base_type.elem)
    if isinstance(base_type, RecordType):
        field = _const_str(index_node)
        if field is None:
            raise SpecError("record fields must be read with a constant name")
        field_type = dict(base_type.fields)[field]
        default = _rust_value_literal(_rust_default(field_type))
        raw = f'veri_record_get(&({base}), "{field}", &{default})'
        return _rust_coerce(raw, field_type)
    raise SpecError(f"type {type_name(base_type)} cannot be subscripted")


def _rust_call(node: ast.Call, tenv: dict[str, TypeSpec], var_names: set[str]) -> str:
    fname = node.func.id  # type: ignore[union-attr]
    args = [_rust(a, tenv, var_names) for a in node.args]
    if fname == "len":
        arg_type = infer_type(node.args[0], tenv)
        if isinstance(arg_type, (MapType, SetType, SeqType, ListType)):
            return f"veri_len(&({args[0]}))"
        if isinstance(arg_type, StrType):
            return f"veri_str_len(&({args[0]}))"
        raise SpecError(f"len() needs a collection or a string, got {type_name(arg_type)}")
    if fname == "abs":
        return f"veri_abs({args[0]})"
    if fname in ("min", "max"):
        if len(args) == 1:
            return f"({args[0]})"
        folded = args[-1]
        for value in reversed(args[:-1]):
            folded = f"veri_{fname}({value}, {folded})"
        return folded
    if fname in ("all", "any"):
        return _rust_quantifier(node, tenv, var_names)
    if fname == "count_values":
        return f"veri_count_values(&({args[0]}), &({args[1]}))"
    if fname == "same_keys":
        return f"veri_same_keys(&({args[0]}), &({args[1]}))"
    if fname == "map_get":
        map_type = infer_type(node.args[0], tenv)
        assert isinstance(map_type, MapType)
        key_type = infer_type(node.args[1], tenv)
        key = _rust_key(args[1], key_type)
        default = _rust_value_literal(_rust_default(map_type.value))
        raw = f"veri_map_get(&({args[0]}), &({key}), &{default})"
        return _rust_coerce(raw, map_type.value)
    if fname == "map_set":
        map_type = infer_type(node.args[0], tenv)
        assert isinstance(map_type, MapType)
        key_type = infer_type(node.args[1], tenv)
        key = _rust_key(args[1], key_type)
        value_type = infer_type(node.args[2], tenv)
        value = _rust_to_value(args[2], value_type)
        domain = _rust_domain_literal(map_type)
        return f"veri_map_set(&({args[0]}), &({key}), &({value}), {domain})"
    if fname == "set_add":
        set_type = infer_type(node.args[0], tenv)
        assert isinstance(set_type, SetType)
        elem_type = infer_type(node.args[1], tenv)
        value = _rust_to_value(args[1], elem_type)
        domain = _rust_domain_literal(set_type)
        return f"veri_set_add(&({args[0]}), &({value}), {domain})"
    if fname == "set_has":
        set_type = infer_type(node.args[0], tenv)
        assert isinstance(set_type, SetType)
        elem_type = infer_type(node.args[1], tenv)
        value = _rust_to_value(args[1], elem_type)
        return f"veri_set_has(&({args[0]}), &({value}))"
    if fname == "seq_get":
        seq_type = infer_type(node.args[0], tenv)
        assert isinstance(seq_type, SeqType)
        default = _rust_value_literal(_rust_default(seq_type.elem))
        raw = f"veri_seq_get(&({args[0]}), ({args[1]}), &{default})"
        return _rust_coerce(raw, seq_type.elem)
    if fname == "seq_append":
        seq_type = infer_type(node.args[0], tenv)
        assert isinstance(seq_type, SeqType)
        elem_type = infer_type(node.args[1], tenv)
        value = _rust_to_value(args[1], elem_type)
        return f"veri_seq_append(&({args[0]}), &({value}))"
    if fname == "keys":
        return f"veri_keys(&({args[0]}))"
    if fname == "values":
        map_type = infer_type(node.args[0], tenv)
        assert isinstance(map_type, MapType)
        default = _rust_value_literal(_rust_default(map_type.value))
        return f"veri_values(&({args[0]}), &{default})"
    raise SpecError(f"unknown function {fname!r}")


def _rust_quantifier(node: ast.Call, tenv: dict[str, TypeSpec], var_names: set[str]) -> str:
    """Expand `all(...)` / `any(...)` over a list literal."""
    fname, per_element = _quantifier_parts(node, tenv)
    if not per_element:
        return "true" if fname == "all" else "false"
    parts: list[str] = []
    for group in per_element:
        body = _rust(group[0], tenv, var_names)
        for guard in group[1:]:
            condition = _rust(guard, tenv, var_names)
            fallback = "true" if fname == "all" else "false"
            body = f"if {condition} {{ ({body}) }} else {{ ({fallback}) }}"
        parts.append(f"({body})")
    op = " && " if fname == "all" else " || "
    return "(" + op.join(parts) + ")"


# --------------------------------------------------------------------------
# C
# --------------------------------------------------------------------------


def to_c(src: Any, tenv: dict[str, TypeSpec], var_names: set[str]) -> str:
    """Translate a spec expression into C.

    C supports scalar state only: every state variable and every action
    parameter is a plain C local (`long long`, `int` or `const char *`).
    """
    return _c(_body(src), tenv, var_names)


def _c_scalar_only(what: str, t: TypeSpec) -> None:
    if not _is_scalar_kind(t):
        raise SpecError(
            f"the C adapter supports scalar specs only: {what} has type "
            f"{type_name(t)}; use the Rust adapter for collection specs"
        )


def _c(node: ast.AST, tenv: dict[str, TypeSpec], var_names: set[str]) -> str:
    if isinstance(node, ast.Constant):
        value = node.value
        if value is True:
            return "1"
        if value is False:
            return "0"
        if value is None:
            return "NULL"
        if isinstance(value, str):
            return _c_str_literal(value)
        return f"{int(value)}LL"
    if isinstance(node, ast.Name):
        _c_scalar_only(f"variable {node.id!r}", tenv[node.id])
        return node.id
    if isinstance(node, ast.BoolOp):
        op = " && " if isinstance(node.op, ast.And) else " || "
        return "(" + op.join(f"({_c(v, tenv, var_names)})" for v in node.values) + ")"
    if isinstance(node, ast.UnaryOp):
        if isinstance(node.op, ast.Not):
            return f"(!{_c(node.operand, tenv, var_names)})"
        if isinstance(node.op, ast.USub):
            return f"(-{_c(node.operand, tenv, var_names)})"
        return _c(node.operand, tenv, var_names)
    if isinstance(node, ast.BinOp):
        left = _c(node.left, tenv, var_names)
        right = _c(node.right, tenv, var_names)
        if isinstance(node.op, ast.FloorDiv):
            return f"veri_floordiv({left}, {right})"
        if isinstance(node.op, ast.Mod):
            return f"veri_mod({left}, {right})"
        op = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*"}[type(node.op)]
        return f"(({left}) {op} ({right}))"
    if isinstance(node, ast.Compare):
        parts: list[str] = []
        left_node: ast.AST = node.left
        for op, comparator in zip(node.ops, node.comparators):
            left = _c(left_node, tenv, var_names)
            right = _c(comparator, tenv, var_names)
            if isinstance(op, ast.Eq):
                parts.append(_c_eq(left, right, left_node, comparator, tenv))
            elif isinstance(op, ast.NotEq):
                parts.append(f"(!{_c_eq(left, right, left_node, comparator, tenv)})")
            elif isinstance(op, ast.In):
                parts.append(_c_membership(left_node, comparator, tenv, var_names))
            elif isinstance(op, ast.NotIn):
                parts.append(f"(!{_c_membership(left_node, comparator, tenv, var_names)})")
            else:
                sym = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">="}[type(op)]
                parts.append(f"(({left}) {sym} ({right}))")
            left_node = comparator
        return "(" + " && ".join(parts) + ")"
    if isinstance(node, ast.IfExp):
        test = _c(node.test, tenv, var_names)
        body = _c(node.body, tenv, var_names)
        orelse = _c(node.orelse, tenv, var_names)
        return f"(({test}) ? ({body}) : ({orelse}))"
    if isinstance(node, (ast.List, ast.Tuple)):
        items = ", ".join(_c(e, tenv, var_names) for e in node.elts)
        return f"{{{items}}}"
    if isinstance(node, ast.Subscript):
        base_type = infer_type(node.value, tenv)
        _c_scalar_only(f"subscript of {type_name(base_type)}", base_type)
        raise SpecError(
            "the C adapter supports scalar specs only: subscripts need a map, "
            "a sequence or a record"
        )
    if isinstance(node, ast.Call):
        return _c_call(node, tenv, var_names)
    raise SpecError(f"cannot transpile {type(node).__name__} to C")


def _c_eq(
    left: str,
    right: str,
    left_node: ast.AST,
    right_node: ast.AST,
    tenv: dict[str, TypeSpec],
) -> str:
    kind = infer_type(left_node, tenv).kind
    if kind in ("str", "enum"):
        return f"veri_str_eq({left}, {right})"
    return f"(({left}) == ({right}))"


def _c_membership(
    left_node: ast.AST,
    right_node: ast.AST,
    tenv: dict[str, TypeSpec],
    var_names: set[str],
) -> str:
    right_type = infer_type(right_node, tenv)
    if isinstance(right_type, StrType):
        left = _c(left_node, tenv, var_names)
        right = _c(right_node, tenv, var_names)
        return f"veri_str_contains({left}, {right})"
    raise SpecError(
        "the C adapter supports scalar specs only: 'in' needs a map, a set or "
        f"a list, got {type_name(right_type)}"
    )


def _c_call(node: ast.Call, tenv: dict[str, TypeSpec], var_names: set[str]) -> str:
    fname = node.func.id  # type: ignore[union-attr]
    args = [_c(a, tenv, var_names) for a in node.args]
    if fname == "len":
        arg_type = infer_type(node.args[0], tenv)
        if isinstance(arg_type, StrType):
            return f"veri_str_len({args[0]})"
        raise SpecError(
            "the C adapter supports scalar specs only: len() needs a string, "
            f"got {type_name(arg_type)}"
        )
    if fname == "abs":
        return f"veri_abs({args[0]})"
    if fname in ("min", "max"):
        if len(args) == 1:
            return f"({args[0]})"
        folded = args[-1]
        for value in reversed(args[:-1]):
            op = "<" if fname == "min" else ">"
            folded = f"(({value}) {op} ({folded}) ? ({value}) : ({folded}))"
        return folded
    if fname in ("all", "any"):
        return _c_quantifier(node, tenv, var_names)
    raise SpecError(f"the C adapter supports scalar specs only: {fname}() needs a collection")


def _c_quantifier(node: ast.Call, tenv: dict[str, TypeSpec], var_names: set[str]) -> str:
    fname, per_element = _quantifier_parts(node, tenv)
    if not per_element:
        return "1" if fname == "all" else "0"
    parts: list[str] = []
    for group in per_element:
        body = _c(group[0], tenv, var_names)
        for guard in group[1:]:
            condition = _c(guard, tenv, var_names)
            fallback = "1" if fname == "all" else "0"
            body = f"(({condition}) ? ({body}) : ({fallback}))"
        parts.append(f"({body})")
    op = " && " if fname == "all" else " || "
    return "(" + op.join(parts) + ")"
