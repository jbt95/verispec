"""The VeriSpec spec model: loading, validation and concrete semantics.

One YAML file is one state machine:

    spec_version: "1.0"
    name: Bank
    description: ...
    imports: [...]              # optional modules
    vars: {...}                 # scalar, map, set, seq and record state
    init: {...}                 # initial values (expressions / concrete data)
    actions:                    # requires (preconditions) + effects
      - name: transfer
        params: {...}
        requires: [...]
        effects: {...}
    invariants: [...]           # MUST always hold

The spec is the human-owned artifact: agents read it, they never edit it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import yaml

from . import types as T
from .errors import SpecError
from .expr import SpecError as _SpecError  # noqa: F401  (re-exported for callers)
from .types import (
    EnumType,
    MapType,
    RecordType,
    SeqType,
    SetType,
    TypeSpec,
    parse_type,
    type_name,
)
from .values import default_value, json_to_value

__all__ = [
    "SpecError",
    "Spec",
    "Var",
    "Action",
    "load_spec",
    "load_spec_data",
    "initial_state",
    "apply_action",
    "check_invariants",
    "SUPPORTED_SPEC_MAJORS",
    "parse_semver",
    "version_matches",
]

#: Major versions of the spec format this CLI understands.
SUPPORTED_SPEC_MAJORS = (0, 1)
#: The format version new specs are written with.
CURRENT_SPEC_VERSION = "1.0"


# --------------------------------------------------------------------------
# versions
# --------------------------------------------------------------------------


def parse_semver(text: str) -> tuple[int, int, int]:
    m = re.fullmatch(r"v?(\d+)\.(\d+)(?:\.(\d+))?", str(text).strip())
    if not m:
        raise SpecError(f"invalid version {text!r}: expected MAJOR.MINOR[.PATCH]")
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)


def version_matches(version: str, spec_range: str | None) -> bool:
    """Check a version against a small semver range language.

    Supported: `1.0`, `^1.0`, `~1.0`, `>=1.0,<2.0`, `1.x`.
    """
    if not spec_range:
        return True
    v = parse_semver(version)
    for part in str(spec_range).split(","):
        part = part.strip()
        if not part:
            continue
        if part.startswith("^"):
            bound = parse_semver(part[1:])
            if v[0] != bound[0]:
                return False
            if bound[1] > 0 and v[1] < bound[1]:
                return False
            if bound[1] == 0 and v[1] > 0:
                return False
        elif part.startswith("~"):
            bound = parse_semver(part[1:])
            if v[0] != bound[0] or v[1] != bound[1]:
                return False
        elif part.startswith(">="):
            if v < parse_semver(part[2:]):
                return False
        elif part.startswith(">"):
            if v <= parse_semver(part[1:]):
                return False
        elif part.startswith("<="):
            if v > parse_semver(part[2:]):
                return False
        elif part.startswith("<"):
            if v >= parse_semver(part[1:]):
                return False
        elif part.endswith(".x") or part.endswith(".*"):
            bound = parse_semver(part[:-2] + ".0")
            if v[0] != bound[0]:
                return False
        else:
            bound = parse_semver(part)
            if v[0] != bound[0]:
                return False
            if len(part.split(".")) == 1:
                continue
            if v[1] != bound[1]:
                return False
    return True


# --------------------------------------------------------------------------
# model
# --------------------------------------------------------------------------


class Var:
    """A state variable."""

    def __init__(self, name: str, raw: Any):
        if isinstance(raw, str):
            raw = {"type": raw}
        if not isinstance(raw, dict):
            raise SpecError(f"var {name}: declaration must be a mapping or a type string")
        self.name = name
        self.raw = raw
        self.type = parse_type(raw, f"var {name}")
        self.kind = self.type.kind
        raw_type = raw.get("type")
        self.type_str = raw_type if isinstance(raw_type, str) else type_name(self.type)
        self.min = getattr(self.type, "min", None)
        self.max = getattr(self.type, "max", None)
        self.values = list(getattr(self.type, "values", ()) or ())

    @property
    def describe(self) -> str:
        return type_name(self.type)

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"Var({self.name!r}, {self.describe})"


class Action:
    """A state transition: preconditions and effects."""

    def __init__(self, raw: dict):
        if not isinstance(raw, dict) or "name" not in raw:
            raise SpecError("every action needs a 'name'")
        self.name = raw["name"]
        self.raw = raw
        self.params: dict[str, Var] = {}
        for pn, pr in (raw.get("params") or {}).items():
            var = Var(pn, pr)
            if not T.is_scalar(var.type):
                raise SpecError(
                    f"action {self.name}: parameter {pn} must be a scalar type in v1"
                )
            self.params[pn] = var
        self.requires = list(raw.get("requires") or [])
        self.effects = dict(raw.get("effects") or {})

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"Action({self.name!r})"


class Spec:
    """A parsed, validated spec."""

    def __init__(self, data: dict, path: str = "<spec>"):
        if not isinstance(data, dict):
            raise SpecError("spec must be a mapping")
        self.path = path
        self.raw = data
        self.name = data.get("name", "Unnamed")
        self.description = data.get("description", "")
        raw_version = data.get("spec_version")
        self.spec_version = str(raw_version) if raw_version is not None else "0.1"
        self.declared_version = raw_version is not None
        self.imports = list(data.get("imports") or [])
        self.vars: dict[str, Var] = {}
        for n, r in (data.get("vars") or {}).items():
            self.vars[n] = Var(n, r)
        self.init: dict[str, Any] = dict(data.get("init") or {})
        self.actions = [Action(a) for a in (data.get("actions") or [])]
        self.invariants: list[dict[str, str]] = []
        for inv in data.get("invariants") or []:
            if isinstance(inv, str):
                self.invariants.append({"name": inv, "expr": inv})
            elif isinstance(inv, dict):
                self.invariants.append(
                    {"name": inv.get("name", "invariant"), "expr": inv.get("expr", "")}
                )
            else:
                raise SpecError("invariants must be strings or {name, expr} mappings")
        self._convert_effects()
        self.enum_map: dict[str, int] = {}
        for v in list(self.vars.values()) + [p for a in self.actions for p in a.params.values()]:
            if v.kind == "enum":
                for i, val in enumerate(v.values):
                    self.enum_map.setdefault(val, i)
        self.modules: list[dict[str, Any]] = []

    def _convert_effects(self) -> None:
        """Turn the raw effect values into expressions.

        A bare word is a string literal unless it names a known variable; an
        enum value is always a literal.
        """
        for action in self.actions:
            for name, raw in list(action.effects.items()):
                var = self.vars.get(name)
                if var is None:
                    continue
                if isinstance(var.type, RecordType):
                    if isinstance(raw, dict):
                        fields = dict(var.type.fields)
                        action.effects[name] = {
                            fname: _value_to_expr(
                                value,
                                self.types,
                                f"action {action.name} effect {name}.{fname}",
                                expected=fields.get(fname),
                            )
                            for fname, value in raw.items()
                        }
                    continue
                action.effects[name] = _value_to_expr(
                    raw, self.types, f"action {action.name} effect {name}", expected=var.type
                )

    # -- helpers ---------------------------------------------------------

    @property
    def types(self) -> dict[str, TypeSpec]:
        """Type of every state variable."""
        return {n: v.type for n, v in self.vars.items()}

    def action(self, name: str) -> Action:
        for a in self.actions:
            if a.name == name:
                return a
        raise SpecError(f"unknown action {name!r}")

    def has_action(self, name: str) -> bool:
        return any(a.name == name for a in self.actions)

    def tenv(self, action: Action | None = None) -> dict[str, TypeSpec]:
        """Type environment for expressions (state vars + action params)."""
        env = dict(self.types)
        if action is not None:
            for pn, pv in action.params.items():
                env[pn] = pv.type
        return env

    def to_dict(self) -> dict[str, Any]:
        """Canonical, self-contained representation of the *resolved* spec.

        Imports are already merged and namespaced, so the result can be
        rebuilt with `load_spec_data` without touching the file system.
        """
        return {
            "name": self.name,
            "description": self.description,
            "spec_version": self.spec_version,
            "vars": {n: v.raw for n, v in self.vars.items()},
            "init": self.init,
            "actions": [a.raw for a in self.actions],
            "invariants": list(self.invariants),
        }

    # -- validation ------------------------------------------------------

    def validate(self) -> bool:
        if not self.vars:
            raise SpecError("spec has no vars")
        major = parse_semver(self.spec_version)[0]
        if major not in SUPPORTED_SPEC_MAJORS:
            raise SpecError(
                f"spec format v{major} is not supported by this CLI "
                f"(supported majors: {list(SUPPORTED_SPEC_MAJORS)}); run `verispec migrate`"
            )
        # init
        for k, e in self.init.items():
            if k not in self.vars:
                raise SpecError(f"init assigns unknown var {k!r}")
            self._check_init_value(self.vars[k], e, f"init.{k}")
        # actions
        seen: set[str] = set()
        for a in self.actions:
            if a.name in seen:
                raise SpecError(f"duplicate action {a.name!r}")
            seen.add(a.name)
            tenv = self.tenv(a)
            for r in a.requires:
                _type_check(r, tenv, f"action {a.name} requires")
            for k, e in a.effects.items():
                if k not in self.vars:
                    raise SpecError(f"action {a.name} effect assigns unknown var {k!r}")
                var = self.vars[k]
                if isinstance(var.type, RecordType):
                    if not isinstance(e, dict):
                        raise SpecError(
                            f"action {a.name}: effect on record var {k!r} must be a mapping "
                            "of field -> expression"
                        )
                    known = {n for n, _ in var.type.fields}
                    for fname, fexpr in e.items():
                        if fname not in known:
                            raise SpecError(
                                f"action {a.name}: unknown field {fname!r} of record var {k!r}; "
                                f"known fields: {sorted(known)}"
                            )
                        ftype = dict(var.type.fields)[fname]
                        _type_check(fexpr, tenv, f"action {a.name} effect {k}.{fname}")
                        _check_assignable(ftype, fexpr, tenv, f"action {a.name} effect {k}.{fname}")
                else:
                    _type_check(e, tenv, f"action {a.name} effect {k}")
                    _check_assignable(var.type, e, tenv, f"action {a.name} effect {k}")
        # invariants
        inv_names: set[str] = set()
        for inv in self.invariants:
            if inv["name"] in inv_names:
                raise SpecError(f"duplicate invariant {inv['name']!r}")
            inv_names.add(inv["name"])
            t = _type_check(inv["expr"], self.types, f"invariant {inv['name']}")
            if t.kind != "bool":
                raise SpecError(
                    f"invariant {inv['name']} must be a boolean expression, got {type_name(t)}"
                )
        # bounds sanity
        for v in self.vars.values():
            if v.kind == "int" and v.min is not None and v.max is not None and v.min > v.max:
                raise SpecError(f"var {v.name} min>max")
        return True

    def _check_init_value(self, var: Var, raw: Any, where: str) -> None:
        t = var.type
        if isinstance(t, (MapType, SetType, SeqType)):
            json_to_value(t, raw, where)  # concrete data, validated
            return
        if isinstance(t, RecordType):
            if not isinstance(raw, dict):
                raise SpecError(f"{where}: record init must be a mapping of field -> value")
            known = {n for n, _ in t.fields}
            missing = known - set(raw)
            extra = set(raw) - known
            if missing:
                raise SpecError(f"{where}: missing field(s) {sorted(missing)}")
            if extra:
                raise SpecError(f"{where}: unknown field(s) {sorted(extra)}")
            for fname, ft in t.fields:
                fval = raw[fname]
                if isinstance(fval, str) and (
                    fval.strip().lower() in ("true", "false")
                    or _looks_like_expression(fval, self.types)
                ):
                    _type_check(fval, self.types, f"{where}.{fname}")
                    _check_assignable(ft, fval, self.types, f"{where}.{fname}")
                else:
                    json_to_value(ft, fval, f"{where}.{fname}")
            return
        # scalars: expressions
        if isinstance(raw, (dict, list)):
            raise SpecError(f"{where}: scalar init must be an expression")
        expr = _value_to_expr(raw, self.types, where)
        _type_check(expr, self.types, where)
        _check_assignable(t, expr, self.types, where)


# --------------------------------------------------------------------------
# expression helpers
# --------------------------------------------------------------------------


def _looks_like_expression(text: str, tenv: dict[str, TypeSpec]) -> bool:
    """Is this bare string a variable reference rather than a literal?"""
    s = text.strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", s):
        return False
    return s in tenv


def _value_to_expr(
    raw: Any,
    tenv: dict[str, TypeSpec],
    where: str,
    expected: TypeSpec | None = None,
) -> str:
    """Turn a YAML value into an expression string.

    Numbers and booleans become constants.  A bare word is a string literal
    unless it names a known variable, so `name: alice` works.  When the
    expected type is an enum, a word that is one of its values is always a
    literal, even if a variable happens to have the same name.
    """
    if isinstance(raw, bool):
        return "True" if raw else "False"
    if isinstance(raw, int):
        return str(raw)
    if isinstance(raw, float):
        raise SpecError(f"{where}: floats are not allowed in specs")
    if isinstance(raw, str):
        stripped = raw.strip()
        if stripped.lower() in ("true", "false"):
            return stripped.lower()
        if expected is not None and isinstance(expected, EnumType):
            if stripped in expected.values:
                return repr(stripped)
        if _looks_like_expression(raw, tenv):
            return raw
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", stripped):
            return repr(stripped)
        return raw
    raise SpecError(f"{where}: unsupported value {raw!r}")


def _type_check(expr: Any, tenv: dict[str, TypeSpec], where: str) -> TypeSpec:
    from .expr import infer_type, parse_expr

    try:
        tree = parse_expr(expr)
        return infer_type(tree.body, tenv)
    except SpecError as e:
        raise SpecError(f"{where}: {e}") from e


def _check_assignable(target: TypeSpec, expr: Any, tenv: dict[str, TypeSpec], where: str) -> None:
    from .expr import infer_type, parse_expr

    got = infer_type(parse_expr(expr).body, tenv)
    if isinstance(target, EnumType) and got.kind in ("str", "enum"):
        return
    if isinstance(target, (MapType, SetType, SeqType)) and got.kind == target.kind:
        return
    if isinstance(target, RecordType) and got.kind == "record":
        return
    if not T.same_type(target, got):
        raise SpecError(
            f"{where}: expected {type_name(target)}, expression has type {type_name(got)}"
        )


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------


def load_spec_data(data: dict, path: str = "<spec>") -> Spec:
    """Build a spec from an in-memory mapping (imports resolved)."""
    spec = Spec(data, path)
    if spec.imports:
        _apply_imports(spec, path)
    spec.validate()
    return spec


def _apply_imports(spec: Spec, path: str) -> None:
    """Merge imported modules into the spec, namespaced by alias."""
    base = Path(path).parent if path not in ("<spec>", "") else Path(".")
    merged_vars: dict[str, Any] = {}
    merged_init: dict[str, Any] = {}
    merged_actions: list[dict[str, Any]] = []
    merged_invariants: list[dict[str, str]] = []
    for imp in spec.imports:
        if not isinstance(imp, dict) or "path" not in imp:
            raise SpecError("every import needs a 'path'")
        alias = imp.get("as") or Path(imp["path"]).stem
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", alias):
            raise SpecError(f"import alias {alias!r} must be a valid identifier")
        imp_path = (base / imp["path"]).resolve()
        if not imp_path.exists():
            raise SpecError(f"import not found: {imp['path']} (resolved to {imp_path})")
        imported = load_spec(str(imp_path))
        want = imp.get("version")
        if want and not version_matches(imported.spec_version, want):
            raise SpecError(
                f"import {imp['path']} has spec_version {imported.spec_version}, "
                f"which does not satisfy {want!r}"
            )
        mapping = {n: f"{alias}_{n}" for n in imported.vars}
        for n, v in imported.vars.items():
            merged_vars[f"{alias}_{n}"] = v.raw
        for n, e in imported.init.items():
            merged_init[f"{alias}_{n}"] = _rename_expr(e, mapping) if isinstance(e, str) else e
        for a in imported.actions:
            raw = dict(a.raw)
            raw["name"] = f"{alias}_{a.name}"
            raw["requires"] = [_rename_expr(r, mapping) for r in a.requires]
            raw["effects"] = {
                mapping.get(k, k): (
                    _rename_expr(v, mapping)
                    if isinstance(v, str)
                    else {fk: _rename_expr(fv, mapping) for fk, fv in v.items()}
                )
                for k, v in a.effects.items()
            }
            merged_actions.append(raw)
        for inv in imported.invariants:
            merged_invariants.append(
                {"name": f"{alias}_{inv['name']}", "expr": _rename_expr(inv["expr"], mapping)}
            )
        spec.modules.append(
            {
                "alias": alias,
                "path": str(imp_path),
                "spec_version": imported.spec_version,
                "vars": list(mapping),
            }
        )
    # imports first, then the local spec
    spec.vars = {**{n: Var(n, r) for n, r in merged_vars.items()}, **spec.vars}
    spec.init = {**merged_init, **spec.init}
    spec.actions = [Action(a) for a in merged_actions] + spec.actions
    spec.invariants = merged_invariants + spec.invariants


def _rename_expr(expr: str, mapping: dict[str, str]) -> str:
    """Rename the state variables referenced by an expression."""
    from .expr import parse_expr

    tree = parse_expr(expr)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in mapping:
            node.id = mapping[node.id]
    return ast.unparse(tree)


def load_spec(path: str | Path) -> Spec:
    """Load and validate a spec from a YAML file."""
    p = Path(path)
    with open(p) as f:
        data = yaml.safe_load(f)
    if data is None:
        raise SpecError(f"{p}: empty spec file")
    return load_spec_data(data, str(p))


# --------------------------------------------------------------------------
# concrete semantics
# --------------------------------------------------------------------------


def initial_state(spec: Spec) -> dict[str, Any]:
    """Compute the initial state of a spec."""
    from .expr import eval_python

    state: dict[str, Any] = {}
    for name, var in spec.vars.items():
        t = var.type
        if name in spec.init:
            raw = spec.init[name]
            if isinstance(t, (MapType, SetType, SeqType)):
                state[name] = json_to_value(t, raw, f"init.{name}")
            elif isinstance(t, RecordType):
                rec: dict[str, Any] = {}
                for fname, ft in t.fields:
                    fval = raw[fname]
                    if isinstance(fval, str) and (
                        fval.strip().lower() in ("true", "false")
                        or _looks_like_expression(fval, spec.types)
                    ):
                        rec[fname] = eval_python(fval, dict(state), spec.types)
                    else:
                        rec[fname] = json_to_value(ft, fval, f"init.{name}.{fname}")
                state[name] = rec
            else:
                expr = _value_to_expr(raw, spec.types, f"init.{name}", expected=t)
                state[name] = eval_python(expr, dict(state), spec.types)
        else:
            state[name] = default_value(t)
    return state


def apply_action(spec: Spec, state: dict[str, Any], action_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """Apply an action to a state, returning the new state."""
    from .expr import eval_python

    action = spec.action(action_name)
    env = {**state, **params}
    for r in action.requires:
        if not eval_python(r, env, spec.tenv(action)):
            raise SpecError(f"precondition failed for {action_name}: {r}")
    nxt = dict(state)
    for k, e in action.effects.items():
        var = spec.vars[k]
        if isinstance(var.type, RecordType):
            rec = dict(state[k])
            for fname, fexpr in e.items():
                rec[fname] = eval_python(fexpr, env, spec.tenv(action))
            nxt[k] = rec
        else:
            nxt[k] = eval_python(e, env, spec.tenv(action))
    return nxt


def check_invariants(spec: Spec, state: dict[str, Any]) -> list[dict[str, str]]:
    """Return the invariants violated by a state."""
    from .expr import eval_python

    bad = []
    for inv in spec.invariants:
        if not eval_python(inv["expr"], dict(state), spec.types):
            bad.append(inv)
    return bad
