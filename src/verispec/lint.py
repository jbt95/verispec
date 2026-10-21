"""Spec linting: catch specs that cannot fail.

A verified spec is only as good as the spec.  The plan calls this out
explicitly: "a bad spec, verified, is still a bad spec".  The linter looks
for the usual suspects:

* no invariants at all,
* tautological invariants,
* actions whose preconditions can never hold,
* actions that change nothing,
* state or parameters without bounds.
"""

from __future__ import annotations

import ast
import re
from typing import Any

from .expr import parse_expr
from .model import Spec

__all__ = ["lint_spec"]

_TAUTOLOGY = re.compile(r"^\s*(true|1\s*==\s*1|not\s+false)\s*$", re.IGNORECASE)


def _string_constants(expression: str) -> list[str]:
    """The string literals in an expression."""
    try:
        tree = parse_expr(expression)
    except Exception:
        return []
    return [
        node.value
        for node in ast.walk(tree.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def _check_quoted_parameters(spec: Spec, action, add) -> None:
    """Catch a string literal that was meant to be a parameter.

    `"old_tag"` and `old_tag` differ by two characters, and the difference
    is invisible in the failure: the expression simply compares against the
    string "old_tag" instead of the parameter's value.  The contract is
    still valid - it just says something else from what was meant.
    """
    if not action.params:
        return
    names = set(action.params)
    expressions = list(action.requires) + [str(e) for e in action.effects.values()]
    for expression in expressions:
        for literal in _string_constants(expression):
            if literal in names:
                add(
                    "warning",
                    "quoted-parameter",
                    f'action {action.name}: the string "{literal}" looks like the '
                    f"parameter {literal}: if you meant the parameter, drop the quotes",
                    action.name,
                )


def lint_spec(spec: Spec) -> dict[str, Any]:
    """Lint a spec and return the findings."""
    findings: list[dict[str, Any]] = []

    def add(level: str, code: str, message: str, where: str = "") -> None:
        findings.append({"level": level, "code": code, "message": message, "where": where})

    if not spec.invariants:
        add(
            "error",
            "no-invariants",
            "the spec declares no invariants, so there is nothing to verify",
        )
    if not spec.actions:
        add(
            "warning",
            "no-actions",
            "the spec declares no actions; only the initial state can be checked",
        )
    if not spec.declared_version:
        add(
            "info",
            "no-spec-version",
            "no spec_version field: the spec is treated as format v0.1; "
            "run `verispec migrate` to move it to v1.0",
        )

    for inv in spec.invariants:
        expr = inv["expr"].strip()
        if _is_tautology(expr):
            add(
                "warning",
                "trivial-invariant",
                f"invariant {inv['name']} looks tautological and can never fail: {expr}",
                inv["name"],
            )
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", expr) and expr in spec.vars:
            add(
                "warning",
                "trivial-invariant",
                f"invariant {inv['name']} only mentions the variable {expr}",
                inv["name"],
            )

    for a in spec.actions:
        _check_quoted_parameters(spec, a, add)
        if not a.effects:
            if a.stutter:
                # The action is meant to change nothing, and says so.
                add(
                    "info",
                    "stutter",
                    f"action {a.name} is a stutter: it changes nothing on purpose",
                    a.name,
                )
            else:
                add(
                    "warning",
                    "no-effects",
                    f"action {a.name} has no effects: it cannot change the state "
                    f"(mark it 'stutter: true' if that is on purpose)",
                    a.name,
                )
        elif not a.requires:
            add(
                "info",
                "no-precondition",
                f"action {a.name} has no precondition: it is enabled in every state",
                a.name,
            )
        for pn, pv in a.params.items():
            if pv.kind == "int" and (pv.min is None or pv.max is None):
                add(
                    "info",
                    "unbounded-param",
                    f"parameter {pn} of action {a.name} has no min/max: the generated "
                    "tests will draw values from a default range",
                    a.name,
                )
        if a.requires and _never_enabled(spec, a):
            add(
                "warning",
                "never-enabled",
                f"action {a.name} can never be enabled: its preconditions are "
                "unsatisfiable in every state",
                a.name,
            )

    for n, v in spec.vars.items():
        if v.kind == "int" and (v.min is None or v.max is None):
            add(
                "info",
                "unbounded-var",
                f"state variable {n} has no min/max: the checker explores every "
                "integer value the transitions can produce",
                n,
            )
        if n not in spec.init:
            add(
                "info",
                "default-init",
                f"state variable {n} has no initial value: the default is used",
                n,
            )

    errors = [f for f in findings if f["level"] == "error"]
    return {
        "ok": not errors,
        "findings": findings,
        "errors": len(errors),
        "warnings": len([f for f in findings if f["level"] == "warning"]),
        "infos": len([f for f in findings if f["level"] == "info"]),
    }


def _is_tautology(expr: str) -> bool:
    if _TAUTOLOGY.match(expr):
        return True
    try:
        tree = parse_expr(expr)
    except Exception:
        return False
    node = tree.body
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        op = node.ops[0]
        left = _render(node.left)
        right = _render(node.comparators[0])
        if left == right:
            if isinstance(op, (ast.Eq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
                return True
        if isinstance(op, ast.NotEq) and left != right:
            return False
    return False


def _render(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - defensive
        return ""


def _never_enabled(spec: Spec, action) -> bool:
    """Can the preconditions of an action hold in any state at all?"""
    import z3

    from .expr import Z3Binding, to_z3
    from .types import EnumType

    solver = z3.Solver()
    env: dict[str, Z3Binding] = {}
    for n, v in spec.vars.items():
        t = v.type
        if t.kind == "int":
            const = z3.Int(f"lint_{n}")
        elif t.kind == "bool":
            const = z3.Bool(f"lint_{n}")
        else:
            const = z3.String(f"lint_{n}")
        env[n] = Z3Binding(const, t)
        if isinstance(t, EnumType):
            solver.add(z3.Or(*[const == z3.StringVal(val) for val in t.values]))
    for pn, pv in action.params.items():
        if pv.kind == "int":
            const = z3.Int(f"lint_{action.name}_{pn}")
        elif pv.kind == "bool":
            const = z3.Bool(f"lint_{action.name}_{pn}")
        else:
            const = z3.String(f"lint_{action.name}_{pn}")
        env[pn] = Z3Binding(const, pv.type)
        if isinstance(pv.type, EnumType):
            solver.add(z3.Or(*[const == z3.StringVal(val) for val in pv.type.values]))
    try:
        for r in action.requires:
            solver.add(to_z3(r, env, spec.tenv(action)))
    except Exception:
        return False
    return solver.check() == z3.unsat
