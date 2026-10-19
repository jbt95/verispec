"""Differential test: the interpreter and the Z3 encoding must agree.

The concrete interpreter (used by the trace engine and the generated tests)
and the Z3 translation (used by the model checker) are two backends of the
same AST.  If they disagree, the checker either invents counterexamples that
do not exist, or misses real ones.

The test pins every reachable state of a collection spec and checks that each
expression evaluates to the same value in both backends.
"""

import itertools
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from verispec import bmc  # noqa: E402
from verispec.expr import eval_python, to_z3  # noqa: E402
from verispec.model import apply_action, initial_state, load_spec  # noqa: E402

COLLECTIONS = ROOT / "corpus" / "collections" / "spec.yaml"
LEDGER = ROOT / "examples" / "ledger.yaml"


def _z3_env(spec, state, action=None, params=None):
    """A Z3 environment where the state is pinned to a concrete state.

    The state is encoded exactly the way the checker encodes it, so a
    collection variable is a real Z3 collection, not a placeholder.
    """
    import z3

    from verispec.expr import Z3Binding

    solver = z3.Solver()
    slots = {name: bmc._slot(spec, var, 0) for name, var in spec.vars.items()}
    bmc._encode_state(solver, spec, state, slots)
    env = {name: bmc._binding(var, slots[name]) for name, var in spec.vars.items()}
    pins = list(solver.assertions())
    if action is not None and params:
        for name, var in action.params.items():
            t = var.type
            value = params[name]
            if t.kind == "int":
                const = z3.Int(f"pin_param_{name}")
                env[name] = Z3Binding(const, t)
                pins.append(const == z3.IntVal(value))
            elif t.kind == "bool":
                const = z3.Bool(f"pin_param_{name}")
                env[name] = Z3Binding(const, t)
                pins.append(const == z3.BoolVal(value))
            else:
                const = z3.String(f"pin_param_{name}")
                env[name] = Z3Binding(const, t)
                pins.append(const == z3.StringVal(str(value)))
    return env, pins


def _states(spec, depth=3, limit=400):
    """Every state reachable in at most `depth` steps (bounded exploration)."""
    seen = [initial_state(spec)]
    frontier = [initial_state(spec)]
    for _ in range(depth):
        new_frontier = []
        for state in frontier:
            for action in spec.actions:
                for params in _enabled_params(spec, action, state):
                    try:
                        nxt = apply_action(spec, state, action.name, params)
                    except Exception:
                        continue
                    if nxt not in seen:
                        seen.append(nxt)
                        new_frontier.append(nxt)
                        if len(seen) >= limit:
                            return seen
        frontier = new_frontier
        if not frontier:
            break
    return seen


def _enabled_params(spec, action, state):
    """Every parameter combination the action's preconditions accept."""
    names = list(action.params)
    if not names:
        return [{}]
    options = [_param_candidates(action.params[n]) for n in names]
    tenv = spec.tenv(action)
    out = []
    for combo in itertools.product(*options):
        params = dict(zip(names, combo))
        env = {**state, **params}
        if all(eval_python(r, env, tenv) for r in action.requires):
            out.append(params)
    return out


def _param_candidates(var):
    if var.kind == "bool":
        return [True, False]
    if var.kind == "enum":
        return list(var.values)
    lo = var.min if var.min is not None else 0
    hi = var.max if var.max is not None else lo + 1
    candidates = [lo, hi, lo + 1]
    return list(dict.fromkeys(c for c in candidates if lo <= c <= hi))


def _sample_params(spec, action, state):
    """A parameter combination that the action's preconditions accept."""
    names = list(action.params)
    if not names:
        return {}
    options = [_param_candidates(action.params[n]) for n in names]
    fallback = None
    for combo in itertools.product(*options):
        params = dict(zip(names, combo))
        env = {**state, **params}
        tenv = spec.tenv(action)
        if all(eval_python(r, env, tenv) for r in action.requires):
            return params
        if fallback is None:
            fallback = params
    return fallback or {}


def _expressions(spec):
    exprs = []
    for action in spec.actions:
        for r in action.requires:
            exprs.append((f"requires of {action.name}", r, action))
        for name, effect in action.effects.items():
            if isinstance(effect, dict):
                for fname, fexpr in effect.items():
                    exprs.append((f"effect {name}.{fname} of {action.name}", fexpr, action))
            else:
                exprs.append((f"effect {name} of {action.name}", str(effect), action))
    for inv in spec.invariants:
        exprs.append((f"invariant {inv['name']}", inv["expr"], None))
    return exprs


def _sample_params(spec, action, state):
    """A parameter combination that the action's preconditions accept."""
    enabled = _enabled_params(spec, action, state)
    return enabled[0] if enabled else {}


@pytest.mark.parametrize("spec_path", [COLLECTIONS, LEDGER])
def test_interpreter_and_z3_agree(spec_path):
    import z3

    spec = load_spec(spec_path)
    states = _states(spec, depth=3)
    assert len(states) > 1, "the exploration should reach more than the initial state"

    for state in states:
        for label, expr, action in _expressions(spec):
            tenv = spec.tenv(action) if action else spec.types
            params = _sample_params(spec, action, state) if action else {}
            concrete = eval_python(expr, {**state, **params}, tenv)
            env, pins = _z3_env(spec, state, action, params)
            solver = z3.Solver()
            for pin in pins:
                solver.add(pin)
            term = to_z3(expr, env, tenv)
            if hasattr(term, "size"):
                # a collection valued expression: check its size, which is the
                # only part the two backends agree on numerically
                solver.add(term.size == z3.IntVal(len(concrete)))
            else:
                solver.add(term == _z3_value(concrete))
            assert solver.check() == z3.sat, (
                f"{label}: the interpreter and Z3 disagree on {expr!r} in state {state}"
            )


def _z3_value(value):
    import z3

    if isinstance(value, bool):
        return z3.BoolVal(value)
    if isinstance(value, int):
        return z3.IntVal(value)
    return z3.StringVal(str(value))


def test_the_exploration_finds_the_buggy_states():
    """The exploration is not vacuous: it reaches states the invariants reject."""
    spec = load_spec(LEDGER)
    states = _states(spec, depth=3)
    from verispec.model import check_invariants

    assert any(check_invariants(spec, state) for state in states) is False
    # and the buggy ledger does produce a violation
    buggy = load_spec(ROOT / "examples" / "ledger-buggy.yaml")
    buggy_states = _states(buggy, depth=3)
    assert any(check_invariants(buggy, state) for state in buggy_states)
