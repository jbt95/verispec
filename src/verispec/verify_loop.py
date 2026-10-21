"""Escalating verification loop (ideas from VeriCUA, arXiv:2610.26469).

Each obligation is checked with an explicit tri-state status:

* **satisfied** — evidence shows the condition holds within the stated guarantee;
* **violated** — a concrete counterexample was found;
* **unresolved** — cheap evidence did not decide; may be escalated.

Overall verdict:

* **success** — every check is satisfied;
* **failure** — stop at the first violated check;
* **uncertain** — no violation, but at least one check is still unresolved.

Cheap backends run first (trace replay and bounded random walks, like generated
property tests). Unresolved checks are escalated to Z3 bounded model checking
with a larger depth budget. Cost (rounds, backend calls, time) is recorded in
the report.
"""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from .bmc import check as bmc_check
from .model import Spec, apply_action, check_invariants, initial_state
from .spec_semantic import IoCase, run_io_validation, run_reference_compare, run_vacuity_checks
from .trace import check_trace_data
from .values import state_to_json

__all__ = [
    "CheckStatus",
    "Verdict",
    "VerificationCheck",
    "VerificationReport",
    "run_verify_loop",
    "format_verify_loop",
]

CheckStatus = Literal["satisfied", "violated", "unresolved"]
Verdict = Literal["success", "failure", "uncertain"]

_BACKEND_TRACE = "trace-monitor"
_BACKEND_FUZZ = "property-walk"
_BACKEND_Z3 = "z3-bmc"


@dataclass
class VerificationCheck:
    """One named obligation with actionable evidence location."""

    check_id: str
    condition: str
    kind: str
    evidence_location: str
    status: CheckStatus = "unresolved"
    backend: str | None = None
    message: str = ""
    guarantee: str | None = None
    counterexample: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.check_id,
            "condition": self.condition,
            "kind": self.kind,
            "evidence_location": self.evidence_location,
            "status": self.status,
        }
        if self.backend:
            out["backend"] = self.backend
        if self.message:
            out["message"] = self.message
        if self.guarantee:
            out["guarantee"] = self.guarantee
        if self.counterexample:
            out["counterexample"] = self.counterexample
        return out


@dataclass
class VerificationReport:
    verdict: Verdict
    checks: list[VerificationCheck]
    cost: dict[str, Any] = field(default_factory=dict)
    stopped_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "checks": [c.to_dict() for c in self.checks],
            "cost": self.cost,
            "stopped_at": self.stopped_at,
        }


def _vars_in_expr(expr: str, spec: Spec) -> list[str]:
    names = sorted(spec.vars.keys(), key=len, reverse=True)
    found: list[str] = []
    for name in names:
        if re.search(rf"\b{name}\b", expr):
            found.append(name)
    return found or list(spec.vars.keys())


def _invariant_checks(spec: Spec) -> list[VerificationCheck]:
    checks: list[VerificationCheck] = []
    for inv in spec.invariants:
        vars_ = _vars_in_expr(inv["expr"], spec)
        loc = "state variables: " + ", ".join(vars_)
        checks.append(
            VerificationCheck(
                check_id=f"invariant:{inv['name']}",
                condition=inv["expr"],
                kind="invariant",
                evidence_location=loc,
            )
        )
    return checks


def _trace_conformance_check() -> VerificationCheck:
    return VerificationCheck(
        check_id="trace:conformance",
        condition="every step matches spec transitions and invariants",
        kind="trace",
        evidence_location="trace events (action, params, state per step)",
    )


def _pick_params(spec: Spec, action_name: str, rng: random.Random) -> dict[str, Any]:
    action = spec.action(action_name)
    params: dict[str, Any] = {}
    for pn, pv in action.params.items():
        if pv.kind == "int":
            lo = pv.min if pv.min is not None else 0
            hi = pv.max if pv.max is not None else lo + 10
            params[pn] = rng.randint(lo, hi)
        elif pv.kind == "bool":
            params[pn] = rng.choice([True, False])
        elif pv.kind == "enum":
            params[pn] = rng.choice(pv.values)
        elif pv.kind == "str":
            params[pn] = "x"
        else:
            params[pn] = 0
    return params


def _fuzz_invariant(
    spec: Spec,
    inv_name: str,
    trials: int,
    max_steps: int,
    seed: int,
) -> dict[str, Any] | None:
    """Random action walks; return counterexample dict or None."""
    if not spec.actions:
        return None
    rng = random.Random(seed)
    for _ in range(trials):
        state = initial_state(spec)
        for step in range(max_steps):
            bad = check_invariants(spec, state, inv_name)
            if bad:
                return {
                    "step": step,
                    "state": state_to_json(spec.types, state),
                    "violation": bad[0],
                }
            action = rng.choice(spec.actions)
            params = _pick_params(spec, action.name, rng)
            try:
                state = apply_action(spec, state, action.name, params)
            except Exception:
                continue
        bad = check_invariants(spec, state, inv_name)
        if bad:
            return {
                "step": max_steps,
                "state": state_to_json(spec.types, state),
                "violation": bad[0],
            }
    return None


def _apply_trace_round(
    spec: Spec,
    trace: list[dict[str, Any]] | None,
    checks: list[VerificationCheck],
    cost: dict[str, Any],
) -> bool:
    """Cheap trace monitor. Returns True if a violation was found (stop early)."""
    if trace is None:
        return False
    cost["backend_calls"] = int(cost.get("backend_calls", 0)) + 1
    result = check_trace_data(spec, trace)
    trace_check = next((c for c in checks if c.check_id == "trace:conformance"), None)
    if trace_check and trace_check.status == "unresolved":
        if result["ok"]:
            trace_check.status = "satisfied"
            trace_check.backend = _BACKEND_TRACE
            trace_check.guarantee = result.get("guarantee")
            trace_check.message = result.get("message", "")
        else:
            trace_check.status = "violated"
            trace_check.backend = _BACKEND_TRACE
            trace_check.guarantee = "violation-found"
            step = result.get("failed_step")
            trace_check.evidence_location = (
                f"trace step {step}: {result.get('reason', 'violation')}"
            )
            trace_check.message = result.get("message", "")
            trace_check.counterexample = {
                "failed_step": step,
                "reason": result.get("reason"),
                "detail": result.get("detail"),
                "violated": result.get("violated"),
            }
            return True
    for inv_check in checks:
        if inv_check.kind != "invariant" or inv_check.status != "unresolved":
            continue
        inv_name = inv_check.check_id.split(":", 1)[1]
        for i, step in enumerate(trace):
            from .values import state_from_json

            try:
                state = state_from_json(spec.types, step.get("state"), f"step {i}")
            except Exception:
                continue
            bad = check_invariants(spec, state, inv_name)
            if bad:
                inv_check.status = "violated"
                inv_check.backend = _BACKEND_TRACE
                inv_check.guarantee = "violation-found"
                inv_check.evidence_location = (
                    f"trace step {i}, state variables: "
                    + ", ".join(_vars_in_expr(inv_check.condition, spec))
                )
                inv_check.message = f"invariant {inv_name} violated in recorded trace"
                inv_check.counterexample = {
                    "step": i,
                    "action": step.get("action"),
                    "violation": bad[0],
                }
                return True
        inv_check.status = "satisfied"
        inv_check.backend = _BACKEND_TRACE
        inv_check.guarantee = "conformance-tested"
        inv_check.message = (
            f"invariant {inv_name} held on all {len(trace)} trace step(s) "
            "(trace replay; not a proof for all executions)"
        )
    return False


def _apply_fuzz_round(
    spec: Spec,
    checks: list[VerificationCheck],
    cost: dict[str, Any],
    fuzz_trials: int,
    fuzz_steps: int,
    seed: int,
) -> bool:
    """Cheap property-style walks for still-unresolved invariants."""
    violated = False
    for inv_check in checks:
        if inv_check.kind != "invariant" or inv_check.status != "unresolved":
            continue
        inv_name = inv_check.check_id.split(":", 1)[1]
        cost["backend_calls"] = int(cost.get("backend_calls", 0)) + 1
        ce = _fuzz_invariant(spec, inv_name, fuzz_trials, fuzz_steps, seed)
        if ce:
            inv_check.status = "violated"
            inv_check.backend = _BACKEND_FUZZ
            inv_check.guarantee = "violation-found"
            inv_check.evidence_location = (
                f"simulated step {ce['step']}, state variables: "
                + ", ".join(_vars_in_expr(inv_check.condition, spec))
            )
            inv_check.message = f"random walk found violation of {inv_name}"
            inv_check.counterexample = ce
            violated = True
            break
    return violated


def _apply_z3_round(
    spec: Spec,
    checks: list[VerificationCheck],
    cost: dict[str, Any],
    bound: int,
    timeout_ms: int,
) -> bool:
    """Escalate unresolved invariants to Z3; stop at first counterexample."""
    for inv_check in checks:
        if inv_check.kind != "invariant" or inv_check.status != "unresolved":
            continue
        inv_name = inv_check.check_id.split(":", 1)[1]
        cost["backend_calls"] = int(cost.get("backend_calls", 0)) + 1
        result = bmc_check(
            spec,
            bound=bound,
            invariant_filter=inv_name,
            timeout_ms=timeout_ms,
        )
        if result["status"] == "violation":
            inv_check.status = "violated"
            inv_check.backend = _BACKEND_Z3
            inv_check.guarantee = "violation-found"
            v0 = (result.get("violations") or [{}])[0]
            step = v0.get("step", result.get("bound"))
            inv_check.evidence_location = (
                f"bounded execution step {step}, state variables: "
                + ", ".join(_vars_in_expr(inv_check.condition, spec))
            )
            inv_check.message = result.get("message", "")
            inv_check.counterexample = {
                "bound": result.get("bound"),
                "trace": result.get("trace"),
                "violations": result.get("violations"),
            }
            return True
        if result["status"] == "ok":
            inv_check.status = "satisfied"
            inv_check.backend = _BACKEND_Z3
            inv_check.guarantee = result.get("guarantee")
            inv_check.message = result.get("message", "")
        else:
            inv_check.status = "unresolved"
            inv_check.backend = _BACKEND_Z3
            inv_check.guarantee = "unknown"
            inv_check.message = result.get("message", "solver could not decide")
    return False


def _final_verdict(checks: list[VerificationCheck]) -> Verdict:
    if any(c.status == "violated" for c in checks):
        return "failure"
    if all(c.status == "satisfied" for c in checks):
        return "success"
    return "uncertain"


def _semantic_strength_checks(
    spec: Spec,
    *,
    io_cases: list[IoCase] | None,
    reference: Spec | None,
    timeout_ms: int,
    z3_bound: int,
    seed: int,
) -> list[VerificationCheck]:
    checks: list[VerificationCheck] = []
    vacuity = run_vacuity_checks(spec, timeout_ms=min(timeout_ms, 15_000))
    for finding in vacuity.findings:
        if finding.status != "proved":
            continue
        checks.append(
            VerificationCheck(
                check_id=f"semantic:vacuity:{finding.code}:{finding.where}",
                condition=finding.message,
                kind="semantic-vacuity",
                evidence_location=finding.where or "spec",
                status="violated",
                backend="z3-vacuity",
                message=finding.message,
            )
        )
    if io_cases:
        io_report = run_io_validation(spec, io_cases, auto_mutate=True, seed=seed)
        for result in io_report.results:
            cid = f"semantic:io:{result.case.label}"
            checks.append(
                VerificationCheck(
                    check_id=cid,
                    condition=result.case.kind,
                    kind="semantic-io",
                    evidence_location=result.case.label,
                    status="satisfied" if result.ok else "violated",
                    backend="trace-conformance",
                    message=result.message,
                )
            )
    if reference is not None:
        ref = run_reference_compare(spec, reference, bound=z3_bound, timeout_ms=timeout_ms)
        status: CheckStatus = "satisfied" if ref.relation == "equivalent" else "violated"
        checks.append(
            VerificationCheck(
                check_id="semantic:reference-compare",
                condition=f"reference relation: {ref.relation}",
                kind="semantic-reference",
                evidence_location="reference spec",
                status=status,
                backend="z3-compare",
                message=str(ref.counterexample) if ref.counterexample else ref.relation,
            )
        )
    return checks


def run_verify_loop(
    spec: Spec,
    *,
    trace: list[dict[str, Any]] | None = None,
    max_rounds: int = 2,
    fuzz_bound: int = 6,
    fuzz_trials: int = 40,
    z3_bound: int = 12,
    timeout_ms: int = 30000,
    seed: int = 0,
    semantic_strength: bool = False,
    io_cases: list[IoCase] | None = None,
    reference: Spec | None = None,
) -> VerificationReport:
    """Run escalating verification; stop at the first violated check."""
    if max_rounds < 1:
        raise ValueError("max_rounds must be at least 1")

    started = time.monotonic()
    cost: dict[str, Any] = {
        "rounds_used": 0,
        "backend_calls": 0,
        "fuzz_trials_per_invariant": fuzz_trials,
        "fuzz_max_steps": fuzz_bound,
        "z3_bound": z3_bound,
    }
    checks: list[VerificationCheck] = []
    if semantic_strength or io_cases or reference is not None:
        checks.extend(
            _semantic_strength_checks(
                spec,
                io_cases=io_cases,
                reference=reference,
                timeout_ms=timeout_ms,
                z3_bound=z3_bound,
                seed=seed,
            )
        )
        for c in checks:
            if c.status == "violated":
                cost["duration_ms"] = int((time.monotonic() - started) * 1000)
                return VerificationReport(
                    verdict="failure",
                    checks=checks + _invariant_checks(spec),
                    cost=cost,
                    stopped_at=c.check_id,
                )
    checks.extend(_invariant_checks(spec))
    if trace is not None:
        checks.insert(0, _trace_conformance_check())

    stopped_at: str | None = None

    def _first_violation() -> str | None:
        for c in checks:
            if c.status == "violated":
                return c.check_id
        return None

    # Round 1: trace monitor (if any) then property-style random walks.
    cost["rounds_used"] = 1
    if _apply_trace_round(spec, trace, checks, cost) or _apply_fuzz_round(
        spec, checks, cost, fuzz_trials, fuzz_bound, seed
    ):
        stopped_at = _first_violation()

    # Round 2+: escalate still-unresolved invariants to Z3 (larger bound).
    round_num = 2
    while stopped_at is None and round_num <= max_rounds:
        if not any(c.status == "unresolved" for c in checks):
            break
        cost["rounds_used"] = round_num
        if _apply_z3_round(spec, checks, cost, z3_bound, timeout_ms):
            stopped_at = _first_violation()
            break
        round_num += 1

    cost["duration_ms"] = int((time.monotonic() - started) * 1000)
    verdict = _final_verdict(checks)
    return VerificationReport(verdict=verdict, checks=checks, cost=cost, stopped_at=stopped_at)


def format_verify_loop(report: VerificationReport) -> str:
    lines = [f"verdict: {report.verdict}"]
    for check in report.checks:
        lines.append(
            f"  [{check.status}] {check.check_id} @ {check.evidence_location}"
            + (f" ({check.backend})" if check.backend else "")
        )
        if check.message:
            lines.append(f"      {check.message}")
    lines.append(
        "cost: "
        f"{report.cost.get('rounds_used', 0)} round(s), "
        f"{report.cost.get('backend_calls', 0)} backend call(s), "
        f"{report.cost.get('duration_ms', 0)} ms"
    )
    if report.stopped_at:
        lines.append(f"stopped at first violation: {report.stopped_at}")
    return "\n".join(lines)
