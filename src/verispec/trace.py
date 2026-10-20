"""Trace conformance: check that a real implementation follows the spec.

A trace is a JSON list of steps:

    [
      {"action": "init",   "params": {}, "state": {...}},
      {"action": "deposit", "params": {"amount": 10}, "state": {...}}
    ]

The first step is the initial state; every later step names the action that
was applied and the state that resulted from it.  This is the language
agnostic bridge: any language can produce one, and `verispec check-trace`
decides whether the implementation honours the contract.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .concurrency import actor_ids
from .expr import SpecError, eval_python
from .model import Spec, check_invariants, initial_state
from .types import MapType, SeqType, SetType
from .values import json_to_value, state_from_json, value_to_json

__all__ = ["check_trace", "load_trace", "GUARANTEE_CONFORMANCE"]

GUARANTEE_CONFORMANCE = "conformance-tested"


def load_trace(trace_path: str | Path) -> list[dict[str, Any]]:
    """Read a trace file and validate its shape."""
    try:
        raw = Path(trace_path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise SpecError(f"{trace_path}: cannot read trace: {error}") from error
    try:
        trace = json.loads(raw)
    except json.JSONDecodeError as error:
        raise SpecError(f"{trace_path}: invalid JSON: {error}") from error
    if not isinstance(trace, list) or not trace:
        raise SpecError("trace must be a non-empty JSON list")
    for i, step in enumerate(trace):
        if not isinstance(step, dict) or "state" not in step:
            raise SpecError(f"step {i}: missing 'state'")
    return trace


def check_trace(spec: Spec, trace_path: str | Path) -> dict[str, Any]:
    """Check a trace file against a spec."""
    try:
        trace = load_trace(trace_path)
    except SpecError as e:
        return _fail(None, "invalid-trace", str(e))
    return check_trace_data(spec, trace)


def check_trace_data(spec: Spec, trace: list[dict[str, Any]]) -> dict[str, Any]:
    """Check an in-memory trace against a spec."""
    if not isinstance(trace, list) or not trace:
        return _fail(None, "invalid-trace", "trace must be a non-empty JSON list")
    if any(not isinstance(step, dict) for step in trace):
        return _fail(None, "invalid-trace", "trace steps must be JSON objects")
    types = spec.types
    known_actors = actor_ids(spec)
    init, error = _evaluate_initial_state(spec)
    if error:
        return _fail(None, "evaluation-error", f"cannot evaluate initial state: {error}")
    for i, step in enumerate(trace):
        raw_state = step.get("state")
        try:
            state = state_from_json(types, raw_state, f"step {i}: state")
        except SpecError as e:
            return _fail(i, "invalid-state", str(e))
        # collection capacity
        over = _capacity_violations(spec, state)
        if over:
            return _fail(
                i,
                "capacity",
                f"step {i}: {over[0]}",
                expected=None,
                got=value_to_json(types, state),
                violated=[over[0]],
            )
        bad, error = _evaluate_invariants(spec, state)
        if error:
            return _fail(i, "evaluation-error", f"cannot evaluate invariants at step {i}: {error}")
        if bad:
            return _fail(
                i,
                "invariant",
                f"step {i}: invariant violated: {bad[0]['name']} ({bad[0]['expr']}) in state {state}",
                expected=None,
                got=value_to_json(types, state),
                violated=[b["name"] for b in bad],
            )
        if i == 0:
            if state != init:
                return _fail(
                    0,
                    "init-mismatch",
                    f"step 0: initial state mismatch: expected {init}, got {state}",
                    expected=value_to_json(types, init),
                    got=value_to_json(types, state),
                )
            continue
        prev = trace[i - 1].get("state")
        try:
            prev_state = state_from_json(types, prev, f"step {i - 1}: state")
        except SpecError as e:
            return _fail(i - 1, "invalid-state", str(e))
        action = step.get("action")
        params = step.get("params", {})
        actor = step.get("actor")
        if actor is not None and not isinstance(actor, str):
            return _fail(i, "invalid-actor", f"step {i}: actor must be a string")
        if known_actors:
            if actor is None:
                return _fail(
                    i,
                    "missing-actor",
                    f"step {i}: missing actor (spec declares concurrency actors)",
                )
            if actor not in known_actors:
                return _fail(
                    i,
                    "unknown-actor",
                    f"step {i}: unknown actor {actor!r} (known: {sorted(known_actors)})",
                )
        if not isinstance(action, str) or not action:
            return _fail(i, "missing-action", f"step {i}: missing or invalid action")
        if not isinstance(params, dict):
            return _fail(i, "invalid-params", f"step {i}: params must be an object")
        if not spec.has_action(action):
            return _fail(i, "unknown-action", f"step {i}: unknown action {action!r}")
        a = spec.action(action)
        for pn, pv in a.params.items():
            if pn not in params:
                return _fail(i, "missing-param", f"step {i}: action {action} misses param {pn!r}")
            try:
                json_to_value(pv.type, params[pn], f"step {i}: param {pn}")
            except SpecError as e:
                return _fail(i, "invalid-param", str(e))
        for extra in params:
            if extra not in a.params:
                return _fail(
                    i, "unknown-param", f"step {i}: action {action} has no param {extra!r}"
                )
        expected, failed_requirement, error = _evaluate_transition(spec, a, prev_state, params)
        if failed_requirement is not None:
            return _fail(
                i,
                "precondition",
                f"step {i}: precondition violated for action {action}: {failed_requirement} "
                f"(prev={prev_state}, params={params})",
            )
        if error:
            return _fail(i, "evaluation-error", f"cannot evaluate action {action} at step {i}: {error}")
        if expected != state:
            return _fail(
                i,
                "effect-mismatch",
                f"step {i}: effect mismatch for action {action}: expected {expected}, "
                f"got {state} (prev={prev_state}, params={params})",
                expected=value_to_json(types, expected),
                got=value_to_json(types, state),
            )
    return {
        "ok": True,
        "status": "ok",
        "message": f"trace OK: {len(trace)} steps conform to spec {spec.name}",
        "steps": len(trace),
        "failed_step": None,
        "reason": "",
        "detail": "",
        "expected": None,
        "got": None,
        "violated": [],
        "guarantee": GUARANTEE_CONFORMANCE,
    }


def _evaluate_initial_state(spec: Spec) -> tuple[dict[str, Any] | None, str | None]:
    try:
        return initial_state(spec), None
    except (SpecError, ArithmeticError, KeyError, TypeError, ValueError) as error:
        return None, str(error)


def _evaluate_invariants(
    spec: Spec, state: dict[str, Any]
) -> tuple[list[dict[str, str]] | None, str | None]:
    try:
        return check_invariants(spec, state), None
    except (SpecError, ArithmeticError, KeyError, TypeError, ValueError) as error:
        return None, str(error)


def _evaluate_transition(
    spec: Spec, action, previous: dict[str, Any], params: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None, str | None]:
    env = {**previous, **params}
    try:
        for requirement in action.requires:
            if not eval_python(requirement, env, spec.tenv(action)):
                return None, requirement, None
        expected = dict(previous)
        for key, effect in action.effects.items():
            variable = spec.vars[key]
            if variable.kind == "record":
                record = dict(previous[key])
                for field, expression in effect.items():
                    record[field] = eval_python(expression, env, spec.tenv(action))
                expected[key] = record
            else:
                expected[key] = eval_python(effect, env, spec.tenv(action))
    except (SpecError, ArithmeticError, KeyError, TypeError, ValueError) as error:
        return None, None, str(error)
    return expected, None, None


def _capacity_violations(spec: Spec, state: dict[str, Any]) -> list[str]:
    out = []
    for name, var in spec.vars.items():
        if isinstance(var.type, (MapType, SetType, SeqType)):
            size = len(state[name])
            if size > var.type.max_size:
                out.append(f"{name} holds {size} elements, more than max_size {var.type.max_size}")
    return out


def _fail(
    step: int | None,
    reason: str,
    detail: str,
    expected: Any = None,
    got: Any = None,
    violated: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "violation",
        "message": detail,
        "steps": 0,
        "failed_step": step,
        "reason": reason,
        "detail": detail,
        "expected": expected,
        "got": got,
        "violated": violated or [],
        "guarantee": "violation-found",
    }
