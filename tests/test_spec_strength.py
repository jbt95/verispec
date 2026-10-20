"""Tests for spec-strength / trace mutation diagnostics."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from verispec.model import load_spec  # noqa: E402
from verispec.spec_strength import (  # noqa: E402
    classify_trace_failure,
    replay_steps,
    run_spec_strength,
    steps_from_trace,
)
from verispec.trace import load_trace  # noqa: E402


def spec(name: str):
    return load_spec(ROOT / "examples" / name)


def test_classify_failure_taxonomy():
    assert classify_trace_failure("invariant", "drop_step") == "invariant_contradiction"
    assert classify_trace_failure("effect-mismatch", "swap_steps") == "misordered_step"


def test_mutants_caught_on_bank_trace():
    trace = load_trace(ROOT / "examples/traces/bank-valid.json")
    report = run_spec_strength(spec("bank.yaml"), trace=trace)
    assert report.base_steps == 2
    assert report.caught > 0
    assert any(m.kind in ("drop_step", "swap_steps") and m.caught for m in report.mutants)
    for m in report.mutants:
        if m.caught:
            assert m.failure_kind is not None


def test_replay_steps_round_trip():
    trace = load_trace(ROOT / "examples/traces/bank-valid.json")
    steps = steps_from_trace(trace)
    rebuilt = replay_steps(spec("bank.yaml"), steps)
    assert len(rebuilt) == len(trace)


def test_model_walk_generates_mutants():
    report = run_spec_strength(spec("bank.yaml"), walk_length=3, seed=1, max_mutants=5)
    assert report.base_steps >= 1
    assert len(report.mutants) <= 5
