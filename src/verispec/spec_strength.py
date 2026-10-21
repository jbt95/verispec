"""Spec-strength diagnostics via controlled trace perturbations.

Inspired by StepBench (arXiv:2610.26679): mutate a known-good trace or a
model-generated walk (drop a step, swap two steps, replace an action) and
see whether the spec still rejects the bad behaviour. Mutants that still
pass conformance checking indicate the contract is too weak.

Failures are classified with a small typed taxonomy instead of a bare pass/fail.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Literal

from .expr import SpecError
from .model import Spec, apply_action, initial_state
from .spec_semantic import (
    IoCase,
    SemanticStrengthReport,
    SemanticVerdict,
    combine_semantic_verdict,
    run_semantic_strength,
)
from .trace import check_trace_data
from .values import state_to_json

__all__ = [
    "FailureKind",
    "SpecMutant",
    "SpecStrengthReport",
    "SemanticVerdict",
    "classify_trace_failure",
    "generate_mutants_from_trace",
    "replay_steps",
    "run_spec_strength",
    "steps_from_trace",
]

FailureKind = Literal[
    "missing_step",
    "extraneous_step",
    "misordered_step",
    "invariant_contradiction",
    "other",
]

MutantKind = Literal["drop_step", "swap_steps", "replace_action"]

_REASON_TO_KIND: dict[str, FailureKind] = {
    "invariant": "invariant_contradiction",
    "capacity": "invariant_contradiction",
    "precondition": "missing_step",
    "effect-mismatch": "misordered_step",
    "init-mismatch": "missing_step",
    "unknown-action": "extraneous_step",
    "missing-action": "missing_step",
    "missing-param": "missing_step",
    "invalid-param": "extraneous_step",
    "invalid-state": "other",
    "invalid-trace": "other",
    "evaluation-error": "other",
}


def classify_trace_failure(reason: str | None, mutant_kind: MutantKind) -> FailureKind:
    """Map a trace checker reason to a failure kind, biased by the perturbation."""
    if not reason:
        return "other"
    base = _REASON_TO_KIND.get(reason, "other")
    if mutant_kind == "drop_step" and base == "misordered_step":
        return "missing_step"
    if mutant_kind == "swap_steps" and base in ("missing_step", "effect-mismatch"):
        return "misordered_step"
    if mutant_kind == "replace_action" and reason == "effect-mismatch":
        return "extraneous_step"
    return base


@dataclass
class SpecMutant:
    mutant_id: str
    kind: MutantKind
    description: str
    trace: list[dict[str, Any]]
    caught: bool
    failure_kind: FailureKind | None = None
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.mutant_id,
            "kind": self.kind,
            "description": self.description,
            "caught": self.caught,
            "failure_kind": self.failure_kind,
            "message": self.message,
            "steps": len(self.trace),
        }


@dataclass
class SpecStrengthReport:
    base_steps: int
    mutants: list[SpecMutant]
    survived: int
    caught: int
    skipped: int
    semantic: SemanticStrengthReport | None = None
    semantic_verdict: SemanticVerdict | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "base_steps": self.base_steps,
            "mutants": [m.to_dict() for m in self.mutants],
            "survived": self.survived,
            "caught": self.caught,
            "skipped": self.skipped,
            "weak_spec": self.survived > 0,
        }
        if self.semantic is not None:
            out.update(self.semantic.to_dict())
        elif self.semantic_verdict is not None:
            out["semantic_verdict"] = self.semantic_verdict
        return out


def steps_from_trace(trace: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    """Action steps from a trace (excluding the initial init row)."""
    out: list[tuple[str, dict[str, Any]]] = []
    for i, step in enumerate(trace):
        if i == 0:
            continue
        action = step.get("action")
        if not isinstance(action, str) or not action:
            raise SpecError(f"step {i}: missing action")
        params = step.get("params") or {}
        if not isinstance(params, dict):
            raise SpecError(f"step {i}: params must be an object")
        out.append((action, dict(params)))
    return out


def replay_steps(spec: Spec, steps: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    """Rebuild a conforming trace from action steps using the spec semantics."""
    state = initial_state(spec)
    trace: list[dict[str, Any]] = [
        {
            "action": "init",
            "params": {},
            "state": state_to_json(spec.types, state),
        }
    ]
    for action, params in steps:
        state = apply_action(spec, state, action, params)
        trace.append(
            {
                "action": action,
                "params": params,
                "state": state_to_json(spec.types, state),
            }
        )
    return trace


def _default_params(spec: Spec, action_name: str, rng: random.Random) -> dict[str, Any]:
    action = spec.action(action_name)
    params: dict[str, Any] = {}
    for pn, pv in action.params.items():
        if pv.kind == "int":
            lo = pv.min if pv.min is not None else 1
            hi = pv.max if pv.max is not None else lo
            params[pn] = lo if lo == hi else rng.randint(lo, hi)
        elif pv.kind == "bool":
            params[pn] = True
        elif pv.kind == "enum":
            params[pn] = pv.values[0]
        else:
            params[pn] = 0
    return params


def _random_walk_steps(spec: Spec, length: int, seed: int) -> list[tuple[str, dict[str, Any]]]:
    if not spec.actions:
        return []
    rng = random.Random(seed)
    steps: list[tuple[str, dict[str, Any]]] = []
    state = initial_state(spec)
    for _ in range(length):
        action = rng.choice(spec.actions)
        params = _default_params(spec, action.name, rng)
        try:
            state = apply_action(spec, state, action.name, params)
            steps.append((action.name, params))
        except SpecError:
            continue
    return steps


def generate_mutants_from_trace(
    spec: Spec,
    base_trace: list[dict[str, Any]],
    *,
    include_replace: bool = True,
) -> list[tuple[MutantKind, str, list[dict[str, Any]]]]:
    """Enumerate trace perturbations (states are not recomputed — procedure faults)."""
    mutants: list[tuple[MutantKind, str, list[dict[str, Any]]]] = []
    n = len(base_trace)
    if n < 2:
        return mutants
    for i in range(1, n):
        dropped = [dict(s) for s in base_trace[:i]] + [dict(s) for s in base_trace[i + 1 :]]
        action = base_trace[i].get("action")
        mutants.append(("drop_step", f"omit recorded step {i} ({action})", dropped))
    for i in range(1, n - 1):
        swapped = [dict(s) for s in base_trace]
        swapped[i], swapped[i + 1] = swapped[i + 1], swapped[i]
        mutants.append(("swap_steps", f"swap recorded steps {i} and {i + 1}", swapped))
    if include_replace and spec.actions:
        for i in range(1, n):
            orig_action = base_trace[i].get("action")
            for alt in spec.actions:
                if alt.name == orig_action:
                    continue
                replaced = [dict(s) for s in base_trace]
                step = dict(replaced[i])
                step["action"] = alt.name
                step["params"] = _default_params(spec, alt.name, random.Random(i))
                replaced[i] = step
                mutants.append(
                    (
                        "replace_action",
                        f"replace step {i} action {orig_action!r} with {alt.name!r} "
                        "(keep recorded state)",
                        replaced,
                    )
                )
    return mutants


def run_spec_strength(
    spec: Spec,
    *,
    trace: list[dict[str, Any]] | None = None,
    walk_length: int = 4,
    seed: int = 0,
    max_mutants: int | None = None,
    io_cases: list[IoCase] | None = None,
    reference: Spec | None = None,
    semantic: bool = True,
    auto_mutate_io: bool = True,
    z3_bound: int = 6,
    timeout_ms: int = 10_000,
) -> SpecStrengthReport:
    """Run mutation-style diagnostics on a base trace or a model-generated walk."""
    if trace is not None:
        base_trace = trace
        base_steps = len(trace) - 1
    else:
        steps = _random_walk_steps(spec, walk_length, seed)
        if not steps:
            return SpecStrengthReport(base_steps=0, mutants=[], survived=0, caught=0, skipped=0)
        base_trace = replay_steps(spec, steps)
        base_steps = len(steps)

    raw_mutants = generate_mutants_from_trace(spec, base_trace)
    if max_mutants is not None:
        raw_mutants = raw_mutants[:max_mutants]

    results: list[SpecMutant] = []
    survived = caught = skipped = 0
    for idx, (kind, description, mutant_trace) in enumerate(raw_mutants):
        mutant_id = f"m{idx}:{kind}"
        check = check_trace_data(spec, mutant_trace)
        if check["ok"]:
            survived += 1
            results.append(
                SpecMutant(
                    mutant_id=mutant_id,
                    kind=kind,
                    description=description,
                    trace=mutant_trace,
                    caught=False,
                    message="mutant still conforms — spec may be too weak for this perturbation",
                )
            )
        else:
            caught += 1
            reason = check.get("reason")
            results.append(
                SpecMutant(
                    mutant_id=mutant_id,
                    kind=kind,
                    description=description,
                    trace=mutant_trace,
                    caught=True,
                    failure_kind=classify_trace_failure(reason, kind),
                    message=check.get("message", ""),
                )
            )
    semantic_report: SemanticStrengthReport | None = None
    verdict: SemanticVerdict | None = None
    if semantic or io_cases or reference is not None:
        semantic_report = run_semantic_strength(
            spec,
            io_cases=io_cases,
            reference=reference,
            auto_mutate_io=auto_mutate_io,
            io_seed=seed,
            z3_bound=z3_bound,
            timeout_ms=timeout_ms,
        )
        verdict = combine_semantic_verdict(
            vacuity=semantic_report.vacuity,
            io=semantic_report.io,
            reference=semantic_report.reference,
            mutation_survived=survived,
        )
        semantic_report.verdict = verdict

    return SpecStrengthReport(
        base_steps=base_steps,
        mutants=results,
        survived=survived,
        caught=caught,
        skipped=skipped,
        semantic=semantic_report,
        semantic_verdict=verdict,
    )
