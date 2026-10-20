"""Bounded refinement checking between a concrete and an abstract spec.

A concrete spec may declare ``refines:`` with a state abstraction and action
simulation map.  The checker searches for a concrete execution (up to a bound)
that cannot be coupled to abstract behaviour under that map.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import z3

from .bmc import _binding, _decode_trace, _encode_init, _slot, _unchanged
from .expr import SpecError, Z3Binding, to_z3, z3_sort_for
from .model import Spec, load_spec
from .types import EnumType, MapType, RecordType, SeqType, SetType
from .values import value_to_json

__all__ = [
    "ActionRefinement",
    "RefinementMapping",
    "check_refinement",
    "export_refinement_tla_comment",
    "format_refinement_result",
    "load_refinement_mapping",
    "resolve_abstract_spec",
]

GUARANTEE_REFINEMENT = "bounded-refinement-proved(k)"


@dataclass
class ActionRefinement:
    kind: Literal["stutter", "abstract"]
    abstract_action: str | None = None
    param_exprs: dict[str, str] = field(default_factory=dict)


@dataclass
class RefinementMapping:
    abstract_path: str
    state_exprs: dict[str, str]
    actions: dict[str, ActionRefinement]


def resolve_abstract_spec(concrete: Spec, mapping: RefinementMapping) -> Spec:
    base = Path(concrete.path).parent if concrete.path not in ("<spec>", "") else Path(".")
    ap = (base / mapping.abstract_path).resolve()
    if not ap.exists():
        raise SpecError(
            f"refines.spec {mapping.abstract_path!r} not found (resolved to {ap})"
        )
    return load_spec(ap)


def load_refinement_mapping(concrete: Spec) -> RefinementMapping:
    """Parse and validate the ``refines`` block on a concrete spec."""
    raw = concrete.raw.get("refines")
    if not raw:
        raise SpecError("concrete spec has no refines block (required for refinement checking)")
    if not isinstance(raw, dict):
        raise SpecError("refines must be a mapping")
    spec_path = raw.get("spec")
    if not spec_path or not isinstance(spec_path, str):
        raise SpecError("refines.spec is required and must be a path string")
    state = raw.get("state")
    if not isinstance(state, dict) or not state:
        raise SpecError("refines.state is required and must be a non-empty mapping")
    actions_raw = raw.get("actions")
    if not isinstance(actions_raw, dict) or not actions_raw:
        raise SpecError("refines.actions is required and must be a non-empty mapping")

    concrete_names = {a.name for a in concrete.actions}
    missing = sorted(concrete_names - set(actions_raw))
    if missing:
        raise SpecError(
            "refines.actions missing mapping for concrete action(s): " + ", ".join(missing)
        )

    state_exprs: dict[str, str] = {}
    for an, expr in state.items():
        if not isinstance(an, str) or not isinstance(expr, str):
            raise SpecError("refines.state entries must be abstract_var: expression strings")
        state_exprs[an] = expr

    actions: dict[str, ActionRefinement] = {}
    for cname, entry in actions_raw.items():
        if cname not in concrete_names:
            raise SpecError(f"refines.actions.{cname}: unknown concrete action")
        if not isinstance(entry, dict):
            raise SpecError(f"refines.actions.{cname}: must be a mapping")
        stutter = entry.get("stutter")
        abstract = entry.get("abstract")
        if stutter and abstract:
            raise SpecError(f"refines.actions.{cname}: cannot set both stutter and abstract")
        if stutter:
            if stutter is not True:
                raise SpecError(f"refines.actions.{cname}: stutter must be true")
            actions[cname] = ActionRefinement(kind="stutter")
            continue
        if not abstract or not isinstance(abstract, str):
            raise SpecError(
                f"refines.actions.{cname}: set stutter: true or abstract: <abstract action name>"
            )
        params_raw = entry.get("params") or {}
        if not isinstance(params_raw, dict):
            raise SpecError(f"refines.actions.{cname}.params must be a mapping")
        param_exprs = {str(k): str(v) for k, v in params_raw.items()}
        actions[cname] = ActionRefinement(
            kind="abstract", abstract_action=abstract, param_exprs=param_exprs
        )

    return RefinementMapping(
        abstract_path=spec_path,
        state_exprs=state_exprs,
        actions=actions,
    )


def _validate_mapping_types(
    concrete: Spec, abstract: Spec, mapping: RefinementMapping
) -> None:
    from .model import _check_assignable  # noqa: PLC2701

    for an, expr in mapping.state_exprs.items():
        if an not in abstract.vars:
            raise SpecError(f"refines.state.{an}: unknown abstract variable")
        _check_assignable(abstract.vars[an].type, expr, concrete.types, f"refines.state.{an}")

    abs_action_names = {a.name for a in abstract.actions}
    for cname, ar in mapping.actions.items():
        if ar.kind == "abstract":
            assert ar.abstract_action is not None
            if ar.abstract_action not in abs_action_names:
                raise SpecError(
                    f"refines.actions.{cname}.abstract: unknown abstract action "
                    f"{ar.abstract_action!r}"
                )
            aa = abstract.action(ar.abstract_action)
            ca = concrete.action(cname)
            for pn, pexpr in ar.param_exprs.items():
                if pn not in aa.params:
                    raise SpecError(
                        f"refines.actions.{cname}.params.{pn}: not a parameter of "
                        f"abstract action {ar.abstract_action!r}"
                    )
                tenv = {**concrete.types, **{p: v.type for p, v in ca.params.items()}}
                _check_assignable(
                    aa.params[pn].type, pexpr, tenv, f"refines.actions.{cname}.params.{pn}"
                )


def _mapped_bindings(
    concrete: Spec,
    abstract: Spec,
    mapping: RefinementMapping,
    conc_slots: dict[str, Any],
) -> dict[str, Z3Binding]:
    conc_bindings = {n: _binding(v, conc_slots[n]) for n, v in concrete.vars.items()}
    out: dict[str, Z3Binding] = {}
    for an, expr in mapping.state_exprs.items():
        expected = abstract.vars[an].type
        out[an] = Z3Binding(
            to_z3(expr, conc_bindings, concrete.types, expected=expected),
            expected,
        )
    return out


def _encode_coupling(
    concrete: Spec,
    abstract: Spec,
    mapping: RefinementMapping,
    conc_slots: dict[str, Any],
    abs_slots: dict[str, Any],
) -> list[Any]:
    conc_bindings = {n: _binding(v, conc_slots[n]) for n, v in concrete.vars.items()}
    abs_bindings = {n: _binding(v, abs_slots[n]) for n, v in abstract.vars.items()}
    out: list[Any] = []
    for an, expr in mapping.state_exprs.items():
        expected = abstract.vars[an].type
        if expected.kind in ("map", "set", "seq", "record"):
            raise SpecError(
                f"refines.state.{an}: collection/record abstraction in refinement is not supported yet"
            )
        lhs = to_z3(expr, conc_bindings, concrete.types, expected=expected)
        rhs = abs_bindings[an].value
        out.append(lhs == rhs)
    return out


def _encode_transitions(
    spec: Spec,
    solver: z3.Solver,
    states: list[dict[str, Any]],
    bound: int,
    prefix: str,
) -> tuple[list[Any], list[dict[int, dict[str, Any]]]]:
    """Encode one transition system (same shape as ``bmc._check_one``)."""
    nact = len(spec.actions)
    choices: list[Any] = []
    params_by_step: list[dict[int, dict[str, Any]]] = []
    for t in range(bound):
        choice = z3.FreshConst(z3.IntSort(), prefix=f"__verispec_{prefix}_action_{t}")
        choices.append(choice)
        params_for_actions: dict[int, dict[str, Any]] = {}
        solver.add(choice >= -1, choice < nact)
        cur, nxt = states[t], states[t + 1]
        cur_bindings = {n: _binding(v, cur[n]) for n, v in spec.vars.items()}
        for ai, a in enumerate(spec.actions):
            penv: dict[str, Z3Binding] = {}
            action_params: dict[str, Any] = {}
            for pn, pv in a.params.items():
                sort = z3_sort_for(pv.type)
                pz = z3.FreshConst(sort, prefix=f"__verispec_{prefix}_param_{t}_{ai}_{pn}")
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
        stutter: list = []
        for vn, v in spec.vars.items():
            stutter.extend(_unchanged(v, cur[vn], nxt[vn]))
        solver.add(z3.Implies(choice == -1, z3.And(*stutter)))
    return choices, params_by_step


def _abstract_param_constraints(
    abstract: Spec,
    action_name: str,
    param_exprs: dict[str, str],
    concrete: Spec,
    concrete_action: str,
    conc_env: dict[str, Z3Binding],
    abs_params: dict[str, Any],
) -> list[Any]:
    aa = abstract.action(action_name)
    ca = concrete.action(concrete_action)
    tenv = {**concrete.types, **{p: v.type for p, v in ca.params.items()}}
    out: list[Any] = []
    for pn, pv in aa.params.items():
        if pn in param_exprs:
            expr = param_exprs[pn]
        elif pn in ca.params:
            expr = pn
        else:
            raise SpecError(
                f"refines.actions.{concrete_action}: missing param mapping for abstract "
                f"parameter {pn!r}"
            )
        val = to_z3(expr, conc_env, tenv, expected=pv.type)
        out.append(abs_params[pn] == val)
    return out


def _refinement_simulation_failure(
    concrete: Spec,
    abstract: Spec,
    mapping: RefinementMapping,
    conc_states: list[dict[str, Any]],
    abs_states: list[dict[str, Any]],
    bound: int,
    conc_choices: list[Any],
    conc_params: list[dict[int, dict[str, Any]]],
    timeout_ms: int,
) -> dict[str, Any]:
    """Concrete trace exists but no abstract simulation matches the refinement map."""
    solver = z3.Solver()
    solver.set("timeout", timeout_ms)
    for t in range(bound + 1):
        coupling_t = _encode_coupling(concrete, abstract, mapping, conc_states[t], abs_states[t])
        if coupling_t:
            solver.add(z3.And(*coupling_t))
    _encode_init(solver, concrete, conc_states)
    _encode_init(solver, abstract, abs_states)
    choices, params = _encode_transitions(concrete, solver, conc_states, bound, "conc_only")
    if solver.check() != z3.sat:
        return {
            "status": "unknown",
            "bound": bound,
            "message": f"simulation failed but no witness concrete trace at bound {bound}",
            "guarantee": "unknown",
        }
    model = solver.model()
    concrete_trace = _decode_trace(concrete, model, conc_states, bound, choices, params)
    abstract_trace = _decode_abstract_trace(
        concrete, abstract, mapping, model, conc_states, abs_states, bound, choices, params
    )
    return {
        "status": "violation",
        "bound": bound,
        "depth": len(concrete_trace) - 1,
        "concrete_trace": concrete_trace,
        "abstract_trace": abstract_trace,
        "violations": [
            {
                "name": "simulation",
                "kind": "refinement",
                "expr": "no abstract step matches the refinement map",
                "step": 0,
                "concrete_state": value_to_json(concrete.types, concrete_trace[0]["state"]),
                "abstract_state": value_to_json(abstract.types, abstract_trace[0]["state"]),
            }
        ],
        "violated": ["simulation"],
        "message": (
            f"REFINEMENT VIOLATION within bound {bound}: "
            "concrete behaviour is not simulated by the abstract spec under the map"
        ),
        "guarantee": "violation-found",
    }


def _check_mapped_invariant_violation(
    concrete: Spec,
    abstract: Spec,
    mapping: RefinementMapping,
    bound: int,
    timeout_ms: int,
) -> dict[str, Any] | None:
    """Search for a concrete trace whose abstraction breaks an abstract invariant."""
    solver = z3.Solver()
    solver.set("timeout", timeout_ms)
    conc_states = [{n: _slot(concrete, v, t) for n, v in concrete.vars.items()} for t in range(bound + 1)]
    for st in conc_states:
        for n, v in concrete.vars.items():
            if isinstance(v.type, EnumType):
                solver.add(z3.Or(*[st[n]["const"] == z3.StringVal(val) for val in v.type.values]))
    _encode_init(solver, concrete, conc_states)
    choices, params = _encode_transitions(concrete, solver, conc_states, bound, "mapped")
    viols: list[Any] = []
    for t in range(bound + 1):
        mapped = _mapped_bindings(concrete, abstract, mapping, conc_states[t])
        for inv in abstract.invariants:
            viols.append(z3.Not(to_z3(inv["expr"], mapped, abstract.types)))
    solver.add(z3.Or(*viols))
    if solver.check() != z3.sat:
        return None
    model = solver.model()
    concrete_trace = _decode_trace(concrete, model, conc_states, bound, choices, params)
    abs_states = [
        {n: _slot(abstract, v, (bound + 1) * 2000 + t) for n, v in abstract.vars.items()}
        for t in range(bound + 1)
    ]
    abstract_trace = []
    for entry in concrete_trace:
        step = entry["step"]
        c_state = entry["state"]
        from .expr import eval_python

        a_state = {
            an: eval_python(mapping.state_exprs[an], c_state, concrete.types)
            for an in mapping.state_exprs
        }
        abstract_trace.append({"step": step, "state": a_state, **{k: v for k, v in entry.items() if k not in ("step", "state")}})
    violations = _classify_violation(concrete, abstract, mapping, concrete_trace, abstract_trace)
    return {
        "status": "violation",
        "bound": bound,
        "depth": len(concrete_trace) - 1,
        "concrete_trace": concrete_trace,
        "abstract_trace": abstract_trace,
        "violations": violations,
        "violated": [v["name"] for v in violations],
        "message": (
            f"REFINEMENT VIOLATION within bound {bound}: "
            + ", ".join(v["name"] for v in violations)
        ),
        "guarantee": "violation-found",
    }


def _check_one(
    concrete: Spec,
    abstract: Spec,
    mapping: RefinementMapping,
    bound: int,
    timeout_ms: int,
) -> dict[str, Any]:
    mapped_hit = _check_mapped_invariant_violation(concrete, abstract, mapping, bound, timeout_ms)
    if mapped_hit is not None:
        return mapped_hit

    solver = z3.Solver()
    solver.set("timeout", timeout_ms)

    conc_states: list[dict[str, Any]] = []
    abs_states: list[dict[str, Any]] = []
    for t in range(bound + 1):
        conc_states.append({n: _slot(concrete, v, t) for n, v in concrete.vars.items()})
        abs_states.append(
            {
                n: _slot(abstract, v, (bound + 1) * 1000 + t)
                for n, v in abstract.vars.items()
            }
        )

    for spec, states in ((concrete, conc_states), (abstract, abs_states)):
        for st in states:
            for n, v in spec.vars.items():
                if isinstance(v.type, EnumType):
                    solver.add(
                        z3.Or(*[st[n]["const"] == z3.StringVal(val) for val in v.type.values])
                    )

    _encode_init(solver, concrete, conc_states)
    _encode_init(solver, abstract, abs_states)

    conc_choices, conc_params = _encode_transitions(
        concrete, solver, conc_states, bound, "conc"
    )

    conc_index = {a.name: i for i, a in enumerate(concrete.actions)}

    for t in range(bound + 1):
        coupling_t = _encode_coupling(concrete, abstract, mapping, conc_states[t], abs_states[t])
        if coupling_t:
            solver.add(z3.And(*coupling_t))

    def _abstract_invs_hold(slots: dict[str, Any]) -> z3.BoolRef:
        abs_bindings = {n: _binding(v, slots[n]) for n, v in abstract.vars.items()}
        terms = [to_z3(inv["expr"], abs_bindings, abstract.types) for inv in abstract.invariants]
        return z3.And(*terms) if terms else z3.BoolVal(True)

    for t in range(bound):
        c_choice = conc_choices[t]
        c_cur, c_nxt = conc_states[t], conc_states[t + 1]
        a_cur, a_nxt = abs_states[t], abs_states[t + 1]

        a_stutter = z3.And(
            *[c for n, v in abstract.vars.items() for c in _unchanged(v, a_cur[n], a_nxt[n])]
        )
        coupling_after = _encode_coupling(concrete, abstract, mapping, c_nxt, a_nxt)
        stutter_ok = z3.And(a_stutter, *coupling_after, _abstract_invs_hold(a_nxt))
        step_matches: list[Any] = [z3.And(c_choice == -1, stutter_ok)]

        for cname, ar in mapping.actions.items():
            ci = conc_index[cname]
            ca = concrete.action(cname)
            conc_bindings = {n: _binding(v, c_cur[n]) for n, v in concrete.vars.items()}
            penv = conc_params[t][ci]
            conc_env = {
                **conc_bindings,
                **{pn: Z3Binding(penv[pn], ca.params[pn].type) for pn in ca.params},
            }

            if ar.kind == "stutter":
                step_matches.append(z3.And(c_choice == ci, stutter_ok))
                continue

            assert ar.abstract_action is not None
            aa = abstract.action(ar.abstract_action)
            abs_pz: dict[str, Any] = {}
            for pn, pv in aa.params.items():
                abs_pz[pn] = z3.FreshConst(
                    z3_sort_for(pv.type), prefix=f"__verispec_abs_follow_{t}_{cname}_{pn}"
                )
            param_eq = _abstract_param_constraints(
                abstract,
                ar.abstract_action,
                ar.param_exprs,
                concrete,
                cname,
                conc_env,
                abs_pz,
            )
            abs_bindings_cur = {n: _binding(v, a_cur[n]) for n, v in abstract.vars.items()}
            penv_abs = {pn: Z3Binding(abs_pz[pn], aa.params[pn].type) for pn in aa.params}
            env_abs = {**abs_bindings_cur, **penv_abs}
            tenv_abs = abstract.tenv(aa)
            req = (
                z3.And(*[to_z3(r, env_abs, tenv_abs) for r in aa.requires])
                if aa.requires
                else z3.BoolVal(True)
            )
            eff_abs: list = []
            for vn, v in abstract.vars.items():
                if vn not in aa.effects:
                    eff_abs.extend(_unchanged(v, a_cur[vn], a_nxt[vn]))
                    continue
                raw = aa.effects[vn]
                val = to_z3(raw, env_abs, tenv_abs, expected=v.type)
                if v.type.kind in ("map", "set", "seq"):
                    if v.type.kind == "map":
                        eff_abs.append(a_nxt[vn]["values"] == val.values)
                        eff_abs.append(a_nxt[vn]["present"] == val.present)
                        eff_abs.append(a_nxt[vn]["size"] == val.size)
                    elif v.type.kind == "set":
                        eff_abs.append(a_nxt[vn]["present"] == val.present)
                        eff_abs.append(a_nxt[vn]["size"] == val.size)
                    else:
                        eff_abs.append(a_nxt[vn]["items"] == val.items)
                        eff_abs.append(a_nxt[vn]["size"] == val.size)
                else:
                    eff_abs.append(a_nxt[vn]["const"] == val)
            coupling_end = _encode_coupling(concrete, abstract, mapping, c_nxt, a_nxt)
            action_ok = z3.And(*param_eq, req, *eff_abs, *coupling_end, _abstract_invs_hold(a_nxt))
            step_matches.append(z3.And(c_choice == ci, action_ok))

        sim_ok = z3.Or(*step_matches)
        solver.add(sim_ok)

    sim_solver = z3.Solver()
    sim_solver.set("timeout", timeout_ms)
    for a in solver.assertions():
        sim_solver.add(a)

    sim_res = sim_solver.check()
    if sim_res == z3.unsat:
        return _refinement_simulation_failure(
            concrete,
            abstract,
            mapping,
            conc_states,
            abs_states,
            bound,
            conc_choices,
            conc_params,
            timeout_ms,
        )
    if sim_res != z3.sat:
        reason = sim_solver.reason_unknown() or str(sim_res)
        return {
            "status": "unknown",
            "bound": bound,
            "reason": reason,
            "message": f"solver returned {sim_res} up to bound {bound} ({reason})",
            "guarantee": "unknown",
        }

    inv_violations: list[Any] = []
    for t in range(bound + 1):
        abs_bindings = {n: _binding(v, abs_states[t][n]) for n, v in abstract.vars.items()}
        for inv in abstract.invariants:
            term = to_z3(inv["expr"], abs_bindings, abstract.types)
            inv_violations.append(z3.Not(term))

    solver.add(z3.Or(*inv_violations))
    res = solver.check()
    if res == z3.unsat:
        return {
            "status": "ok",
            "bound": bound,
            "message": f"no refinement violation up to bound {bound}",
            "guarantee": GUARANTEE_REFINEMENT.replace("(k)", f"({bound})"),
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
    concrete_trace = _decode_trace(concrete, model, conc_states, bound, conc_choices, conc_params)
    abstract_trace = _decode_abstract_trace(
        concrete, abstract, mapping, model, conc_states, abs_states, bound, conc_choices, conc_params
    )
    violations = _classify_violation(
        concrete, abstract, mapping, concrete_trace, abstract_trace
    )
    return {
        "status": "violation",
        "bound": bound,
        "depth": len(concrete_trace) - 1,
        "concrete_trace": concrete_trace,
        "abstract_trace": abstract_trace,
        "violations": violations,
        "violated": [v["name"] for v in violations],
        "message": (
            f"REFINEMENT VIOLATION within bound {bound}: "
            + ", ".join(v["name"] for v in violations)
        ),
        "guarantee": "violation-found",
    }


def _decode_abstract_trace(
    concrete: Spec,
    abstract: Spec,
    mapping: RefinementMapping,
    model,
    conc_states: list[dict[str, Any]],
    abs_states: list[dict[str, Any]],
    bound: int,
    conc_choices: list[Any],
    conc_params: list[dict[int, dict[str, Any]]],
) -> list[dict[str, Any]]:
    from .bmc import _decode_scalar

    conc_index = {i: a.name for i, a in enumerate(concrete.actions)}
    trace: list[dict[str, Any]] = []
    for t in range(bound + 1):
        state = _decode_state(abstract, model, abs_states[t])
        entry: dict[str, Any] = {"step": t, "state": state}
        if t < bound:
            ch = model.eval(conc_choices[t], model_completion=True).as_long()
            if ch == -1:
                entry["action"] = "(stutter)"
                entry["params"] = {}
            elif 0 <= ch < len(concrete.actions):
                cname = conc_index[ch]
                ar = mapping.actions[cname]
                if ar.kind == "stutter":
                    entry["action"] = "(stutter)"
                    entry["params"] = {}
                else:
                    assert ar.abstract_action is not None
                    aa = abstract.action(ar.abstract_action)
                    ca = concrete.action(cname)
                    conc_bindings = {
                        n: _binding(v, conc_states[t][n]) for n, v in concrete.vars.items()
                    }
                    penv = conc_params[t][ch]
                    conc_env = {
                        **conc_bindings,
                        **{pn: Z3Binding(penv[pn], ca.params[pn].type) for pn in ca.params},
                    }
                    params: dict[str, Any] = {}
                    for pn, pv in aa.params.items():
                        if pn in ar.param_exprs:
                            from .expr import eval_python

                            params[pn] = eval_python(
                                ar.param_exprs[pn],
                                {
                                    n: _decode_scalar(
                                        concrete.vars[n].type,
                                        model.eval(
                                            conc_bindings[n].value, model_completion=True
                                        ),
                                    )
                                    for n in concrete.vars
                                }
                                | {
                                    pn: _decode_scalar(
                                        ca.params[pn].type,
                                        model.eval(penv[pn], model_completion=True),
                                    )
                                    for pn in ca.params
                                },
                                concrete.types,
                            )
                        elif pn in ca.params:
                            params[pn] = _decode_scalar(
                                pv.type, model.eval(penv[pn], model_completion=True)
                            )
                    entry["action"] = ar.abstract_action
                    entry["params"] = params
        trace.append(entry)
    return trace


def _decode_state(spec: Spec, model, slots: dict[str, Any]) -> dict[str, Any]:
    from .bmc import _decode_map, _decode_scalar, _decode_seq, _decode_set

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
        else:
            raise SpecError(f"var {name}: unsupported type {t.kind}")
    return out


def _classify_violation(
    concrete: Spec,
    abstract: Spec,
    mapping: RefinementMapping,
    concrete_trace: list[dict[str, Any]],
    abstract_trace: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    from .expr import eval_python
    from .model import check_invariants

    found: list[dict[str, Any]] = []
    for entry in concrete_trace:
        step = entry["step"]
        c_state = entry["state"]
        a_entry = abstract_trace[step]
        a_state = a_entry["state"]
        abs_view: dict[str, Any] = {}
        for an, expr in mapping.state_exprs.items():
            abs_view[an] = eval_python(expr, c_state, concrete.types)
        for an, val in abs_view.items():
            if a_state.get(an) != val:
                found.append(
                    {
                        "name": "coupling",
                        "kind": "refinement",
                        "expr": f"{an} = {mapping.state_exprs[an]}",
                        "step": step,
                        "concrete_state": value_to_json(concrete.types, c_state),
                        "abstract_state": value_to_json(abstract.types, a_state),
                        "abstract_view": abs_view,
                    }
                )
                break
        for inv in check_invariants(abstract, a_state):
            found.append(
                {
                    "name": inv["name"],
                    "kind": "abstract-invariant",
                    "expr": inv["expr"],
                    "step": step,
                    "concrete_state": value_to_json(concrete.types, c_state),
                    "abstract_state": value_to_json(abstract.types, a_state),
                }
            )
    if not found:
        found.append(
            {
                "name": "simulation",
                "kind": "refinement",
                "expr": "concrete step not matched by abstract map",
                "step": concrete_trace[-1]["step"],
                "concrete_state": value_to_json(concrete.types, concrete_trace[-1]["state"]),
                "abstract_state": value_to_json(abstract.types, abstract_trace[-1]["state"]),
            }
        )
    return found


def check_refinement(
    concrete: Spec,
    abstract: Spec,
    mapping: RefinementMapping,
    bound: int = 10,
    timeout_ms: int = 30000,
) -> dict[str, Any]:
    if isinstance(bound, bool) or not isinstance(bound, int) or bound < 0:
        raise SpecError("bound must be a non-negative integer")
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or timeout_ms <= 0:
        raise SpecError("timeout_ms must be a positive integer")
    _validate_mapping_types(concrete, abstract, mapping)
    for k in range(0, bound + 1):
        r = _check_one(concrete, abstract, mapping, bound=k, timeout_ms=timeout_ms)
        if r["status"] == "violation":
            r["max_bound"] = bound
            return r
        if r["status"] == "unknown":
            r["max_bound"] = bound
            if k > 0:
                r["message"] = (
                    f"no refinement violation up to bound {k - 1}; "
                    f"the solver could not decide bound {k} ({r.get('reason', 'unknown')})"
                )
                r["guarantee"] = GUARANTEE_REFINEMENT.replace("(k)", f"({k - 1})")
            return r
    return {
        "status": "ok",
        "bound": bound,
        "max_bound": bound,
        "message": f"no refinement violation up to bound {bound}",
        "guarantee": GUARANTEE_REFINEMENT.replace("(k)", f"({bound})"),
    }


def format_refinement_result(result: dict[str, Any]) -> str:
    if result["status"] == "ok":
        return (
            f"{result['message']}  (bounded claim only; guarantee: {result.get('guarantee')})"
        )
    if result["status"] != "violation":
        return f"{result['message']}  (guarantee: {result.get('guarantee', 'unknown')})"
    lines = [result["message"]]
    for entry in result.get("concrete_trace", []):
        lines.append(f"  concrete step {entry['step']}: state={entry['state']}")
        if "action" in entry:
            lines.append(
                f"    -> {entry['action']} params={entry.get('params', {})}"
            )
    for entry in result.get("abstract_trace", []):
        lines.append(f"  abstract step {entry['step']}: state={entry['state']}")
        if "action" in entry:
            lines.append(
                f"    -> {entry['action']} params={entry.get('params', {})}"
            )
    for v in result.get("violations", []):
        lines.append(
            f"  violated at step {v['step']}: {v['name']} ({v['kind']}) {v.get('expr', '')}"
        )
    lines.append(
        "  Fix the concrete model or the refinement map; do not weaken the abstract spec."
    )
    return "\n".join(lines)


def export_refinement_tla_comment(
    concrete: Spec,
    abstract: Spec,
    mapping: RefinementMapping,
) -> str:
    """TLA+ comment block documenting the refinement map."""
    lines = [
        "(* Refinement map (documentation only; not checked by TLC):",
        f"   concrete module: {concrete.name}",
        f"   abstract module: {abstract.name}",
        "   state abstraction:",
    ]
    for an, expr in mapping.state_exprs.items():
        lines.append(f"     {an} = {expr}")
    lines.append("   action simulation:")
    for cname, ar in mapping.actions.items():
        if ar.kind == "stutter":
            lines.append(f"     {cname} |-> stutter")
        else:
            params = ", ".join(f"{k}={v}" for k, v in ar.param_exprs.items())
            lines.append(f"     {cname} |-> {ar.abstract_action}({params})")
    lines.append("*)")
    return "\n".join(lines)
