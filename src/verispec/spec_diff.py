"""Semantic diff between two spec versions."""

from __future__ import annotations

from typing import Any

import z3

from .model import Spec
from .spec_semantic import _make_state_solver, _new_solver
from .expr import to_z3

__all__ = ["spec_diff"]


def _implies(base_expr: str, cur_expr: str, spec: Spec, timeout_ms: int = 10000) -> bool | None:
    try:
        solver, slots, bindings = _make_state_solver(spec, timeout_ms)
    except Exception:
        return None
    try:
        bt = to_z3(base_expr, bindings, spec.types)
        ct = to_z3(cur_expr, bindings, spec.types)
    except Exception:
        return None
    # base => cur ?
    s2 = _new_solver(timeout_ms, None)
    # reuse constraints? _make_state_solver already added domain constraints to solver; replicate by pushing on same solver
    solver.push()
    solver.add(bt)
    solver.add(z3.Not(ct))
    res = solver.check()
    solver.pop()
    if res == z3.unsat:
        return True
    if res == z3.sat:
        return False
    return None


def spec_diff(current: Spec, base: Spec) -> dict[str, Any]:
    base_invs = {inv["name"]: inv for inv in base.invariants}
    cur_invs = {inv["name"]: inv for inv in current.invariants}
    added = sorted(set(cur_invs) - set(base_invs))
    removed = sorted(set(base_invs) - set(cur_invs))
    changed = []
    weakened = []
    strengthened = []
    for name in sorted(set(base_invs) & set(cur_invs)):
        be = base_invs[name].get("expr")
        ce = cur_invs[name].get("expr")
        if be == ce:
            continue
        # base (old) vs current (new)
        base_implies_cur = _implies(be, ce, base)
        cur_implies_base = _implies(ce, be, base)
        if base_implies_cur is True and cur_implies_base is False:
            change = "weakened"
            weakened.append(name)
        elif cur_implies_base is True and base_implies_cur is False:
            change = "strengthened"
            strengthened.append(name)
        elif base_implies_cur is True and cur_implies_base is True:
            change = "equivalent"
        else:
            change = "modified"
        changed.append({"name": name, "change": change,
                        "base_expr": be, "current_expr": ce})
    # overall relation
    try:
        from .spec_semantic import run_reference_compare
        ref = run_reference_compare(current, base, bound=6, timeout_ms=10000)
        relation = ref.relation
    except Exception:
        relation = "unknown"
    return {
        "relation": relation,
        "added": added,
        "removed": removed,
        "changed": changed,
        "weakened": weakened,
        "strengthened": strengthened,
    }

diff_specs = spec_diff
spec_diff_report = spec_diff
