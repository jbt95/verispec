"""Tests for the escalating verification loop."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from verispec.model import load_spec  # noqa: E402
from verispec.trace import load_trace  # noqa: E402
from verispec.verify_loop import run_verify_loop  # noqa: E402


def spec(name: str):
    return load_spec(ROOT / "examples" / name)


def test_verify_loop_success_on_bank():
    report = run_verify_loop(spec("bank.yaml"), max_rounds=2, z3_bound=6, fuzz_trials=20)
    assert report.verdict == "success"
    assert all(c.status == "satisfied" for c in report.checks)
    assert report.cost["rounds_used"] >= 1
    assert report.cost["backend_calls"] >= 1


def test_verify_loop_finds_buggy_bank():
    report = run_verify_loop(spec("bank-buggy.yaml"), max_rounds=2, z3_bound=8, fuzz_trials=30)
    assert report.verdict == "failure"
    assert report.stopped_at is not None
    violated = [c for c in report.checks if c.status == "violated"]
    assert violated
    assert violated[0].evidence_location
    assert violated[0].counterexample


def test_verify_loop_with_trace_marks_conformance():
    trace = load_trace(ROOT / "examples/traces/bank-valid.json")
    report = run_verify_loop(
        spec("bank.yaml"),
        trace=trace,
        max_rounds=1,
        fuzz_trials=5,
    )
    assert report.verdict == "success"
    trace_check = next(c for c in report.checks if c.check_id == "trace:conformance")
    assert trace_check.status == "satisfied"
    assert trace_check.backend == "trace-monitor"


def test_verify_loop_stops_at_first_violation():
    trace = load_trace(ROOT / "examples/traces/bank-invalid.json")
    report = run_verify_loop(spec("bank.yaml"), trace=trace, max_rounds=2)
    assert report.verdict == "failure"
    assert report.stopped_at == "trace:conformance"


def test_verify_loop_records_cost():
    report = run_verify_loop(spec("bank.yaml"), max_rounds=2, z3_bound=4)
    assert "duration_ms" in report.cost
    assert report.cost["rounds_used"] <= 2
