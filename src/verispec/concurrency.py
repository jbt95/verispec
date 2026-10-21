"""Concurrent interleaving exploration with partial-order reduction.

Specs may declare actors with fixed action programs; this module searches
reachable interleavings up to a step bound and reports counterexamples with
an ``actor`` label on each transition.
"""

from __future__ import annotations

import ast
import functools
import itertools
import json
from dataclasses import dataclass
from typing import Any

from .errors import SpecError
from .expr import parse_expr
from .model import Spec, apply_action, check_invariants, initial_state
from .types import MapType, SeqType, SetType
from .values import value_to_json

__all__ = [
    "ConcurrencyConfig",
    "Actor",
    "ProgramStep",
    "parse_concurrency",
    "validate_concurrency",
    "check",
    "format_result",
    "GUARANTEE_CONCURRENT",
    "actor_ids",
]

GUARANTEE_CONCURRENT = "concurrent-explored(k)"


@dataclass(frozen=True)
class ProgramStep:
    action: str
    param_choices: tuple[tuple[tuple[str, Any], ...], ...]

    def fixed_params(self) -> dict[str, Any] | None:
        if len(self.param_choices) == 1:
            return dict(self.param_choices[0])
        return None


@dataclass(frozen=True)
class Actor:
    actor_id: str
    program: tuple[ProgramStep, ...]


@dataclass(frozen=True)
class ConcurrencyConfig:
    actors: tuple[Actor, ...]


@dataclass(frozen=True)
class _Move:
    actor_index: int
    actor_id: str
    step_index: int
    action: str
    params: dict[str, Any]
    reads: frozenset[str]
    writes: frozenset[str]


def actor_ids(spec: Spec) -> frozenset[str]:
    cfg = parse_concurrency(spec)
    if cfg is None:
        return frozenset()
    return frozenset(a.actor_id for a in cfg.actors)


def parse_concurrency(spec: Spec) -> ConcurrencyConfig | None:
    raw = spec.raw.get("concurrency")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise SpecError("concurrency must be a mapping")
    actors_raw = raw.get("actors")
    if not isinstance(actors_raw, list) or not actors_raw:
        raise SpecError("concurrency.actors must be a non-empty list")
    actors: list[Actor] = []
    seen: set[str] = set()
    for entry in actors_raw:
        if not isinstance(entry, dict):
            raise SpecError("each concurrency actor must be a mapping")
        actor_id = entry.get("id")
        if not isinstance(actor_id, str) or not actor_id.strip():
            raise SpecError("each concurrency actor needs a non-empty string id")
        if actor_id in seen:
            raise SpecError(f"duplicate concurrency actor id {actor_id!r}")
        seen.add(actor_id)
        program_raw = entry.get("program")
        if not isinstance(program_raw, list) or not program_raw:
            raise SpecError(f"actor {actor_id}: program must be a non-empty list")
        steps: list[ProgramStep] = []
        for i, step in enumerate(program_raw):
            if not isinstance(step, dict):
                raise SpecError(f"actor {actor_id}: program[{i}] must be a mapping")
            action = step.get("action")
            if not isinstance(action, str) or not action:
                raise SpecError(f"actor {actor_id}: program[{i}] needs action")
            if not spec.has_action(action):
                raise SpecError(f"actor {actor_id}: unknown action {action!r}")
            a = spec.action(action)
            params_raw = step.get("params")
            choices = _param_choices(spec, a, params_raw, f"actor {actor_id} program[{i}]")
            steps.append(ProgramStep(action=action, param_choices=choices))
        actors.append(Actor(actor_id=actor_id, program=tuple(steps)))
    return ConcurrencyConfig(actors=tuple(actors))


def validate_concurrency(spec: Spec) -> None:
    parse_concurrency(spec)


def _param_choices(
    spec: Spec, action, params_raw: Any, where: str
) -> tuple[tuple[tuple[str, Any], ...], ...]:
    if not action.params:
        if params_raw is not None and params_raw != {}:
            raise SpecError(f"{where}: action {action.name} has no parameters")
        return ((),)
    if params_raw is None:
        domains: list[list[tuple[str, Any]]] = []
        for pn, pv in action.params.items():
            domain = _scalar_domain(pv)
            if not domain:
                raise SpecError(
                    f"{where}: cannot enumerate params for {action.name}.{pn}; "
                    "provide concrete params on the program step"
                )
            domains.append([(pn, v) for v in domain])
        combos: list[tuple[tuple[str, Any], ...]] = []
        for parts in itertools.product(*domains):
            combos.append(tuple(parts))
        if len(combos) > 64:
            raise SpecError(
                f"{where}: parameter enumeration would exceed 64 combinations; "
                "fix params on the program step"
            )
        return tuple(combos)
    if not isinstance(params_raw, dict):
        raise SpecError(f"{where}: params must be a mapping")
    for pn in action.params:
        if pn not in params_raw:
            raise SpecError(f"{where}: missing param {pn!r} for action {action.name}")
    for extra in params_raw:
        if extra not in action.params:
            raise SpecError(f"{where}: unknown param {extra!r} for action {action.name}")
    from .values import json_to_value

    fixed: list[tuple[str, Any]] = []
    for pn, pv in action.params.items():
        fixed.append(
            (
                pn,
                json_to_value(pv.type, params_raw[pn], f"{where}.{pn}"),
            )
        )
    return (tuple(fixed),)


def _scalar_domain(var) -> list[Any]:
    t = var.type
    if t.kind == "bool":
        return [False, True]
    if t.kind == "int":
        lo = var.min if var.min is not None else 0
        hi = var.max if var.max is not None else min(lo + 3, lo + 10)
        if hi < lo:
            return []
        span = hi - lo + 1
        if span > 8:
            return [lo, lo + 1, hi]
        return list(range(lo, hi + 1))
    if t.kind == "enum":
        return list(var.values)
    if t.kind == "str":
        return list(var.values) if var.values else []
    return []


def _footprint(spec: Spec, action_name: str) -> tuple[frozenset[str], frozenset[str]]:
    action = spec.action(action_name)
    reads: set[str] = set()
    writes: set[str] = set()
    state_names = set(spec.vars)
    for req in action.requires:
        reads |= _vars_in_expr(req, state_names)
    for var_name, effect in action.effects.items():
        writes.add(var_name)
        if isinstance(effect, dict):
            for fexpr in effect.values():
                reads |= _vars_in_expr(fexpr, state_names)
        else:
            reads |= _vars_in_expr(effect, state_names)
    return frozenset(reads), frozenset(writes)


def _vars_in_expr(expr: str, state_names: set[str]) -> set[str]:
    tree = parse_expr(expr)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in state_names:
            found.add(node.id)
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            if node.value.id in state_names:
                found.add(node.value.id)
    return found


@functools.cache
def _moves_independent(
    r1: frozenset[str], w1: frozenset[str], r2: frozenset[str], w2: frozenset[str]
) -> bool:
    if w1 & (r2 | w2):
        return False
    if w2 & (r1 | w1):
        return False
    return True


def _independent(m1: _Move, m2: _Move) -> bool:
    return _moves_independent(m1.reads, m1.writes, m2.reads, m2.writes)


def _ample_set(moves: list[_Move]) -> list[_Move]:
    if len(moves) <= 1:
        return moves
    ordered = sorted(moves, key=lambda m: (m.actor_index, m.step_index, m.action))
    ample: list[_Move] = [ordered[0]]
    for m in ordered[1:]:
        if any(not _independent(m, a) for a in ample):
            ample.append(m)
    for m in ordered:
        if m in ample:
            continue
        if all(_independent(m, a) for a in ample):
            continue
        if m not in ample:
            ample.append(m)
    if len(ample) < len(ordered):
        for m in ordered:
            if m not in ample and not all(_independent(m, a) for a in ample):
                return ordered
    return ample


def check(
    spec: Spec,
    bound: int = 10,
    invariant_filter: str | None = None,
) -> dict[str, Any]:
    """Search interleavings for an invariant violation up to ``bound`` steps."""
    if isinstance(bound, bool) or not isinstance(bound, int) or bound < 0:
        raise SpecError("bound must be a non-negative integer")
    cfg = parse_concurrency(spec)
    if cfg is None:
        raise SpecError(
            "spec has no concurrency.actors section; use `verispec check` or add concurrency"
        )
    init = initial_state(spec)
    indices = tuple(0 for _ in cfg.actors)
    trace: list[dict[str, Any]] = [{"step": 0, "state": value_to_json(spec.types, init)}]
    visited: set[tuple[Any, ...]] = set()

    violation = _dfs(
        spec,
        cfg,
        init,
        indices,
        bound,
        0,
        trace,
        visited,
        invariant_filter,
    )
    if violation is not None:
        return violation
    return {
        "status": "ok",
        "bound": bound,
        "max_bound": bound,
        "message": f"no concurrent violation up to bound {bound}",
        "guarantee": GUARANTEE_CONCURRENT.replace("(k)", f"({bound})"),
        "trace": trace[:1],
    }


def _dfs(
    spec: Spec,
    cfg: ConcurrencyConfig,
    state: dict[str, Any],
    indices: tuple[int, ...],
    bound: int,
    depth: int,
    trace: list[dict[str, Any]],
    visited: set[tuple[Any, ...]],
    invariant_filter: str | None,
) -> dict[str, Any] | None:
    key = _config_key(spec, state, indices)
    if key in visited:
        return None
    visited.add(key)

    bad = _state_violations(spec, state, invariant_filter, depth)
    if bad:
        return _violation_result(spec, bound, trace, bad)

    if depth >= bound:
        return None

    moves = _enabled_moves(spec, cfg, state, indices)
    if not moves:
        return None

    for move in _ample_set(moves):
        try:
            nxt = apply_action(spec, state, move.action, move.params)
        except SpecError:
            continue
        new_indices = list(indices)
        new_indices[move.actor_index] += 1
        entry = {
            "step": depth + 1,
            "actor": move.actor_id,
            "action": move.action,
            "params": move.params,
            "state": value_to_json(spec.types, nxt),
        }
        trace.append(entry)
        found = _dfs(
            spec,
            cfg,
            nxt,
            tuple(new_indices),
            bound,
            depth + 1,
            trace,
            visited,
            invariant_filter,
        )
        if found is not None:
            return found
        trace.pop()
    return None


def _config_key(spec: Spec, state: dict[str, Any], indices: tuple[int, ...]) -> tuple[Any, ...]:
    frozen = json.dumps(value_to_json(spec.types, state), sort_keys=True)
    return (indices, frozen)


def _enabled_moves(
    spec: Spec, cfg: ConcurrencyConfig, state: dict[str, Any], indices: tuple[int, ...]
) -> list[_Move]:
    moves: list[_Move] = []
    for ai, actor in enumerate(cfg.actors):
        si = indices[ai]
        if si >= len(actor.program):
            continue
        step = actor.program[si]
        reads, writes = _footprint(spec, step.action)
        for combo in step.param_choices:
            params = dict(combo)
            action = spec.action(step.action)
            env = {**state, **params}
            from .expr import eval_python

            ok = True
            for req in action.requires:
                if not eval_python(req, env, spec.tenv(action)):
                    ok = False
                    break
            if not ok:
                continue
            moves.append(
                _Move(
                    actor_index=ai,
                    actor_id=actor.actor_id,
                    step_index=si,
                    action=step.action,
                    params=params,
                    reads=reads,
                    writes=writes,
                )
            )
    return moves


def _state_violations(
    spec: Spec, state: dict[str, Any], invariant_filter: str | None, step: int
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for inv in check_invariants(spec, state):
        if invariant_filter and inv["name"] != invariant_filter:
            continue
        found.append(
            {
                "name": inv["name"],
                "kind": "invariant",
                "expr": inv["expr"],
                "step": step,
                "state": value_to_json(spec.types, state),
            }
        )
    if invariant_filter:
        return found
    for name, var in spec.vars.items():
        if isinstance(var.type, (MapType, SetType, SeqType)):
            size = len(state[name])
            if size > var.type.max_size:
                found.append(
                    {
                        "name": f"{name}_capacity",
                        "kind": "bound",
                        "expr": f"len({name}) <= {var.type.max_size}",
                        "step": step,
                        "state": value_to_json(spec.types, state),
                    }
                )
    return found


def _violation_result(
    spec: Spec, bound: int, trace: list[dict[str, Any]], violations: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "status": "violation",
        "bound": bound,
        "depth": len(trace) - 1,
        "trace": list(trace),
        "violated": [v["name"] for v in violations],
        "violations": violations,
        "message": (
            "CONCURRENT COUNTEREXAMPLE found within bound "
            f"{bound}: invariant(s) violated: "
            + ", ".join(v["name"] for v in violations)
        ),
        "guarantee": "violation-found",
    }


def format_result(spec: Spec, result: dict[str, Any]) -> str:
    """Human readable rendering of a concurrent check result."""
    if result["status"] == "ok":
        return (
            f"{result['message']}  (interleaving exploration only up to the bound; "
            f"guarantee: {result.get('guarantee', GUARANTEE_CONCURRENT)})"
        )
    if result["status"] != "violation":
        return result.get("message", str(result))
    lines = [result["message"]]
    for entry in result.get("trace", []):
        actor = entry.get("actor")
        prefix = f"actor {actor} " if actor else ""
        lines.append(f"  step {entry['step']}: state={entry['state']}")
        if "action" in entry:
            lines.append(
                f"    -> {prefix}action {entry['action']} params={entry.get('params', {})}"
            )
    for v in result.get("violations", []):
        lines.append(f"  violated at step {v['step']}: {v['name']} ({v['kind']}) {v['expr']}")
    lines.append(
        "  This is a concurrent counterexample: reproduce the interleaving, fix the "
        "implementation, re-run. Do not weaken the spec."
    )
    return "\n".join(lines)
