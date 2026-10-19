"""Type system for the VeriSpec spec format (v1).

Scalars (v0.1)
    int, bool, str, enum[a,b]

Collections (v1, all bounded by an explicit finite domain)
    map   {type: map, key: <scalar>, value: <scalar>, ...domain..., max_size: N}
    set   {type: set, of: <scalar>, ...domain..., max_size: N}
    seq   {type: seq, of: <scalar>, max_size: N}

Records (v1)
    record {type: record, fields: {name: <scalar>, ...}}

Every collection carries a *finite domain*: the set of keys/elements that may
exist.  A finite domain is what makes the Z3 encoding total (no
unconstrained reads, therefore no phantom counterexamples) and what makes the
"bounded collection" promise checkable.

The domain of a map key / set element is derived from the element type:

    int   -> [min .. max]        (declared as key_min/key_max or elem_min/elem_max)
    str   -> explicit list       (declared as key_domain / elem_domain)
    enum  -> the enum values
    bool  -> [False, True]       (rejected for map keys, see below)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .errors import SpecError

#: Maximum number of elements a collection domain may declare.
MAX_DOMAIN = 256
#: Default capacity for collections that do not declare `max_size`.
DEFAULT_MAX_SIZE = 16


@dataclass(frozen=True)
class IntType:
    min: int | None = None
    max: int | None = None
    kind: str = "int"


@dataclass(frozen=True)
class BoolType:
    kind: str = "bool"


@dataclass(frozen=True)
class StrType:
    kind: str = "str"


@dataclass(frozen=True)
class EnumType:
    values: tuple[str, ...]
    kind: str = "enum"


@dataclass(frozen=True)
class ListType:
    """Result of `keys()` / `values()`; concrete evaluation only."""

    elem: Any
    kind: str = "list"


@dataclass(frozen=True)
class MapType:
    key: Any
    value: Any
    max_size: int
    domain: tuple[Any, ...]
    kind: str = "map"


@dataclass(frozen=True)
class SetType:
    elem: Any
    max_size: int
    domain: tuple[Any, ...]
    kind: str = "set"


@dataclass(frozen=True)
class SeqType:
    elem: Any
    max_size: int
    kind: str = "seq"


@dataclass(frozen=True)
class RecordType:
    fields: tuple[tuple[str, Any], ...]
    kind: str = "record"


TypeSpec = (
    IntType | BoolType | StrType | EnumType | ListType | MapType | SetType | SeqType | RecordType
)

SCALAR_KINDS = ("int", "bool", "str", "enum")
COLLECTION_KINDS = ("map", "set", "seq")


def is_scalar(t: TypeSpec) -> bool:
    return getattr(t, "kind", None) in SCALAR_KINDS


def is_collection(t: TypeSpec) -> bool:
    return getattr(t, "kind", None) in COLLECTION_KINDS


def type_name(t: TypeSpec) -> str:
    """Human readable type name, used in briefs, errors and reports."""
    kind = getattr(t, "kind", "?")
    if kind == "int":
        if t.min is not None and t.max is not None:
            return f"int[{t.min}..{t.max}]"
        if t.min is not None:
            return f"int[>={t.min}]"
        if t.max is not None:
            return f"int[<={t.max}]"
        return "int"
    if kind == "bool":
        return "bool"
    if kind == "str":
        return "str"
    if kind == "enum":
        return "enum[" + ",".join(t.values) + "]"
    if kind == "list":
        return f"list[{type_name(t.elem)}]"
    if kind == "map":
        return (
            f"map[{type_name(t.key)} -> {type_name(t.value)}]"
            f" (max {t.max_size} keys, domain {len(t.domain)})"
        )
    if kind == "set":
        return f"set[{type_name(t.elem)}] (max {t.max_size} elements, domain {len(t.domain)})"
    if kind == "seq":
        return f"seq[{type_name(t.elem)}] (max {t.max_size} elements)"
    if kind == "record":
        return "record{" + ", ".join(f"{n}: {type_name(ft)}" for n, ft in t.fields) + "}"
    return str(kind)


def default_value(t: TypeSpec) -> Any:
    """Value returned when reading a key that is not present."""
    kind = getattr(t, "kind", None)
    if kind == "int":
        return t.min if t.min is not None else 0
    if kind == "bool":
        return False
    if kind == "str":
        return ""
    if kind == "enum":
        return t.values[0]
    if kind == "seq":
        return []
    if kind == "map":
        return {}
    if kind == "set":
        return set()
    if kind == "record":
        return {name: default_value(ft) for name, ft in t.fields}
    if kind == "list":
        return []
    raise SpecError(f"no default value for type {type_name(t)}")


def finite_domain(t: TypeSpec) -> tuple[Any, ...] | None:
    """Finite domain of a scalar type, or None when it is unbounded."""
    kind = getattr(t, "kind", None)
    if kind == "int":
        if t.min is not None and t.max is not None:
            return tuple(range(t.min, t.max + 1))
        return None
    if kind == "bool":
        return (False, True)
    if kind == "enum":
        return t.values
    if kind == "str":
        return None
    return None


def same_type(a: TypeSpec, b: TypeSpec) -> bool:
    """Loose type compatibility used by the type checker."""
    ka, kb = getattr(a, "kind", None), getattr(b, "kind", None)
    if ka != kb:
        return False
    if ka == "enum":
        return a.values == b.values
    if ka == "record":
        return [n for n, _ in a.fields] == [n for n, _ in b.fields]
    if ka == "map":
        return same_type(a.key, b.key) and same_type(a.value, b.value)
    if ka in ("set", "seq", "list"):
        return same_type(a.elem, b.elem)
    return True


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------


def _parse_type_str(raw: str, where: str) -> TypeSpec:
    s = raw.strip()
    if s == "int":
        return IntType()
    if s == "bool":
        return BoolType()
    if s == "str":
        return StrType()
    m = re.fullmatch(r"enum\[(.*)\]", s)
    if m:
        values = tuple(v.strip() for v in m.group(1).split(",") if v.strip())
        if not values:
            raise SpecError(f"{where}: empty enum type")
        if len(set(values)) != len(values):
            raise SpecError(f"{where}: duplicate values in enum")
        return EnumType(values=values)
    raise SpecError(
        f"{where}: unsupported type {raw!r} "
        "(supported: int, bool, str, enum[...], map, set, seq, record)"
    )


def _int_range(elem: TypeSpec, raw: dict, prefix: str, where: str) -> tuple[int, int]:
    """Resolve the (min, max) of an int element type from either spelling."""
    lo = raw.get(f"{prefix}_min", getattr(elem, "min", None))
    hi = raw.get(f"{prefix}_max", getattr(elem, "max", None))
    if lo is None or hi is None:
        raise SpecError(
            f"{where}: {prefix} must declare {prefix}_min and {prefix}_max "
            "(int collections need a finite domain)"
        )
    if not isinstance(lo, int) or isinstance(lo, bool) or not isinstance(hi, int) or isinstance(hi, bool):
        raise SpecError(f"{where}: {prefix}_min/{prefix}_max must be integers")
    if lo > hi:
        raise SpecError(f"{where}: {prefix}_min > {prefix}_max")
    if hi - lo + 1 > MAX_DOMAIN:
        raise SpecError(
            f"{where}: domain of {hi - lo + 1} values exceeds the limit of {MAX_DOMAIN}; "
            "narrow the range"
        )
    return lo, hi


def _domain_for(elem: TypeSpec, raw: dict, prefix: str, where: str) -> tuple[Any, ...]:
    """Finite domain of a collection key/element type."""
    kind = getattr(elem, "kind", None)
    if kind == "int":
        lo, hi = _int_range(elem, raw, prefix, where)
        return tuple(range(lo, hi + 1))
    if kind == "enum":
        return elem.values
    if kind == "str":
        domain = raw.get(f"{prefix}_domain")
        if domain is None:
            raise SpecError(
                f"{where}: str elements require an explicit {prefix}_domain list "
                "(collections must be bounded)"
            )
        if not isinstance(domain, list) or not domain:
            raise SpecError(f"{where}: {prefix}_domain must be a non-empty list")
        for item in domain:
            if not isinstance(item, str):
                raise SpecError(f"{where}: {prefix}_domain entries must be strings")
        if len(set(domain)) != len(domain):
            raise SpecError(f"{where}: duplicate entries in {prefix}_domain")
        if len(domain) > MAX_DOMAIN:
            raise SpecError(f"{where}: {prefix}_domain exceeds the limit of {MAX_DOMAIN}")
        return tuple(domain)
    if kind == "bool":
        raise SpecError(
            f"{where}: bool elements are not supported in collections in v1 "
            "(use int or enum)"
        )
    raise SpecError(f"{where}: unsupported element type {type_name(elem)}")


def _parse_map(raw: dict, where: str) -> MapType:
    if "key" not in raw:
        raise SpecError(f"{where}: map requires a 'key' type")
    if "value" not in raw:
        raise SpecError(f"{where}: map requires a 'value' type")
    key = parse_type(raw["key"], f"{where}.key")
    value = parse_type(raw["value"], f"{where}.value")
    if not is_scalar(key):
        raise SpecError(f"{where}: map keys must be a scalar type (int/str/enum)")
    if key.kind == "bool":
        raise SpecError(f"{where}: bool map keys are not supported in v1")
    if not is_scalar(value):
        raise SpecError(f"{where}: map values must be a scalar type (int/str/enum) in v1")
    domain = _domain_for(key, raw, "key", where)
    max_size = raw.get("max_size", len(domain))
    if not isinstance(max_size, int) or isinstance(max_size, bool) or max_size < 0:
        raise SpecError(f"{where}: max_size must be a non-negative integer")
    return MapType(key=key, value=value, max_size=max_size, domain=domain)


def _parse_set(raw: dict, where: str) -> SetType:
    if "of" not in raw:
        raise SpecError(f"{where}: set requires an 'of' element type")
    elem = parse_type(raw["of"], f"{where}.of")
    if not is_scalar(elem):
        raise SpecError(f"{where}: set elements must be a scalar type (int/str/enum)")
    domain = _domain_for(elem, raw, "elem", where)
    max_size = raw.get("max_size", len(domain))
    if not isinstance(max_size, int) or isinstance(max_size, bool) or max_size < 0:
        raise SpecError(f"{where}: max_size must be a non-negative integer")
    return SetType(elem=elem, max_size=max_size, domain=domain)


def _parse_seq(raw: dict, where: str) -> SeqType:
    if "of" not in raw:
        raise SpecError(f"{where}: seq requires an 'of' element type")
    elem = parse_type(raw["of"], f"{where}.of")
    if not is_scalar(elem):
        raise SpecError(f"{where}: seq elements must be a scalar type (int/bool/str/enum)")
    if elem.kind == "int":
        # optional documentation bounds for the elements
        lo = raw.get("elem_min", elem.min)
        hi = raw.get("elem_max", elem.max)
        if lo is not None or hi is not None:
            if lo is None or hi is None:
                raise SpecError(f"{where}: elem_min and elem_max must be declared together")
            if lo > hi:
                raise SpecError(f"{where}: elem_min > elem_max")
            elem = IntType(min=lo, max=hi)
    max_size = raw.get("max_size", DEFAULT_MAX_SIZE)
    if not isinstance(max_size, int) or isinstance(max_size, bool) or max_size < 1:
        raise SpecError(f"{where}: seq requires max_size >= 1")
    return SeqType(elem=elem, max_size=max_size)


def _parse_record(raw: dict, where: str) -> RecordType:
    fields_raw = raw.get("fields")
    if not isinstance(fields_raw, dict) or not fields_raw:
        raise SpecError(f"{where}: record requires a non-empty 'fields' mapping")
    fields: list[tuple[str, TypeSpec]] = []
    for name, fraw in fields_raw.items():
        if not isinstance(name, str) or not name.isidentifier():
            raise SpecError(f"{where}: record field name {name!r} must be a valid identifier")
        ftype = parse_type(fraw, f"{where}.fields.{name}")
        if not is_scalar(ftype):
            raise SpecError(f"{where}: record fields must be scalar types in v1")
        fields.append((name, ftype))
    return RecordType(fields=tuple(fields))


def parse_type(raw: Any, where: str = "type") -> TypeSpec:
    """Parse a type declaration (string shorthand or mapping) into a TypeSpec."""
    if isinstance(raw, str):
        return _parse_type_str(raw, where)
    if not isinstance(raw, dict):
        raise SpecError(f"{where}: type must be a string or a mapping, got {type(raw).__name__}")
    t = raw.get("type")
    if t is None:
        raise SpecError(f"{where}: missing 'type'")
    if not isinstance(t, str):
        raise SpecError(f"{where}: 'type' must be a string")
    if t == "int":
        lo, hi = raw.get("min"), raw.get("max")
        for bound in (lo, hi):
            if bound is not None and (not isinstance(bound, int) or isinstance(bound, bool)):
                raise SpecError(f"{where}: min/max must be integers")
        if lo is not None and hi is not None and lo > hi:
            raise SpecError(f"{where}: min > max")
        return IntType(min=lo, max=hi)
    if t in ("bool", "str"):
        return _parse_type_str(t, where)
    if t.startswith("enum["):
        return _parse_type_str(t, where)
    if t == "enum":
        values = raw.get("values")
        if not isinstance(values, list) or not values:
            raise SpecError(f"{where}: enum requires a non-empty 'values' list")
        for v in values:
            if not isinstance(v, str):
                raise SpecError(f"{where}: enum values must be strings")
        return EnumType(values=tuple(values))
    if t == "map":
        return _parse_map(raw, where)
    if t == "set":
        return _parse_set(raw, where)
    if t == "seq":
        return _parse_seq(raw, where)
    if t == "record":
        return _parse_record(raw, where)
    raise SpecError(
        f"{where}: unsupported type {t!r} "
        "(supported: int, bool, str, enum[...], map, set, seq, record)"
    )
