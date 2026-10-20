"""Temporal (liveness) properties and fairness for VeriSpec specs.

Safety invariants are checked in ``bmc``; this module handles:

* parsing ``fairness`` and ``temporal`` sections from a spec,
* bounded liveness model checking via lasso-shaped counterexamples,
* finite-trace monitoring with *pending* obligations at trace end, and
* TLA+ fragments for temporal formulas and fairness assumptions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import z3

from .expr import SpecError, Z3Binding, eval_python, to_z3
from .model import Spec
from .types import EnumType, RecordType
from .values import value_to_json

__all__ = [
    "FairnessSpec",
    "TemporalProperty",
    "GUARANTEE_BOUNDED_LIVENESS",
    "GUARANTEE_PENDING",
    "parse_fairness",
    "parse_temporal_list",
    "validate_temporal",
    "check_temporal_bounded",
    "check_temporal_on_trace",
    "format_liveness_result",
    "merge_check_with_liveness",
    "tla_fairness_lines",
    "tla_temporal_lines",
]

GUARANTEE_BOUNDED_LIVENESS = "bounded-liveness-proved(k)"
GUARANTEE_PENDING = "pending-obligation"


@dataclass(frozen=True)
class FairnessSpec:
    weak: tuple[str, ...]
    strong: tuple[str, ...]


@dataclass(frozen=True)
class TemporalProperty:
    name: str
    kind: Literal["eventually", "leads_to"]
    goal: str
    when: str | None = None


def parse_fairness(raw: Any) -> FairnessSpec:
    if raw is None:
        return FairnessSpec(weak=(), strong=())
    if not isinstance(raw, dict):
        raise SpecError("fairness must be a mapping with optional weak and strong lists")
    weak = _action_name_list(raw.get("weak"), "fairness.weak")
    strong = _action_name_list(raw.get("strong"), "fairness.strong")
    overlap = set(weak) & set(strong)
    if overlap:
        raise SpecError(f"fairness: action(s) listed in both weak and strong: {sorted(overlap)}")
    return FairnessSpec(weak=tuple(weak), strong=tuple(strong))


def parse_temporal_list(raw: Any) -> list[TemporalProperty]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SpecError("temporal must be a list of property mappings")
    out: list[TemporalProperty] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise SpecError(f"temporal[{i}]: must be a mapping")
        name = item.get("name")
        if not name or not isinstance(name, str):
            raise SpecError(f"temporal[{i}]: missing or invalid name")
        kind = item.get("kind")
        if kind not in ("eventually", "leads_to"):
            raise SpecError(
                f"temporal property {name!r}: kind must be 'eventually' or 'leads_to', got {kind!r}"
            )
        goal = item.get("goal") or item.get("expr")
        if not goal or not isinstance(goal, str):
            raise SpecError(f"temporal property {name!r}: missing goal (boolean expression)")
        when = item.get("when")
        if when is not None and not isinstance(when, str):
            raise SpecError(f"temporal property {name!r}: when must be a string expression")
        if kind == "leads_to" and not when:
            raise SpecError(f"temporal property {name!r}: leads_to requires a when expression")
        out.append(TemporalProperty(name=name, kind=kind, goal=goal, when=when))
    return out


def validate_temporal(spec: Spec, props: list[TemporalProperty], fairness: FairnessSpec) -> None:
    from .model import _type_check

    names: set[str] = set()
    for prop in props:
        if prop.name in names:
            raise SpecError(f"duplicate temporal property {prop.name!r}")
        names.add(prop.name)
        t = _type_check(prop.goal, spec.types, f"temporal {prop.name} goal")
        if t.kind != "bool":
            raise SpecError(f"temporal {prop.name}: goal must be boolean, got {t.kind}")
        if prop.when:
            tw = _type_check(prop.when, spec.types, f"temporal {prop.name} when")
            if tw.kind != "bool":
                raise SpecError(f"temporal {prop.name}: when must be boolean, got {tw.kind}")
    for label, actions in (("weak", fairness.weak), ("strong", fairness.strong)):
        for action in actions:
            if not spec.has_action(action):
                raise SpecError(f"fairness.{label}: unknown action {action!r}")


def _action_name_list(raw: Any, where: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SpecError(f"{where} must be a list of action names")
    out: list[str] = []
    for i, name in enumerate(raw):
        if not isinstance(name, str) or not name:
            raise SpecError(f"{where}[{i}]: must be a non-empty action name string")
        out.append(name)
    return out


# --------------------------------------------------------------------------
# finite traces
# --------------------------------------------------------------------------


def check_temporal_on_trace(spec: Spec, trace: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluate temporal properties on a finite trace.

    Returns satisfied properties, pending obligations (not yet discharged at
    trace end), and hard violations (none for the current semantics).
    """
    props = spec.temporal
    if not props:
        return {
            "status": "ok",
            "satisfied": [],
            "pending": [],
            "violations": [],
            "message": "no temporal properties declared",
        }
    states = [step["state"] for step in trace]
    satisfied: list[str] = []
    pending: list[dict[str, Any]] = []
    for prop in props:
        outcome = _trace_outcome(spec, prop, states)
        if outcome == "satisfied":
            satisfied.append(prop.name)
        elif outcome == "pending":
            pending.append(
                {
                    "name": prop.name,
                    "kind": prop.kind,
                    "goal": prop.goal,
                    "when": prop.when,
                    "guarantee": GUARANTEE_PENDING,
                    "step": len(states) - 1,
                }
            )
    if pending:
        return {
            "status": "pending",
            "satisfied": satisfied,
            "pending": pending,
            "violations": [],
            "message": (
                f"trace ended with {len(pending)} pending temporal obligation(s): "
                + ", ".join(p["name"] for p in pending)
            ),
            "guarantee": GUARANTEE_PENDING,
        }
    return {
        "status": "ok",
        "satisfied": satisfied,
        "pending": [],
        "violations": [],
        "message": f"all {len(satisfied)} temporal property(ies) satisfied on trace",
        "guarantee": "conformance-tested",
    }


def _trace_outcome(spec: Spec, prop: TemporalProperty, states: list[dict[str, Any]]) -> str:
    types = spec.types
    if prop.kind == "eventually":
        for state in states:
            if eval_python(prop.goal, dict(state), types):
                return "satisfied"
        return "pending"
    # leads_to: always (when -> eventually goal)
    obligation_from: int | None = None
    for i, state in enumerate(states):
        env = dict(state)
        if prop.when and eval_python(prop.when, env, types):
            if obligation_from is None:
                obligation_from = i
        if obligation_from is not None and eval_python(prop.goal, env, types):
            obligation_from = None
    if obligation_from is not None:
        return "pending"
    return "satisfied"


# --------------------------------------------------------------------------
# bounded liveness (lasso BMC)
# --------------------------------------------------------------------------


def check_temporal_bounded(
    spec: Spec,
    bound: int = 10,
    property_filter: str | None = None,
    timeout_ms: int = 30000,
) -> dict[str, Any]:
    """Search for lasso counterexamples to temporal properties up to ``bound``."""
    props = [p for p in spec.temporal if not property_filter or p.name == property_filter]
    if property_filter and not props:
        raise SpecError(f"temporal property {property_filter!r} not found")
    if not props:
        return {
            "status": "ok",
            "bound": bound,
            "message": "no temporal properties to check",
            "guarantee": GUARANTEE_BOUNDED_LIVENESS.replace("(k)", f"({bound})"),
        }
    for k in range(2, bound + 1):
        for prop in props:
            r = _check_liveness_one(spec, prop, bound=k, timeout_ms=timeout_ms)
            if r["status"] == "violation":
                r["max_bound"] = bound
                return r
            if r["status"] == "unknown":
                r["max_bound"] = bound
                return r
    return {
        "status": "ok",
        "bound": bound,
        "max_bound": bound,
        "message": f"no liveness counterexample up to bound {bound}",
        "guarantee": GUARANTEE_BOUNDED_LIVENESS.replace("(k)", f"({bound})"),
    }


def _check_liveness_one(
    spec: Spec,
    prop: TemporalProperty,
    bound: int,
    timeout_ms: int,
) -> dict[str, Any]:
    from .bmc import (
        _binding,
        _encode_init,
        _fresh,
        _slot,
        _unchanged,
    )

    solver = z3.Solver()
    solver.set("timeout", timeout_ms)
    states: list[dict[str, Any]] = []
    for t in range(bound + 1):
        states.append({n: _slot(spec, v, t) for n, v in spec.vars.items()})

    for st in states:
        for n, v in spec.vars.items():
            if isinstance(v.type, EnumType):
                solver.add(z3.Or(*[st[n]["const"] == z3.StringVal(val) for val in v.type.values]))

    _encode_init(solver, spec, states)
    nact = len(spec.actions)
    choices: list[Any] = []
    params_by_step: list[dict[int, dict[str, Any]]] = []
    enabled_by_step: list[dict[str, z3.BoolRef]] = []

    for t in range(bound):
        choice = _fresh(z3.IntSort(), f"liveness_action_{t}")
        choices.append(choice)
        params_for_actions: dict[int, dict[str, Any]] = {}
        enabled_at_t: dict[str, z3.BoolRef] = {}
        solver.add(choice >= -1, choice < nact)
        cur, nxt = states[t], states[t + 1]
        cur_bindings = {n: _binding(v, cur[n]) for n, v in spec.vars.items()}
        for ai, a in enumerate(spec.actions):
            penv: dict[str, Z3Binding] = {}
            action_params: dict[str, Any] = {}
            for pn, pv in a.params.items():
                sort = _z3_sort_for(pv.type)
                pz = _fresh(sort, f"liveness_param_{t}_{ai}_{pn}")
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
            enabled_at_t[a.name] = req
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
                    eff.append(nxt[vn]["values"] == val.values)
                    eff.append(nxt[vn]["present"] == val.present)
                    eff.append(nxt[vn]["size"] == val.size)
                elif v.type.kind == "set":
                    eff.append(nxt[vn]["present"] == val.present)
                    eff.append(nxt[vn]["size"] == val.size)
                elif v.type.kind == "seq":
                    eff.append(nxt[vn]["items"] == val.items)
                    eff.append(nxt[vn]["size"] == val.size)
                else:
                    eff.append(nxt[vn]["const"] == val)
            solver.add(z3.Implies(choice == ai, z3.And(req, *eff)))
        params_by_step.append(params_for_actions)
        enabled_by_step.append(enabled_at_t)
        stutter: list = []
        for vn, v in spec.vars.items():
            stutter.extend(_unchanged(v, cur[vn], nxt[vn]))
        solver.add(z3.Implies(choice == -1, z3.And(*stutter)))

    # invariants on every step
    for t, st in enumerate(states):
        bindings = {n: _binding(v, st[n]) for n, v in spec.vars.items()}
        for inv in spec.invariants:
            solver.add(to_z3(inv["expr"], bindings, spec.types))

    lasso_cases: list[z3.BoolRef] = []
    for ls in range(bound):
        for le in range(ls + 1, bound + 1):
            same = z3.And(
                *[_state_equal(v, states[ls][n], states[le][n]) for n, v in spec.vars.items()]
            )
            fair = _fairness_on_loop(spec, ls, le, choices, enabled_by_step)
            viol = _liveness_violation_z3(spec, prop, states, bound)
            lasso_cases.append(z3.And(same, fair, viol))
    if lasso_cases:
        solver.add(z3.Or(*lasso_cases))

    res = solver.check()
    if res == z3.unsat:
        return {
            "status": "ok",
            "bound": bound,
            "property": prop.name,
            "message": f"no liveness counterexample for {prop.name} up to bound {bound}",
            "guarantee": GUARANTEE_BOUNDED_LIVENESS.replace("(k)", f"({bound})"),
        }
    if res != z3.sat:
        reason = solver.reason_unknown() or str(res)
        return {
            "status": "unknown",
            "bound": bound,
            "property": prop.name,
            "reason": reason,
            "message": f"solver returned {res} for {prop.name} at bound {bound} ({reason})",
            "guarantee": "unknown",
        }

    from .bmc import _decode_trace

    model = solver.model()
    trace = _decode_trace(spec, model, states, bound, choices, params_by_step)
    ls, le = _decode_lasso_pair(spec, model, states, bound)
    return {
        "status": "violation",
        "bound": bound,
        "depth": len(trace) - 1,
        "property": prop.name,
        "temporal_kind": prop.kind,
        "trace": trace,
        "lasso": {"loop_start": ls, "loop_end": le},
        "violated": [prop.name],
        "violations": [
            {
                "name": prop.name,
                "kind": "temporal",
                "expr": _temporal_expr_text(prop),
                "step": le,
                "lasso": {"loop_start": ls, "loop_end": le},
                "state": value_to_json(spec.types, trace[le]["state"]),
            }
        ],
        "message": (
            f"LIVENESS COUNTEREXAMPLE for {prop.name} within bound {bound} (lasso steps {ls}..{le})"
        ),
        "guarantee": "violation-found",
    }


def _z3_sort_for(t):
    from .expr import z3_sort_for

    return z3_sort_for(t)


def _state_equal(var, left: dict[str, Any], right: dict[str, Any]) -> z3.BoolRef:
    t = var.type
    if t.kind in ("int", "bool", "str", "enum"):
        return left["const"] == right["const"]
    if t.kind == "map":
        return z3.And(
            left["values"] == right["values"],
            left["present"] == right["present"],
            left["size"] == right["size"],
        )
    if t.kind == "set":
        return z3.And(left["present"] == right["present"], left["size"] == right["size"])
    if t.kind == "seq":
        return z3.And(left["items"] == right["items"], left["size"] == right["size"])
    if t.kind == "record":
        return z3.And(*[left["fields"][f] == right["fields"][f] for f in left["fields"]])
    raise SpecError(f"var {var.name}: unsupported type {t.kind}")


def _liveness_violation_z3(
    spec: Spec,
    prop: TemporalProperty,
    states: list[dict[str, Any]],
    bound: int,
) -> z3.BoolRef:
    """Constraint: the temporal property fails on the bounded prefix (lasso repeats it)."""
    if prop.kind == "eventually":
        terms = []
        for t in range(bound + 1):
            bindings = {n: _binding_from_slot(spec.vars[n], states[t][n]) for n in spec.vars}
            terms.append(z3.Not(to_z3(prop.goal, bindings, spec.types)))
        return z3.And(*terms)
    triggers: list[z3.BoolRef] = []
    for t in range(bound + 1):
        bindings = {n: _binding_from_slot(spec.vars[n], states[t][n]) for n in spec.vars}
        when = to_z3(prop.when or "false", bindings, spec.types)
        never = z3.And(
            *[
                z3.Not(
                    to_z3(
                        prop.goal,
                        {n: _binding_from_slot(spec.vars[n], states[u][n]) for n in spec.vars},
                        spec.types,
                    )
                )
                for u in range(t, bound + 1)
            ]
        )
        triggers.append(z3.And(when, never))
    return z3.Or(*triggers)


def _binding_from_slot(var, slot: dict[str, Any]) -> Z3Binding:
    from .bmc import _binding

    return _binding(var, slot)


def _fairness_on_loop(
    spec: Spec,
    loop_start: int,
    loop_end: int,
    choices: list[Any],
    enabled_by_step: list[dict[str, z3.BoolRef]],
) -> z3.BoolRef:
    """Weak/strong fairness on transitions loop_start .. loop_end-1 (bounded approximation)."""
    if not spec.fairness.weak and not spec.fairness.strong:
        return z3.BoolVal(True)
    action_index = {a.name: i for i, a in enumerate(spec.actions)}
    loop_transitions = list(range(loop_start, min(loop_end, len(choices))))
    if not loop_transitions:
        return z3.BoolVal(True)
    constraints: list[z3.BoolRef] = []
    for action_name in spec.fairness.weak:
        always_enabled = z3.And(*[enabled_by_step[t][action_name] for t in loop_transitions])
        taken = z3.Or(
            *[
                choices[t] == action_index[action_name]
                for t in loop_transitions
                if action_name in action_index
            ]
        )
        constraints.append(z3.Implies(always_enabled, taken))
    for action_name in spec.fairness.strong:
        any_enabled = z3.Or(*[enabled_by_step[t][action_name] for t in loop_transitions])
        taken = z3.Or(
            *[
                choices[t] == action_index[action_name]
                for t in loop_transitions
                if action_name in action_index
            ]
        )
        constraints.append(z3.Implies(any_enabled, taken))
    return z3.And(*constraints) if constraints else z3.BoolVal(True)


def _decode_lasso_pair(spec: Spec, model, states, bound: int) -> tuple[int, int]:
    for ls in range(bound):
        for le in range(ls + 1, bound + 1):
            same = z3.And(
                *[_state_equal(v, states[ls][n], states[le][n]) for n, v in spec.vars.items()]
            )
            if z3.is_true(model.eval(same, model_completion=True)):
                return ls, le
    return 0, bound


def _temporal_expr_text(prop: TemporalProperty) -> str:
    if prop.kind == "eventually":
        return f"eventually ({prop.goal})"
    return f"always ({prop.when} -> eventually ({prop.goal}))"


def merge_check_with_liveness(safety: dict[str, Any], liveness: dict[str, Any]) -> dict[str, Any]:
    """Combine safety BMC output with a temporal liveness result."""
    if safety.get("status") != "ok":
        return safety
    if liveness.get("status") == "ok" and not liveness.get("property"):
        return safety
    merged = dict(safety)
    merged["liveness"] = liveness
    if liveness["status"] == "violation":
        merged["status"] = "violation"
        merged["guarantee"] = liveness.get("guarantee", "violation-found")
        merged["message"] = liveness.get("message", merged.get("message", ""))
        merged["violated"] = liveness.get("violated", [])
        merged["violations"] = liveness.get("violations", [])
        merged["trace"] = liveness.get("trace", [])
        merged["lasso"] = liveness.get("lasso")
    elif liveness["status"] == "unknown":
        merged["status"] = "unknown"
        merged["guarantee"] = "unknown"
        merged["message"] = liveness.get("message", merged.get("message", ""))
    else:
        merged["guarantee"] = (
            f"{safety.get('guarantee', '')}; {liveness.get('guarantee', '')}".strip("; ")
        )
        merged["message"] = f"{safety.get('message', '')}; {liveness.get('message', '')}".strip(
            "; "
        )
    return merged


def format_liveness_result(spec: Spec, result: dict[str, Any]) -> str:
    if result.get("status") == "ok" and "property" not in result:
        return ""
    lines = [result.get("message", "")]
    if result.get("lasso"):
        ls = result["lasso"]
        lines.append(f"  lasso: repeat from step {ls['loop_start']} at step {ls['loop_end']}")
    for entry in result.get("trace", []):
        lines.append(f"  step {entry['step']}: state={entry['state']}")
        if "action" in entry:
            lines.append(f"    -> action {entry['action']} params={entry.get('params', {})}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# TLA+ export helpers
# --------------------------------------------------------------------------


def tla_fairness_lines(spec: Spec) -> list[str]:
    lines: list[str] = []
    for action_name in spec.fairness.weak:
        a = spec.action(action_name)
        sig = ", ".join(a.params)
        call = f"{action_name}({sig})" if sig else action_name
        lines.append(f"WF_{action_name} == WF_vars({call})")
    for action_name in spec.fairness.strong:
        a = spec.action(action_name)
        sig = ", ".join(a.params)
        call = f"{action_name}({sig})" if sig else action_name
        lines.append(f"SF_{action_name} == SF_vars({call})")
    if lines:
        lines.append("")
    return lines


def tla_temporal_lines(spec: Spec) -> list[str]:
    from .codegen import to_tla

    lines: list[str] = []
    for prop in spec.temporal:
        goal = to_tla(prop.goal, spec.types)
        if prop.kind == "eventually":
            lines.append(f"{prop.name} == <> ({goal})")
        else:
            when = to_tla(prop.when or "FALSE", spec.types)
            lines.append(f"{prop.name} == [] ({when} => <> ({goal}))")
    if lines:
        lines.append("")
    return lines
