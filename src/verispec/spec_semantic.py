"""Semantic spec-strength checks (ideas from VerusEval, arXiv:2610.26464).

Adapted to VeriSpec state machines: action ``requires`` play the role of
preconditions; global invariants play the role of postconditions.  Checks use
the same Z3 expression encoding as bounded model checking.
"""

from __future__ import annotations

import copy
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import z3

from .bmc import _binding, _encode_transition, _fresh, _new_solver, _slot
from .expr import SpecError, Z3Binding, to_z3, z3_sort_for
from .model import Spec
from .trace import check_trace_data
from .types import EnumType

__all__ = [
    "SemanticVerdict",
    "VacuityReport",
    "IoSuiteReport",
    "ReferenceCompareReport",
    "SemanticStrengthReport",
    "combine_semantic_verdict",
    "load_io_suite",
    "run_io_validation",
    "run_reference_compare",
    "run_vacuity_checks",
    "run_semantic_strength",
]

SemanticVerdict = Literal["ok", "vacuous", "accepted-but-weak", "rejects-valid-io"]
ImplicationStatus = Literal["proved", "refuted", "unknown"]
ReferenceRelation = Literal["equivalent", "stronger", "weaker", "incomparable", "unknown"]


def _make_state_solver(spec: Spec, timeout_ms: int) -> tuple[z3.Solver, dict[str, dict[str, Any]], dict[str, Z3Binding]]:
    solver = _new_solver(timeout_ms, None)
    slots: dict[str, dict[str, Any]] = {}
    bindings: dict[str, Z3Binding] = {}
    for i, (n, v) in enumerate(spec.vars.items()):
        slots[n] = _slot(spec, v, i)
        bindings[n] = _binding(v, slots[n])
        if isinstance(v.type, EnumType):
            solver.add(
                z3.Or(*[slots[n]["const"] == z3.StringVal(val) for val in v.type.values])
            )
        if v.type.kind == "int":
            if v.min is not None:
                solver.add(slots[n]["const"] >= v.min)
            if v.max is not None:
                solver.add(slots[n]["const"] <= v.max)
    return solver, slots, bindings


def _action_params(
    spec: Spec,
    action,
    solver: z3.Solver,
    tag: str,
) -> dict[str, Z3Binding]:
    penv: dict[str, Z3Binding] = {}
    for pn, pv in action.params.items():
        pz = _fresh(z3_sort_for(pv.type), f"{tag}_{action.name}_{pn}")
        if pv.kind == "int":
            if pv.min is not None:
                solver.add(pz >= pv.min)
            if pv.max is not None:
                solver.add(pz <= pv.max)
        elif isinstance(pv.type, EnumType):
            solver.add(z3.Or(*[pz == z3.StringVal(val) for val in pv.type.values]))
        penv[pn] = Z3Binding(pz, pv.type)
    return penv


def _invariant_conj(spec: Spec, bindings: dict[str, Z3Binding]) -> z3.BoolRef:
    terms = [to_z3(inv["expr"], bindings, spec.types) for inv in spec.invariants]
    return z3.And(*terms) if terms else z3.BoolVal(True)


def _requires_conj(spec: Spec, action, env: dict[str, Z3Binding]) -> z3.BoolRef:
    if not action.requires:
        return z3.BoolVal(True)
    return z3.And(*[to_z3(r, env, spec.tenv(action)) for r in action.requires])


def _implication_status(
    solver: z3.Solver,
    antecedent: z3.BoolRef,
    consequent: z3.BoolRef,
    timeout_ms: int,
) -> ImplicationStatus:
    solver.push()
    solver.add(antecedent, z3.Not(consequent))
    res = solver.check()
    solver.pop()
    if res == z3.unsat:
        return "proved"
    if res == z3.sat:
        return "refuted"
    return "unknown"


@dataclass
class VacuityFinding:
    code: str
    message: str
    where: str = ""
    status: ImplicationStatus = "proved"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "where": self.where,
            "status": self.status,
        }


@dataclass
class VacuityReport:
    findings: list[VacuityFinding] = field(default_factory=list)

    @property
    def vacuous(self) -> bool:
        return any(f.status == "proved" for f in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {"vacuous": self.vacuous, "findings": [f.to_dict() for f in self.findings]}


def run_vacuity_checks(spec: Spec, *, timeout_ms: int = 10_000) -> VacuityReport:
    """Detect tautological invariants and unsatisfiable or vacuous action contracts."""
    findings: list[VacuityFinding] = []
    solver, slots, bindings = _make_state_solver(spec, timeout_ms)

    for inv in spec.invariants:
        inv_term = to_z3(inv["expr"], bindings, spec.types)
        status = _implication_status(solver, z3.BoolVal(True), inv_term, timeout_ms)
        if status == "proved":
            findings.append(
                VacuityFinding(
                    code="true-postcondition",
                    message=f"invariant {inv['name']} is tautological (always true)",
                    where=inv["name"],
                    status=status,
                )
            )

    for action in spec.actions:
        penv = _action_params(spec, action, solver, "vac")
        env = {**bindings, **penv}
        pre = _requires_conj(spec, action, env)
        if action.requires:
            solver.push()
            solver.add(pre)
            pre_sat = solver.check()
            solver.pop()
            if pre_sat == z3.unsat:
                findings.append(
                    VacuityFinding(
                        code="false-precondition",
                        message=f"action {action.name} preconditions are unsatisfiable",
                        where=action.name,
                        status="proved",
                    )
                )
                continue

        inv_on_pre = _invariant_conj(spec, bindings)
        if action.requires:
            status = _implication_status(solver, pre, inv_on_pre, timeout_ms)
            if status == "proved":
                findings.append(
                    VacuityFinding(
                        code="post-implied-by-pre",
                        message=(
                            f"action {action.name}: invariants follow from requires on the "
                            "pre-state alone"
                        ),
                        where=action.name,
                        status=status,
                    )
                )

        nxt_slots = {n: _slot(spec, v, 200 + i) for i, (n, v) in enumerate(spec.vars.items())}
        step_solver = _new_solver(timeout_ms, None)
        states = [slots, nxt_slots]
        choice = _fresh(z3.IntSort(), f"vac_{action.name}")
        params_for: dict[int, dict[str, Any]] = {}
        ai = next(i for i, a in enumerate(spec.actions) if a.name == action.name)
        _encode_transition(step_solver, spec, states, 0, choice, params_for)
        step_solver.add(choice == ai)
        succ_bindings = {n: _binding(v, nxt_slots[n]) for n, v in spec.vars.items()}
        post_succ = _invariant_conj(spec, succ_bindings)
        status2 = _implication_status(step_solver, z3.BoolVal(True), post_succ, timeout_ms)
        if status2 == "proved":
            findings.append(
                VacuityFinding(
                    code="true-step-postcondition",
                    message=(
                        f"action {action.name}: invariants on the successor state are "
                        "tautological without requires"
                    ),
                    where=action.name,
                    status=status2,
                )
            )

    return VacuityReport(findings=findings)


IoCaseKind = Literal["correct", "wrong_output", "invalid_input"]


@dataclass
class IoCase:
    kind: IoCaseKind
    trace: list[dict[str, Any]]
    label: str = ""
    generated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "generated": self.generated,
            "steps": len(self.trace),
        }


@dataclass
class IoCaseResult:
    case: IoCase
    ok: bool
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {**self.case.to_dict(), "ok": self.ok, "message": self.message}


@dataclass
class IoSuiteReport:
    results: list[IoCaseResult] = field(default_factory=list)
    auto_generated: int = 0

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "auto_generated": self.auto_generated,
            "results": [r.to_dict() for r in self.results],
        }


def load_io_suite(path: str | Path) -> list[IoCase]:
    """Load I/O examples from JSON (``cases`` list)."""
    raw_text = Path(path).read_text(encoding="utf-8")
    data = json.loads(raw_text)
    if not isinstance(data, dict) or "cases" not in data:
        raise SpecError("I/O suite must be a JSON object with a 'cases' array")
    cases: list[IoCase] = []
    for i, entry in enumerate(data["cases"]):
        if not isinstance(entry, dict):
            raise SpecError(f"case {i}: must be an object")
        kind = entry.get("kind", "correct")
        if kind not in ("correct", "wrong_output", "invalid_input"):
            raise SpecError(f"case {i}: unknown kind {kind!r}")
        if "trace" in entry:
            trace = entry["trace"]
        elif "before" in entry and "action" in entry and "after" in entry:
            trace = [
                {"action": "init", "params": {}, "state": entry["before"]},
                {
                    "action": entry["action"],
                    "params": entry.get("params") or {},
                    "state": entry["after"],
                },
            ]
        else:
            raise SpecError(f"case {i}: need 'trace' or before/action/after")
        if not isinstance(trace, list) or len(trace) < 2:
            raise SpecError(f"case {i}: trace must have init plus at least one step")
        cases.append(
            IoCase(
                kind=kind,
                trace=trace,
                label=str(entry.get("label") or f"case-{i}"),
                generated=bool(entry.get("generated")),
            )
        )
    return cases


def _mutate_state_value(key: str, value: Any, rng: random.Random) -> Any:
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        delta = rng.choice([-1, 1, 2, -2])
        return value + delta
    if isinstance(value, dict):
        if not value:
            return {0: 1}
        k = rng.choice(list(value.keys()))
        child = copy.deepcopy(value)
        child[k] = _mutate_state_value(k, child[k], rng)
        return child
    if isinstance(value, list):
        if not value:
            return [1]
        idx = rng.randrange(len(value))
        child = list(value)
        child[idx] = _mutate_state_value(str(idx), child[idx], rng)
        return child
    return value


def _auto_wrong_outputs(
    spec: Spec,
    case: IoCase,
    rng: random.Random,
    max_mutants: int,
) -> list[IoCase]:
    if case.kind != "correct":
        return []
    out: list[IoCase] = []
    last = case.trace[-1]
    state = last.get("state")
    if not isinstance(state, dict):
        return []
    for m in range(max_mutants):
        mutated = copy.deepcopy(state)
        key = rng.choice(list(mutated.keys())) if mutated else None
        if key is None:
            continue
        mutated[key] = _mutate_state_value(key, mutated[key], rng)
        trace = [dict(s) for s in case.trace[:-1]] + [
            {**last, "state": mutated},
        ]
        out.append(
            IoCase(
                kind="wrong_output",
                trace=trace,
                label=f"auto-mutant-{m}-of-{case.label}",
                generated=True,
            )
        )
    return out


def _evaluate_io_case(spec: Spec, case: IoCase) -> IoCaseResult:
    check = check_trace_data(spec, case.trace)
    conforms = check["ok"]
    if case.kind == "correct":
        ok = conforms
        msg = "accepted correct I/O" if ok else check.get("message", "rejected valid trace")
    elif case.kind == "wrong_output":
        ok = not conforms
        msg = "rejected wrong output" if ok else "accepted wrong output (spec too weak)"
    else:
        ok = not conforms
        msg = "rejected invalid input" if ok else "accepted invalid input"
    return IoCaseResult(case=case, ok=ok, message=msg)


def run_io_validation(
    spec: Spec,
    cases: list[IoCase],
    *,
    auto_mutate: bool = True,
    seed: int = 0,
    max_auto_per_case: int = 2,
) -> IoSuiteReport:
    rng = random.Random(seed)
    expanded: list[IoCase] = list(cases)
    auto_n = 0
    if auto_mutate:
        for case in cases:
            mutants = _auto_wrong_outputs(spec, case, rng, max_auto_per_case)
            expanded.extend(mutants)
            auto_n += len(mutants)
    results = [_evaluate_io_case(spec, c) for c in expanded]
    return IoSuiteReport(results=results, auto_generated=auto_n)


@dataclass
class ObligationResult:
    name: str
    status: ImplicationStatus
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "message": self.message}


@dataclass
class ReferenceCompareReport:
    relation: ReferenceRelation
    obligations: list[ObligationResult] = field(default_factory=list)
    counterexample: dict[str, Any] | None = None
    bound: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "relation": self.relation,
            "obligations": [o.to_dict() for o in self.obligations],
            "counterexample": self.counterexample,
            "bound": self.bound,
        }


def _compatible_specs(candidate: Spec, reference: Spec) -> None:
    if set(candidate.vars.keys()) != set(reference.vars.keys()):
        raise SpecError("reference spec must declare the same state variables")
    cand_actions = {a.name for a in candidate.actions}
    ref_actions = {a.name for a in reference.actions}
    if cand_actions != ref_actions:
        raise SpecError("reference spec must declare the same action names")


def run_reference_compare(
    candidate: Spec,
    reference: Spec,
    *,
    bound: int = 6,
    timeout_ms: int = 10_000,
) -> ReferenceCompareReport:
    """Bounded comparison using contract-refinement obligations (L1–L4 style)."""
    _compatible_specs(candidate, reference)
    obligations: list[ObligationResult] = []

    solver_c, _, bind_c = _make_state_solver(candidate, timeout_ms)
    solver_r, _, bind_r = _make_state_solver(reference, timeout_ms)

    inv_c = _invariant_conj(candidate, bind_c)
    inv_r = _invariant_conj(reference, bind_r)

    # VerusEval L1–L4 on global invariants (post) and per-action requires (pre).
    inv_obligations = [
        ("L2-cand-post-implies-ref-post", inv_c, inv_r),
        ("L4-ref-post-implies-cand-post", inv_r, inv_c),
    ]
    ce: dict[str, Any] | None = None
    for name, ant, cons in inv_obligations:
        status = _implication_status(solver_c, ant, cons, timeout_ms)
        obligations.append(
            ObligationResult(
                name=name,
                status=status,
                message=f"invariants: {name} is {status}",
            )
        )
        if status == "refuted" and ce is None:
            ce = {"kind": "invariants", "obligation": name}

    ref_by_name = {a.name: a for a in reference.actions}
    for action in candidate.actions:
        ref_a = ref_by_name[action.name]
        penv_c = _action_params(candidate, action, solver_c, "cmp")
        penv_r = _action_params(reference, ref_a, solver_r, "cmp")
        env_c = {**bind_c, **penv_c}
        env_r = {**bind_r, **penv_r}
        pre_c = _requires_conj(candidate, action, env_c)
        pre_r = _requires_conj(reference, ref_a, env_r)
        for name, ant, cons in (
            ("L1-ref-pre-implies-cand-pre", pre_r, pre_c),
            ("L3-cand-pre-implies-ref-pre", pre_c, pre_r),
        ):
            status = _implication_status(solver_c, ant, cons, timeout_ms)
            obligations.append(
                ObligationResult(
                    name=f"{action.name}:{name}",
                    status=status,
                    message=f"{action.name} requires: {name} is {status}",
                )
            )
            if status == "refuted" and ce is None:
                ce = {"kind": "requires", "action": action.name, "obligation": name}

    proved = {o.name: o.status == "proved" for o in obligations}
    # Sound: L1 + L2; complete: L3 + L4 (VerusEval contract refinement).
    sound = proved.get("L2-cand-post-implies-ref-post") and all(
        proved.get(f"{a.name}:L1-ref-pre-implies-cand-pre", False) for a in candidate.actions
    )
    complete = proved.get("L4-ref-post-implies-cand-post") and all(
        proved.get(f"{a.name}:L3-cand-pre-implies-ref-pre", False) for a in candidate.actions
    )
    if sound and complete:
        relation: ReferenceRelation = "equivalent"
    elif sound and not complete:
        relation = "stronger"
    elif complete and not sound:
        relation = "weaker"
    elif any(o.status == "unknown" for o in obligations):
        relation = "unknown"
    else:
        relation = "incomparable"

    return ReferenceCompareReport(
        relation=relation,
        obligations=obligations,
        counterexample=ce,
        bound=bound,
    )


@dataclass
class SemanticStrengthReport:
    verdict: SemanticVerdict
    vacuity: VacuityReport
    io: IoSuiteReport | None = None
    reference: ReferenceCompareReport | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "semantic_verdict": self.verdict,
            "vacuity": self.vacuity.to_dict(),
        }
        if self.io is not None:
            out["io"] = self.io.to_dict()
        if self.reference is not None:
            out["reference"] = self.reference.to_dict()
        return out


def combine_semantic_verdict(
    *,
    vacuity: VacuityReport,
    io: IoSuiteReport | None,
    reference: ReferenceCompareReport | None,
    mutation_survived: int,
) -> SemanticVerdict:
    if vacuity.vacuous:
        return "vacuous"
    if io is not None and not io.ok:
        for r in io.results:
            if r.case.kind == "correct" and not r.ok:
                return "rejects-valid-io"
        return "accepted-but-weak"
    if mutation_survived > 0:
        return "accepted-but-weak"
    if reference is not None and reference.relation in ("weaker", "incomparable"):
        return "accepted-but-weak"
    return "ok"


def run_semantic_strength(
    spec: Spec,
    *,
    io_cases: list[IoCase] | None = None,
    reference: Spec | None = None,
    auto_mutate_io: bool = True,
    io_seed: int = 0,
    z3_bound: int = 6,
    timeout_ms: int = 10_000,
) -> SemanticStrengthReport:
    vacuity = run_vacuity_checks(spec, timeout_ms=timeout_ms)
    io_report = (
        run_io_validation(spec, io_cases, auto_mutate=auto_mutate_io, seed=io_seed)
        if io_cases
        else None
    )
    ref_report = (
        run_reference_compare(spec, reference, bound=z3_bound, timeout_ms=timeout_ms)
        if reference is not None
        else None
    )
    verdict = combine_semantic_verdict(
        vacuity=vacuity,
        io=io_report,
        reference=ref_report,
        mutation_survived=0,
    )
    return SemanticStrengthReport(
        verdict=verdict,
        vacuity=vacuity,
        io=io_report,
        reference=ref_report,
    )
