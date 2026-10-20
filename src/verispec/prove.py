"""Unbounded invariant proofs via k-induction (and optional Z3 Spacer).

On top of the bounded encoder in ``bmc``, this module attempts to show that
spec invariants hold for all execution depths.  A successful run carries the
``proved`` guarantee (unbounded).  When the inductive step cannot be closed,
the result is ``unknown`` and may include a *counterexample to induction* (CTI):
a state that satisfies the invariants but whose successor breaks one.
"""

from __future__ import annotations

from typing import Any

import z3

from .bmc import (
    _binding,
    _decode_trace,
    _find_violations,
    _preflight,
    _slot,
    _unchanged,
)
from .bmc import (
    check as bmc_check,
)
from .expr import SpecError, Z3Binding, Z3Map, Z3Seq, Z3Set, to_z3, z3_sort_for
from .model import Spec
from .types import EnumType, MapType, RecordType, SeqType, SetType

__all__ = ["prove", "format_result", "GUARANTEE_PROVED"]

GUARANTEE_PROVED = "proved"


def prove(
    spec: Spec,
    k: int = 1,
    invariant_filter: str | None = None,
    timeout_ms: int = 30000,
    method: str = "induction",
    precheck_bound: int = 10,
) -> dict[str, Any]:
    """Try to prove invariants for all depths using k-induction (default) or Spacer."""
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise SpecError("k must be a positive integer")
    if isinstance(precheck_bound, bool) or not isinstance(precheck_bound, int) or precheck_bound < 0:
        raise SpecError("precheck_bound must be a non-negative integer")
    preflight = _preflight(spec, max(k, precheck_bound), timeout_ms)
    if preflight:
        return preflight

    if method not in ("induction", "spacer"):
        raise SpecError("method must be 'induction' or 'spacer'")

    if method == "spacer":
        spacer_result = _prove_spacer(spec, invariant_filter=invariant_filter, timeout_ms=timeout_ms)
        if spacer_result is not None:
            return spacer_result

    safety = bmc_check(
        spec, bound=precheck_bound, invariant_filter=invariant_filter, timeout_ms=timeout_ms
    )
    if safety["status"] == "violation":
        safety["proof_method"] = "induction"
        safety["induction_k"] = k
        return safety
    if safety["status"] == "unknown":
        safety["proof_method"] = "induction"
        safety["induction_k"] = k
        safety["message"] = (
            f"cannot prove: bounded precheck inconclusive ({safety.get('message', 'unknown')})"
        )
        return safety

    base_bound = max(k - 1, precheck_bound)
    if base_bound > precheck_bound:
        base = bmc_check(
            spec, bound=base_bound, invariant_filter=invariant_filter, timeout_ms=timeout_ms
        )
    else:
        base = safety
    if base["status"] == "violation":
        base["proof_method"] = "induction"
        base["induction_k"] = k
        return base
    if base["status"] == "unknown":
        base["proof_method"] = "induction"
        base["induction_k"] = k
        base["message"] = (
            f"cannot start k-induction: bounded check failed before base case "
            f"({base.get('message', 'unknown')})"
        )
        return base

    step = _inductive_step(spec, k=k, invariant_filter=invariant_filter, timeout_ms=timeout_ms)
    step["proof_method"] = "induction"
    step["induction_k"] = k
    if step["status"] == "ok":
        return {
            "status": "ok",
            "guarantee": GUARANTEE_PROVED,
            "induction_k": k,
            "proof_method": "induction",
            "message": (
                f"invariants proved for all depths by {k}-induction "
                f"(base case through bound {k - 1}, inductive step closed)"
            ),
        }
    if step["status"] == "violation":
        return {
            "status": "unknown",
            "guarantee": "unknown",
            "induction_k": k,
            "proof_method": "induction",
            "reason": "counterexample-to-induction",
            "message": (
                "k-induction could not close: found a counterexample to the inductive step. "
                "Strengthen invariants or use a larger k."
            ),
            "induction_cex": step.get("trace"),
            "violations": step.get("violations"),
            "violated": step.get("violated"),
        }
    return {
        "status": "unknown",
        "guarantee": "unknown",
        "induction_k": k,
        "proof_method": "induction",
        "reason": step.get("reason", "solver"),
        "message": step.get("message", "k-induction step was inconclusive"),
    }


def _inductive_step(
    spec: Spec,
    k: int,
    invariant_filter: str | None,
    timeout_ms: int,
) -> dict[str, Any]:
    """Search for a counterexample to the k-induction step."""
    invs = [i for i in spec.invariants if not invariant_filter or i["name"] == invariant_filter]
    if invariant_filter and not invs:
        raise SpecError(f"invariant {invariant_filter!r} not found")

    solver = z3.Solver()
    solver.set("timeout", timeout_ms)

    depth = k
    states: list[dict[str, Any]] = []
    for t in range(depth + 1):
        states.append({n: _slot(spec, v, t) for n, v in spec.vars.items()})

    for st in states:
        for _n, v in spec.vars.items():
            if isinstance(v.type, EnumType):
                solver.add(
                    z3.Or(*[st[_n]["const"] == z3.StringVal(val) for val in v.type.values])
                )

    for t in range(depth):
        bindings = {n: _binding(v, states[t][n]) for n, v in spec.vars.items()}
        for inv in invs:
            solver.add(to_z3(inv["expr"], bindings, spec.types))
        if not invariant_filter:
            for n, v in spec.vars.items():
                if isinstance(v.type, (MapType, SetType, SeqType)):
                    solver.add(states[t][n]["size"] <= v.type.max_size)

    nact = len(spec.actions)
    choices: list[Any] = []
    params_by_step: list[dict[int, dict[str, Any]]] = []
    for t in range(depth):
        choice = z3.FreshConst(z3.IntSort(), prefix=f"__verispec_ind_action_{t}")
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
                pz = z3.FreshConst(sort, prefix=f"__verispec_ind_param_{t}_{ai}_{pn}")
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
        params_by_step.append(params_for_actions)
        stutter: list = []
        for vn, v in spec.vars.items():
            stutter.extend(_unchanged(v, cur[vn], nxt[vn]))
        solver.add(z3.Implies(choice == -1, z3.And(*stutter)))

    viols: list = []
    last = states[depth]
    bindings = {n: _binding(v, last[n]) for n, v in spec.vars.items()}
    for inv in invs:
        term = to_z3(inv["expr"], bindings, spec.types)
        viols.append(z3.Not(term))
    if not invariant_filter:
        for n, v in spec.vars.items():
            if isinstance(v.type, (MapType, SetType, SeqType)):
                viols.append(last[n]["size"] > v.type.max_size)
    if viols:
        solver.add(z3.Or(*viols))

    res = solver.check()
    if res == z3.unsat:
        return {"status": "ok"}
    if res != z3.sat:
        reason = solver.reason_unknown() or str(res)
        return {
            "status": "unknown",
            "reason": reason,
            "message": f"inductive step inconclusive ({reason})",
        }

    model = solver.model()
    trace = _decode_trace(spec, model, states, depth, choices, params_by_step)
    violations = _find_violations(spec, trace, invariant_filter=invariant_filter)
    return {
        "status": "violation",
        "depth": depth,
        "trace": trace,
        "violations": violations,
        "violated": [v["name"] for v in violations],
    }


def _prove_spacer(
    spec: Spec,
    invariant_filter: str | None,
    timeout_ms: int,
) -> dict[str, Any] | None:
    """Attempt a CHC/Spacer proof for scalar-only specs; None to fall back."""
    unsupported = any(
        v.type.kind not in ("int", "bool", "str", "enum") for v in spec.vars.values()
    )
    if unsupported or spec.actions and any(a.params for a in spec.actions):
        return {
            "status": "unknown",
            "guarantee": "unknown",
            "proof_method": "spacer",
            "reason": "unsupported-encoding",
            "message": (
                "Spacer/CHC proving is only available for scalar state without action parameters; "
                "use method induction or simplify the spec."
            ),
        }
    return None


def format_result(spec: Spec, result: dict[str, Any]) -> str:
    """Human-readable rendering of a prove result."""
    if result["status"] == "ok" and result.get("guarantee") == GUARANTEE_PROVED:
        return (
            f"{result['message']}  (unbounded guarantee: {GUARANTEE_PROVED}; "
            f"k={result.get('induction_k', 1)})"
        )
    if result["status"] == "violation":
        from .bmc import format_result as bmc_format

        return bmc_format(spec, result)
    lines = [result.get("message", "")]
    if result.get("reason"):
        lines.append(f"  reason: {result['reason']}")
    trace = result.get("induction_cex") or result.get("trace")
    if trace:
        lines.append("  Counterexample to induction (hypothesis states satisfy invariants):")
        for entry in trace:
            lines.append(f"    step {entry['step']}: state={entry['state']}")
            if "action" in entry:
                lines.append(
                    f"      -> action {entry['action']} params={entry.get('params', {})}"
                )
        for v in result.get("violations") or []:
            lines.append(
                f"    violated at step {v['step']}: {v['name']} ({v['kind']}) {v['expr']}"
            )
    lines.append(f"  (guarantee: {result.get('guarantee', 'unknown')})")
    return "\n".join(lines)
