"""Bounded model checking of a VeriSpec spec with Z3.

The checker encodes the spec as a transition system and asks Z3 for a state
that violates an invariant.  Collections are encoded as Z3 arrays plus an
explicit size counter, so every read is total and no phantom counterexample
can appear.

The guarantee produced is *bounded*: "no violation up to bound K".  It is
never a proof for all depths, and the result says so explicitly.
"""

from __future__ import annotations

import ast
from typing import Any

import z3

from .expr import (
    SpecError,
    Z3Binding,
    Z3Map,
    Z3Record,
    Z3Seq,
    Z3Set,
    to_z3,
    z3_array_sort,
    z3_default,
    z3_sort_for,
)
from .model import Spec, initial_state
from .types import EnumType, MapType, RecordType, SeqType, SetType
from .values import value_to_json

__all__ = ["check", "format_result", "GUARANTEE_BOUNDED"]


GUARANTEE_BOUNDED = "bounded-proved(k)"


# --------------------------------------------------------------------------
# state encoding
# --------------------------------------------------------------------------


def _fresh(sort, prefix: str):
    """Create a solver symbol isolated from user-controlled names."""
    return z3.FreshConst(sort, prefix=f"__verispec_{prefix}")


def _slot(spec: Spec, var, step: int) -> dict[str, Any]:
    """Create the Z3 constants that represent one variable at one step."""
    t = var.type
    name = var.name
    prefix = f"state_{step}_{name}"
    if t.kind == "int":
        return {"kind": "scalar", "const": _fresh(z3.IntSort(), prefix)}
    if t.kind == "bool":
        return {"kind": "scalar", "const": _fresh(z3.BoolSort(), prefix)}
    if t.kind in ("str", "enum"):
        return {"kind": "scalar", "const": _fresh(z3.StringSort(), prefix)}
    if t.kind == "map":
        return {
            "kind": "map",
            "values": _fresh(z3_array_sort(z3.IntSort(), z3_sort_for(t.value)), f"{prefix}_values"),
            "present": _fresh(z3_array_sort(z3.IntSort(), z3.BoolSort()), f"{prefix}_present"),
            "size": _fresh(z3.IntSort(), f"{prefix}_size"),
        }
    if t.kind == "set":
        return {
            "kind": "set",
            "present": _fresh(z3_array_sort(z3.IntSort(), z3.BoolSort()), f"{prefix}_present"),
            "size": _fresh(z3.IntSort(), f"{prefix}_size"),
        }
    if t.kind == "seq":
        return {
            "kind": "seq",
            "items": _fresh(z3_array_sort(z3.IntSort(), z3_sort_for(t.elem)), f"{prefix}_items"),
            "size": _fresh(z3.IntSort(), f"{prefix}_size"),
        }
    if t.kind == "record":
        return {
            "kind": "record",
            "fields": {
                fname: _fresh(z3_sort_for(ft), f"{prefix}_{fname}") for fname, ft in t.fields
            },
        }
    raise SpecError(f"var {name}: unsupported type {t.kind}")


def _binding(var, slot: dict[str, Any]) -> Z3Binding:
    t = var.type
    if t.kind == "map":
        return Z3Binding(Z3Map(slot["values"], slot["present"], slot["size"], t), t)
    if t.kind == "set":
        return Z3Binding(Z3Set(slot["present"], slot["size"], t), t)
    if t.kind == "seq":
        return Z3Binding(Z3Seq(slot["items"], slot["size"], t), t)
    if t.kind == "record":
        return Z3Binding(Z3Record(slot["fields"], t), t)
    return Z3Binding(slot["const"], t)


def _unchanged(var, cur: dict[str, Any], nxt: dict[str, Any]) -> list:
    """Constraints saying that a variable does not change between two steps."""
    t = var.type
    if t.kind in ("int", "bool", "str", "enum"):
        return [nxt["const"] == cur["const"]]
    if t.kind == "map":
        return [
            nxt["values"] == cur["values"],
            nxt["present"] == cur["present"],
            nxt["size"] == cur["size"],
        ]
    if t.kind == "set":
        return [nxt["present"] == cur["present"], nxt["size"] == cur["size"]]
    if t.kind == "seq":
        return [nxt["items"] == cur["items"], nxt["size"] == cur["size"]]
    if t.kind == "record":
        return [nxt["fields"][f] == cur["fields"][f] for f in cur["fields"]]
    raise SpecError(f"var {var.name}: unsupported type {t.kind}")


def _domain_index_concrete(t, domain: tuple, key: Any) -> int | None:
    """Position of a concrete key/element inside a finite domain."""
    for i, item in enumerate(domain):
        if item == key and isinstance(item, bool) == isinstance(key, bool):
            return i
    return None


def _encode_init(solver, spec: Spec, states: list[dict[str, Any]]) -> None:
    """Constrain step 0 to the initial state."""
    _encode_state(solver, spec, initial_state(spec), states[0])


def _encode_state(solver, spec: Spec, state: dict[str, Any], slots: dict[str, Any]) -> None:
    """Constrain one step's slots to a concrete state."""
    init = state
    for name, var in spec.vars.items():
        t = var.type
        slot = slots[name]
        value = init[name]
        if t.kind == "int":
            solver.add(slot["const"] == z3.IntVal(int(value)))
        elif t.kind == "bool":
            solver.add(slot["const"] == z3.BoolVal(bool(value)))
        elif t.kind in ("str", "enum"):
            solver.add(slot["const"] == z3.StringVal(str(value)))
        elif t.kind == "map":
            values = z3.K(z3.IntSort(), z3_default(t.value))
            present = z3.K(z3.IntSort(), z3.BoolVal(False))
            for key, v in value.items():
                idx = _domain_index_concrete(t.key, t.domain, key)
                if idx is None:  # pragma: no cover - validated earlier
                    continue
                values = z3.Store(values, idx, _to_z3_value(t.value, v))
                present = z3.Store(present, idx, z3.BoolVal(True))
            solver.add(slot["values"] == values)
            solver.add(slot["present"] == present)
            solver.add(slot["size"] == z3.IntVal(len(value)))
        elif t.kind == "set":
            present = z3.K(z3.IntSort(), z3.BoolVal(False))
            for item in value:
                idx = _domain_index_concrete(t.elem, t.domain, item)
                if idx is None:  # pragma: no cover - validated earlier
                    continue
                present = z3.Store(present, idx, z3.BoolVal(True))
            solver.add(slot["present"] == present)
            solver.add(slot["size"] == z3.IntVal(len(value)))
        elif t.kind == "seq":
            items = z3.K(z3.IntSort(), z3_default(t.elem))
            for i, v in enumerate(value):
                items = z3.Store(items, i, _to_z3_value(t.elem, v))
            solver.add(slot["items"] == items)
            solver.add(slot["size"] == z3.IntVal(len(value)))
        elif t.kind == "record":
            for fname, ft in t.fields:
                solver.add(slot["fields"][fname] == _to_z3_value(ft, value[fname]))
        else:  # pragma: no cover - unreachable
            raise SpecError(f"var {name}: unsupported type {t.kind}")


def _to_z3_value(t, value: Any):
    """Encode a concrete value as a Z3 term of the right sort."""
    if t.kind == "int":
        return z3.IntVal(int(value))
    if t.kind == "bool":
        return z3.BoolVal(bool(value))
    if t.kind in ("str", "enum"):
        return z3.StringVal(str(value))
    raise SpecError(f"cannot encode {t.kind} as a Z3 value")


# --------------------------------------------------------------------------
# checking
# --------------------------------------------------------------------------


def check(
    spec: Spec,
    bound: int = 10,
    invariant_filter: str | None = None,
    timeout_ms: int = 30000,
    solver_memory_mb: int | None = None,
) -> dict[str, Any]:
    """Search for a state that violates an invariant, up to `bound` steps.

    Iterative deepening gives the shortest counterexample: the first bound at
    which a violation exists is the bound at which it is reported.
    """
    return _check_after_preflight(
        spec,
        bound,
        invariant_filter,
        timeout_ms,
        solver_memory_mb,
        _preflight(spec, bound, timeout_ms),
    )


def _check_after_preflight(
    spec: Spec,
    bound: int,
    invariant_filter: str | None,
    timeout_ms: int,
    solver_memory_mb: int | None,
    preflight: dict[str, Any] | None,
) -> dict[str, Any]:
    if preflight:
        return preflight
    return _check_after_preflight_rebuild(
        spec,
        bound,
        invariant_filter,
        timeout_ms,
        solver_memory_mb,
        None,
    )


def _check_after_preflight_rebuild(
    spec: Spec,
    bound: int,
    invariant_filter: str | None,
    timeout_ms: int,
    solver_memory_mb: int | None,
    preflight: dict[str, Any] | None,
) -> dict[str, Any]:
    """Reference deepening loop that rebuilds the solver at every bound (tests)."""
    if preflight:
        return preflight
    for k in range(0, bound + 1):
        r = _check_one(
            spec,
            bound=k,
            invariant_filter=invariant_filter,
            timeout_ms=timeout_ms,
            solver_memory_mb=solver_memory_mb,
        )
        if r["status"] == "violation":
            r["max_bound"] = bound
            return r
        if r["status"] == "unknown":
            r["max_bound"] = bound
            r["proved_up_to"] = max(k - 1, 0)
            if k > 0:
                r["message"] = (
                    f"no violation up to bound {k - 1}; "
                    f"the solver could not decide bound {k} ({r.get('reason', 'unknown')})"
                )
                r["guarantee"] = f"{GUARANTEE_BOUNDED.replace('(k)', f'({k - 1})')}"
            return r
    return {
        "status": "ok",
        "bound": bound,
        "max_bound": bound,
        "message": f"no violation up to bound {bound}",
        "guarantee": f"{GUARANTEE_BOUNDED.replace('(k)', f'({bound})')}",
    }


def _new_solver(timeout_ms: int, solver_memory_mb: int | None) -> z3.Solver:
    solver = z3.Solver()
    solver.set("timeout", timeout_ms)
    if solver_memory_mb is not None:
        solver.set("max_memory", solver_memory_mb)
    return solver


def _encode_enum_domains(solver, spec: Spec, state_slots: dict[str, Any]) -> None:
    for n, v in spec.vars.items():
        if isinstance(v.type, EnumType):
            solver.add(
                z3.Or(*[state_slots[n]["const"] == z3.StringVal(val) for val in v.type.values])
            )


def _encode_transition(
    solver,
    spec: Spec,
    states: list[dict[str, Any]],
    t: int,
    choice: Any,
    params_for_actions: dict[int, dict[str, Any]],
) -> None:
    nact = len(spec.actions)
    solver.add(choice >= -1, choice < nact)
    cur, nxt = states[t], states[t + 1]
    cur_bindings = {n: _binding(v, cur[n]) for n, v in spec.vars.items()}
    for ai, a in enumerate(spec.actions):
        penv: dict[str, Z3Binding] = {}
        action_params: dict[str, Any] = {}
        for pn, pv in a.params.items():
            sort = z3_sort_for(pv.type)
            pz = _fresh(sort, f"param_{t}_{ai}_{pn}")
            action_params[pn] = pz
            if pv.kind == "int":
                if pv.min is not None:
                    solver.add(pz >= pv.min)
                if pv.max is not None:
                    solver.add(pz <= pv.max)
            elif pv.kind == "enum":
                solver.add(z3.Or(*[pz == z3.StringVal(val) for val in pv.type.values]))
            penv[pn] = Z3Binding(pz, pv.type)
        params_for_actions[ai] = action_params
        env = {**cur_bindings, **penv}
        tenv = spec.tenv(a)
        req = (
            z3.And(*[to_z3(r, env, tenv) for r in a.requires])
            if a.requires
            else z3.BoolVal(True)
        )
        eff: list = []
        for vn, v in spec.vars.items():
            if vn not in a.effects:
                eff.extend(_unchanged(v, cur[vn], nxt[vn]))
                continue
            raw = a.effects[vn]
            if isinstance(v.type, RecordType):
                for fname, ft in v.type.fields:
                    if fname in raw:
                        val = to_z3(raw[fname], env, tenv, expected=ft)
                        eff.append(nxt[vn]["fields"][fname] == val)
                    else:
                        eff.append(nxt[vn]["fields"][fname] == cur[vn]["fields"][fname])
                continue
            val = to_z3(raw, env, tenv, expected=v.type)
            if v.type.kind == "map":
                assert isinstance(val, Z3Map)
                eff.append(nxt[vn]["values"] == val.values)
                eff.append(nxt[vn]["present"] == val.present)
                eff.append(nxt[vn]["size"] == val.size)
            elif v.type.kind == "set":
                assert isinstance(val, Z3Set)
                eff.append(nxt[vn]["present"] == val.present)
                eff.append(nxt[vn]["size"] == val.size)
            elif v.type.kind == "seq":
                assert isinstance(val, Z3Seq)
                eff.append(nxt[vn]["items"] == val.items)
                eff.append(nxt[vn]["size"] == val.size)
            else:
                eff.append(nxt[vn]["const"] == val)
        solver.add(z3.Implies(choice == ai, z3.And(req, *eff)))
    stutter: list = []
    for vn, v in spec.vars.items():
        stutter.extend(_unchanged(v, cur[vn], nxt[vn]))
    solver.add(z3.Implies(choice == -1, z3.And(*stutter)))


def _violation_terms(
    spec: Spec,
    states: list[dict[str, Any]],
    invs: list[dict[str, Any]],
    invariant_filter: str | None,
    upto_step: int,
) -> list:
    viols: list = []
    for t in range(upto_step + 1):
        st = states[t]
        bindings = {n: _binding(v, st[n]) for n, v in spec.vars.items()}
        for inv in invs:
            term = to_z3(inv["expr"], bindings, spec.types)
            viols.append(z3.Not(term))
        if invariant_filter:
            continue
        for n, v in spec.vars.items():
            if isinstance(v.type, (MapType, SetType, SeqType)):
                size = st[n]["size"]
                viols.append(size > v.type.max_size)
    return viols


def _result_from_solver(
    spec: Spec,
    solver: z3.Solver,
    bound: int,
    states: list[dict[str, Any]],
    choices: list[Any],
    params_by_step: list[dict[int, dict[str, Any]]],
    invariant_filter: str | None,
) -> dict[str, Any]:
    res = solver.check()
    if res == z3.unsat:
        return {
            "status": "ok",
            "bound": bound,
            "message": f"no violation up to bound {bound}",
            "guarantee": f"{GUARANTEE_BOUNDED.replace('(k)', f'({bound})')}",
        }
    if res != z3.sat:
        reason = solver.reason_unknown() or str(res)
        return {
            "status": "unknown",
            "bound": bound,
            "reason": reason,
            "message": f"solver returned {res} up to bound {bound} ({reason})",
            "guarantee": "unknown",
        }
    model = solver.model()
    trace = _decode_trace(spec, model, states, bound, choices, params_by_step)
    violations = _find_violations(spec, trace, invariant_filter=invariant_filter)
    return {
        "status": "violation",
        "bound": bound,
        "depth": len(trace) - 1,
        "trace": trace,
        "violated": [v["name"] for v in violations],
        "violations": violations,
        "message": (
            f"COUNTEREXAMPLE found within bound {bound}: invariant(s) violated: "
            + ", ".join(v["name"] for v in violations)
        ),
        "guarantee": "violation-found",
    }


def _check_after_preflight_incremental(
    spec: Spec,
    bound: int,
    invariant_filter: str | None,
    timeout_ms: int,
    solver_memory_mb: int | None,
) -> dict[str, Any]:
    """Iterative deepening with one Z3 solver; each step extends the encoding."""
    invs = [i for i in spec.invariants if not invariant_filter or i["name"] == invariant_filter]
    if invariant_filter and not invs:
        raise SpecError(f"invariant {invariant_filter!r} not found")

    solver = _new_solver(timeout_ms, solver_memory_mb)
    states: list[dict[str, Any]] = []
    choices: list[Any] = []
    params_by_step: list[dict[int, dict[str, Any]]] = []

    for k in range(0, bound + 1):
        if k == 0:
            states.append({n: _slot(spec, v, 0) for n, v in spec.vars.items()})
            _encode_enum_domains(solver, spec, states[0])
            _encode_init(solver, spec, states)
        else:
            states.append({n: _slot(spec, v, k) for n, v in spec.vars.items()})
            _encode_enum_domains(solver, spec, states[k])
            choice = _fresh(z3.IntSort(), f"action_{k - 1}")
            choices.append(choice)
            params_for_actions: dict[int, dict[str, Any]] = {}
            _encode_transition(solver, spec, states, k - 1, choice, params_for_actions)
            params_by_step.append(params_for_actions)

        viols = _violation_terms(spec, states, invs, invariant_filter, k)
        if not viols:
            continue
        solver.push()
        solver.add(z3.Or(*viols))
        r = _result_from_solver(
            spec, solver, k, states, choices, params_by_step, invariant_filter
        )
        solver.pop()
        if r["status"] == "violation":
            r["max_bound"] = bound
            return r
        if r["status"] == "unknown":
            r["max_bound"] = bound
            r["proved_up_to"] = max(k - 1, 0)
            if k > 0:
                r["message"] = (
                    f"no violation up to bound {k - 1}; "
                    f"the solver could not decide bound {k} ({r.get('reason', 'unknown')})"
                )
                r["guarantee"] = f"{GUARANTEE_BOUNDED.replace('(k)', f'({k - 1})')}"
            return r

    return {
        "status": "ok",
        "bound": bound,
        "max_bound": bound,
        "message": f"no violation up to bound {bound}",
        "guarantee": f"{GUARANTEE_BOUNDED.replace('(k)', f'({bound})')}",
    }


def _preflight(spec: Spec, bound: int, timeout_ms: int) -> dict[str, Any] | None:
    if isinstance(bound, bool) or not isinstance(bound, int) or bound < 0:
        raise SpecError("bound must be a non-negative integer")
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or timeout_ms <= 0:
        raise SpecError("timeout_ms must be a positive integer")

    try:
        initial_state(spec)
    except ZeroDivisionError as error:
        raise SpecError("division by zero while evaluating the initial state") from error

    problem = _division_problem(spec)
    if problem is None:
        return None
    status, message = problem
    if status == "invalid":
        raise SpecError(message)
    return {
        "status": "unknown",
        "bound": bound,
        "max_bound": bound,
        "reason": "division-by-zero semantics",
        "message": message,
        "guarantee": "unknown",
    }


def _constant_int(node: ast.AST) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _constant_int(node.operand)
        if value is not None:
            return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult)):
        left, right = _constant_int(node.left), _constant_int(node.right)
        if left is not None and right is not None:
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            return left * right
    return None


def _division_problem(spec: Spec) -> tuple[str, str] | None:
    """Reject literal zero divisors and fail closed on unbounded symbolic ones."""
    from .expr import parse_expr

    expressions: list[str] = []
    for action in spec.actions:
        expressions.extend(action.requires)
        for effect in action.effects.values():
            if isinstance(effect, dict):
                expressions.extend(str(value) for value in effect.values())
            else:
                expressions.append(str(effect))
    expressions.extend(str(inv["expr"]) for inv in spec.invariants)

    for expression in expressions:
        try:
            tree = parse_expr(expression)
        except SpecError:
            continue  # normal validation reports malformed expressions
        for node in ast.walk(tree):
            if not isinstance(node, ast.BinOp) or not isinstance(node.op, (ast.FloorDiv, ast.Mod)):
                continue
            divisor = _constant_int(node.right)
            if divisor == 0:
                return ("invalid", f"division by zero in expression {expression!r}")
            if divisor is None:
                return (
                    "unknown",
                    "bounded checking cannot prove a symbolic division or modulo divisor "
                    f"nonzero in {expression!r}; refusing to report a proof",
                )
    return None


def _check_one(
    spec: Spec,
    bound: int = 10,
    invariant_filter: str | None = None,
    timeout_ms: int = 30000,
    solver_memory_mb: int | None = None,
) -> dict[str, Any]:
    invs = [i for i in spec.invariants if not invariant_filter or i["name"] == invariant_filter]
    if invariant_filter and not invs:
        raise SpecError(f"invariant {invariant_filter!r} not found")
    solver = _new_solver(timeout_ms, solver_memory_mb)

    states: list[dict[str, Any]] = []
    for t in range(bound + 1):
        states.append({n: _slot(spec, v, t) for n, v in spec.vars.items()})

    for st in states:
        _encode_enum_domains(solver, spec, st)

    _encode_init(solver, spec, states)

    choices: list[Any] = []
    params_by_step: list[dict[int, dict[str, Any]]] = []
    for t in range(bound):
        choice = _fresh(z3.IntSort(), f"action_{t}")
        choices.append(choice)
        params_for_actions: dict[int, dict[str, Any]] = {}
        _encode_transition(solver, spec, states, t, choice, params_for_actions)
        params_by_step.append(params_for_actions)

    viols = _violation_terms(spec, states, invs, invariant_filter, bound)
    if viols:
        solver.add(z3.Or(*viols))
    return _result_from_solver(
        spec, solver, bound, states, choices, params_by_step, invariant_filter
    )


def _decode_trace(spec: Spec, model, states, bound: int, choices, params_by_step) -> list[dict[str, Any]]:
    """Turn a Z3 model into a concrete counterexample trace."""
    nact = len(spec.actions)
    trace: list[dict[str, Any]] = []
    for t in range(bound + 1):
        state = _decode_state(spec, model, states[t])
        entry: dict[str, Any] = {"step": t, "state": state}
        if t < bound:
            ch = model.eval(choices[t], model_completion=True).as_long()
            if 0 <= ch < nact:
                a = spec.actions[ch]
                params: dict[str, Any] = {}
                for pn, pv in a.params.items():
                    pz = params_by_step[t][ch][pn]
                    params[pn] = _decode_scalar(pv.type, model.eval(pz, model_completion=True))
                entry["action"] = a.name
                entry["params"] = params
            else:
                entry["action"] = "(stutter)"
                entry["params"] = {}
        trace.append(entry)
    return trace


def _decode_state(spec: Spec, model, slots: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, var in spec.vars.items():
        t = var.type
        slot = slots[name]
        if t.kind in ("int", "bool", "str", "enum"):
            out[name] = _decode_scalar(t, model.eval(slot["const"], model_completion=True))
        elif t.kind == "map":
            out[name] = _decode_map(t, slot, model)
        elif t.kind == "set":
            out[name] = _decode_set(t, slot, model)
        elif t.kind == "seq":
            out[name] = _decode_seq(t, slot, model)
        elif t.kind == "record":
            out[name] = {
                fname: _decode_scalar(ft, model.eval(slot["fields"][fname], model_completion=True))
                for fname, ft in t.fields
            }
        else:  # pragma: no cover - unreachable
            raise SpecError(f"var {name}: unsupported type {t.kind}")
    return out


def _decode_scalar(t, val):
    if t.kind == "int":
        return val.as_long()
    if t.kind == "bool":
        return z3.is_true(val)
    return val.as_string()


def _decode_map(t: MapType, slot, model) -> dict:
    out: dict[Any, Any] = {}
    for i, key in enumerate(t.domain):
        present = model.eval(z3.Select(slot["present"], z3.IntVal(i)), model_completion=True)
        if not z3.is_true(present):
            continue
        value = model.eval(z3.Select(slot["values"], z3.IntVal(i)), model_completion=True)
        out[key] = _decode_scalar(t.value, value)
    return out


def _decode_set(t: SetType, slot, model) -> set:
    out: set[Any] = set()
    for i, item in enumerate(t.domain):
        present = model.eval(z3.Select(slot["present"], z3.IntVal(i)), model_completion=True)
        if z3.is_true(present):
            out.add(item)
    return out


def _decode_seq(t: SeqType, slot, model) -> list:
    size = model.eval(slot["size"], model_completion=True).as_long()
    out = []
    for i in range(min(size, t.max_size + 1)):
        value = model.eval(z3.Select(slot["items"], z3.IntVal(i)), model_completion=True)
        out.append(_decode_scalar(t.elem, value))
    return out


def _find_violations(
    spec: Spec, trace: list[dict[str, Any]], invariant_filter: str | None = None
) -> list[dict[str, Any]]:
    """Which invariants (or capacity bounds) the trace breaks, and where."""
    from .model import check_invariants

    found: list[dict[str, Any]] = []
    for entry in trace:
        state = entry["state"]
        for inv in check_invariants(spec, state):
            if invariant_filter and inv["name"] != invariant_filter:
                continue
            found.append(
                {
                    "name": inv["name"],
                    "kind": "invariant",
                    "expr": inv["expr"],
                    "step": entry["step"],
                    "state": value_to_json(spec.types, state),
                }
            )
        if invariant_filter:
            continue
        for name, var in spec.vars.items():
            if isinstance(var.type, (MapType, SetType, SeqType)):
                size = len(state[name])
                if size > var.type.max_size:
                    found.append(
                        {
                            "name": f"{name}_capacity",
                            "kind": "bound",
                            "expr": f"len({name}) <= {var.type.max_size}",
                            "step": entry["step"],
                            "state": value_to_json(spec.types, state),
                        }
                    )
    if not found and trace:
        found.append(
            {
                "name": "unknown",
                "kind": "unknown",
                "expr": "",
                "step": trace[-1]["step"],
                "state": value_to_json(spec.types, trace[-1]["state"]),
            }
        )
    return found


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------


def format_result(spec: Spec, result: dict[str, Any]) -> str:
    """Human readable rendering of a check result."""
    if result["status"] == "ok":
        return (
            f"{result['message']}  (bounded claim only, not a proof for all depths; "
            f"guarantee: {result.get('guarantee', GUARANTEE_BOUNDED)})"
        )
    if result["status"] != "violation":
        return f"{result['message']}  (guarantee: {result.get('guarantee', 'unknown')})"
    lines = [result["message"]]
    for entry in result.get("trace", []):
        lines.append(f"  step {entry['step']}: state={entry['state']}")
        if "action" in entry:
            lines.append(f"    -> action {entry['action']} params={entry.get('params', {})}")
    for v in result.get("violations", []):
        lines.append(f"  violated at step {v['step']}: {v['name']} ({v['kind']}) {v['expr']}")
    lines.append(
        "  This is a counterexample: reproduce it, fix the implementation, re-run. "
        "Do not weaken the spec."
    )
    return "\n".join(lines)
