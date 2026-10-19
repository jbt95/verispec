"""Concrete runtime values for VeriSpec specs.

The interpreter, the trace engine and the code generators all agree on this
model:

    int / bool / str / enum   -> Python int / bool / str / str
    map                       -> dict[key, value]
    set                       -> set[element]
    seq                       -> list[element]
    record                    -> dict[field, value]

JSON representation (used by traces, adapters and the MCP server):

    map     -> object, keys stringified ("0" for integer keys)
    set     -> array of elements
    seq     -> array of elements
    record  -> object keyed by field name
"""

from __future__ import annotations

from typing import Any

from .errors import SpecError
from .types import (
    EnumType,
    IntType,
    MapType,
    SeqType,
    SetType,
    StrType,
    TypeSpec,
    default_value,
    type_name,
)


def in_domain(domain: tuple[Any, ...], value: Any) -> bool:
    """Is `value` part of the finite domain of a collection key/element?"""
    for item in domain:
        if item == value and isinstance(item, bool) == isinstance(value, bool):
            return True
    return False


def map_get(m: dict, key: Any, t: MapType) -> Any:
    """Read a map key; missing keys yield the value type default."""
    if key in m:
        return m[key]
    return default_value(t.value)


def map_set(m: dict, key: Any, value: Any, t: MapType) -> dict:
    """Return a copy of `m` with `key` set to `value`.

    Keys outside the declared domain are ignored, which is exactly what the Z3
    encoding does, so the two semantics never disagree.
    """
    if not in_domain(t.domain, key):
        return dict(m)
    out = dict(m)
    out[key] = value
    return out


def set_add(s: set, value: Any, t: SetType) -> set:
    out = set(s)
    if in_domain(t.domain, value):
        out.add(value)
    return out


def set_has(s: set, value: Any, t: SetType) -> bool:
    return value in s


def seq_get(q: list, index: int, t: SeqType) -> Any:
    if isinstance(index, bool) or not isinstance(index, int):
        return default_value(t.elem)
    if 0 <= index < len(q):
        return q[index]
    return default_value(t.elem)


def seq_append(q: list, value: Any, t: SeqType) -> list:
    return list(q) + [value]


def value_to_json(t: TypeSpec, value: Any) -> Any:
    """Convert a concrete value into its JSON representation."""
    kind = getattr(t, "kind", None)
    if kind == "map":
        return {_json_key(k): value_to_json(t.value, v) for k, v in value.items()}
    if kind == "set":
        return [value_to_json(t.elem, v) for v in sorted(value, key=_sort_key)]
    if kind == "seq":
        return [value_to_json(t.elem, v) for v in value]
    if kind == "record":
        return {name: value_to_json(ft, value.get(name, default_value(ft))) for name, ft in t.fields}
    if kind == "list":
        return [value_to_json(t.elem, v) for v in value]
    return value


def _sort_key(value: Any) -> tuple[int, Any]:
    return (0, value) if isinstance(value, (int, float)) and not isinstance(value, bool) else (1, str(value))


def _json_key(key: Any) -> str:
    if isinstance(key, bool):
        return "true" if key else "false"
    if isinstance(key, int):
        return str(key)
    return str(key)


def json_to_value(t: TypeSpec, raw: Any, where: str = "value") -> Any:
    """Validate a JSON value against a type and return the concrete value."""
    kind = getattr(t, "kind", None)
    if kind == "int":
        if not isinstance(raw, int) or isinstance(raw, bool):
            raise SpecError(f"{where}: expected int, got {raw!r}")
        if t.min is not None and raw < t.min:
            raise SpecError(f"{where}: {raw} is below the declared min {t.min}")
        if t.max is not None and raw > t.max:
            raise SpecError(f"{where}: {raw} is above the declared max {t.max}")
        return raw
    if kind == "bool":
        if not isinstance(raw, bool):
            raise SpecError(f"{where}: expected bool, got {raw!r}")
        return raw
    if kind == "str":
        if not isinstance(raw, str):
            raise SpecError(f"{where}: expected str, got {raw!r}")
        return raw
    if kind == "enum":
        if not isinstance(raw, str):
            raise SpecError(f"{where}: expected {type_name(t)}, got {raw!r}")
        if raw not in t.values:
            raise SpecError(f"{where}: {raw!r} is not one of {list(t.values)}")
        return raw
    if kind == "map":
        if not isinstance(raw, dict):
            raise SpecError(f"{where}: expected an object for {type_name(t)}, got {raw!r}")
        out: dict[Any, Any] = {}
        for k, v in raw.items():
            key = _json_to_key(t.key, k, where)
            if not in_domain(t.domain, key):
                raise SpecError(f"{where}: key {k!r} is outside the declared domain")
            if key in out:
                raise SpecError(f"{where}: duplicate key {k!r}")
            out[key] = json_to_value(t.value, v, f"{where}.{k}")
        return out
    if kind == "set":
        if isinstance(raw, (set, frozenset)):
            raw = list(raw)
        if not isinstance(raw, list):
            raise SpecError(f"{where}: expected an array for {type_name(t)}, got {raw!r}")
        out_set: set[Any] = set()
        for i, v in enumerate(raw):
            item = json_to_value(t.elem, v, f"{where}[{i}]")
            if not in_domain(t.domain, item):
                raise SpecError(f"{where}: element {v!r} is outside the declared domain")
            if item in out_set:
                raise SpecError(f"{where}: duplicate element {v!r}")
            out_set.add(item)
        return out_set
    if kind == "seq":
        if not isinstance(raw, list):
            raise SpecError(f"{where}: expected an array for {type_name(t)}, got {raw!r}")
        if len(raw) > t.max_size:
            raise SpecError(f"{where}: sequence of {len(raw)} exceeds max_size {t.max_size}")
        return [json_to_value(t.elem, v, f"{where}[{i}]") for i, v in enumerate(raw)]
    if kind == "record":
        if not isinstance(raw, dict):
            raise SpecError(f"{where}: expected an object for {type_name(t)}, got {raw!r}")
        expected = {name for name, _ in t.fields}
        got = set(raw)
        missing = expected - got
        extra = got - expected
        if missing:
            raise SpecError(f"{where}: missing field(s) {sorted(missing)}")
        if extra:
            raise SpecError(f"{where}: unknown field(s) {sorted(extra)}")
        return {name: json_to_value(ft, raw[name], f"{where}.{name}") for name, ft in t.fields}
    if kind == "list":
        if not isinstance(raw, list):
            raise SpecError(f"{where}: expected an array, got {raw!r}")
        return [json_to_value(t.elem, v, f"{where}[{i}]") for i, v in enumerate(raw)]
    raise SpecError(f"{where}: unsupported type {type_name(t)}")


def _json_to_key(key_type: TypeSpec, raw: Any, where: str) -> Any:
    if isinstance(key_type, IntType):
        if isinstance(raw, bool):
            raise SpecError(f"{where}: invalid integer key {raw!r}")
        if isinstance(raw, int):
            return raw
        try:
            return int(str(raw))
        except ValueError as e:
            raise SpecError(f"{where}: invalid integer key {raw!r}") from e
    if isinstance(key_type, EnumType):
        if not isinstance(raw, str):
            raise SpecError(f"{where}: invalid enum key {raw!r}")
        return raw
    if isinstance(key_type, StrType):
        if not isinstance(raw, str):
            raise SpecError(f"{where}: invalid string key {raw!r}")
        return raw
    raise SpecError(f"{where}: unsupported key type {type_name(key_type)}")


def state_to_json(types: dict[str, TypeSpec], state: dict[str, Any]) -> dict[str, Any]:
    """Serialize a state dict to its JSON representation."""
    return {name: value_to_json(types[name], state[name]) for name in types if name in state}


def state_from_json(types: dict[str, TypeSpec], raw: Any, where: str = "state") -> dict[str, Any]:
    """Parse and validate a JSON state dict against the spec var types."""
    if not isinstance(raw, dict):
        raise SpecError(f"{where}: expected an object, got {raw!r}")
    missing = [n for n in types if n not in raw]
    if missing:
        raise SpecError(f"{where}: missing var(s) {missing}")
    extra = [n for n in raw if n not in types]
    if extra:
        raise SpecError(f"{where}: unknown var(s) {extra}")
    return {n: json_to_value(types[n], raw[n], f"{where}.{n}") for n in types}
