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

from .expr import SpecError, eval_python
from .model import Spec, check_invariants, initial_state
from .types import MapType, SeqType, SetType
from .values import json_to_value, state_from_json, value_to_json

__all__ = ["check_trace", "load_trace", "GUARANTEE_CONFORMANCE"]

GUARANTEE_CONFORMANCE = "conformance-tested"


def load_trace(trace_path: str | Path) -> list[dict[str, Any]]:
    """Read a trace file and validate its shape."""
    with open(trace_path) as f:
        try:
            trace = json.load(f)
        except json.JSONDecodeError as e:
            raise SpecError(f"{trace_path}: invalid JSON: {e}") from e
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
    types = spec.types
    init = initial_state(spec)
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
        bad = check_invariants(spec, state)
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
        params = step.get("params") or {}
        if action in (None, "init"):
            return _fail(i, "missing-action", f"step {i}: missing action")
        if not spec.has_action(action):
            return _fail(i, "unknown-action", f"step {i}: unknown action {action!r}")
        a = spec.action(action)
        if not isinstance(params, dict):
            return _fail(i, "invalid-params", f"step {i}: params must be an object")
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
        env = {**prev_state, **params}
        for r in a.requires:
            if not eval_python(r, env, spec.tenv(a)):
                return _fail(
                    i,
                    "precondition",
                    f"step {i}: precondition violated for action {action}: {r} "
                    f"(prev={prev_state}, params={params})",
                )
        expected = dict(prev_state)
        for k, e in a.effects.items():
            var = spec.vars[k]
            if var.kind == "record":
                rec = dict(prev_state[k])
                for fname, fexpr in e.items():
                    rec[fname] = eval_python(fexpr, env, spec.tenv(a))
                expected[k] = rec
            else:
                expected[k] = eval_python(e, env, spec.tenv(a))
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
