"""Regression coverage for trust-boundary defects found in the readiness audit."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from verispec.bmc import check
from verispec.codegen import to_go, to_ts
from verispec.errors import SpecError
from verispec.model import load_spec_data
from verispec.monitor import monitor_trace
from verispec.sign import sign_spec, verify_signature


def _spec(invariant: str, *, init: dict | None = None, actions: list | None = None):
    variables = {"x": {"type": "int", "min": -20, "max": 20}}
    initial = {"x": 5}
    if init:
        initial.update(init)
    return load_spec_data(
        {
            "spec_version": "1.0",
            "name": "Regression",
            "vars": variables,
            "init": initial,
            "actions": actions or [],
            "invariants": [{"name": "expected", "expr": invariant}],
        }
    )


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("x // -2 == -3", "ok"),
        ("x // -2 == -2", "violation"),
        ("x % -2 == -1", "ok"),
        ("x % -2 == 1", "violation"),
    ],
)
def test_bmc_matches_python_negative_division(expression, expected):
    assert check(_spec(expression), bound=0)["status"] == expected


def test_bmc_fails_closed_for_symbolic_divisors():
    spec = load_spec_data(
        {
            "spec_version": "1.0",
            "name": "Symbolic divisor",
            "vars": {
                "x": {"type": "int", "min": -10, "max": 10},
                "y": {"type": "int", "min": -10, "max": 10},
            },
            "init": {"x": 5, "y": 1},
            "invariants": [{"name": "division", "expr": "x // y == 5"}],
        }
    )
    result = check(spec, bound=0)
    assert result["status"] == "unknown"
    assert "nonzero" in result["message"]


def test_bmc_rejects_invalid_bounds():
    spec = _spec("x == 5")
    for bound in (-1, True, 1.5):
        with pytest.raises(SpecError, match="non-negative integer"):
            check(spec, bound=bound)


def test_bmc_rejects_literal_zero_division():
    spec = load_spec_data(
        {
            "spec_version": "1.0",
            "name": "Zero divisor",
            "vars": {"x": {"type": "int", "min": 0, "max": 10}},
            "init": {"x": 1},
            "actions": [{"name": "crash", "effects": {"x": "1 // 0"}}],
            "invariants": [{"name": "safe", "expr": "x == 1"}],
        }
    )
    with pytest.raises(SpecError, match="division by zero"):
        check(spec, bound=1)


def test_bmc_solver_symbols_do_not_collide_with_user_names():
    spec = load_spec_data(
        {
            "spec_version": "1.0",
            "name": "Internal-looking user variable",
            "vars": {
                "__action": {"type": "int", "min": -1, "max": 1},
                "bad": {"type": "int", "min": 0, "max": 1},
            },
            "init": {"__action": -1, "bad": 0},
            "actions": [{"name": "break", "effects": {"bad": "1"}}],
            "invariants": [{"name": "safe", "expr": "bad == 0"}],
        }
    )
    assert check(spec, bound=1)["status"] == "violation"


def test_generated_typescript_and_go_use_python_integer_arithmetic():
    spec = _spec("x == 5")
    assert "veriFloorDiv(" in to_ts("x // -2", spec.types, {"x"})
    assert "veriMod(" in to_ts("x % -2", spec.types, {"x"})
    assert "veriFloorDiv(" in to_go("x // -2", spec.types, {"x"})
    assert "veriMod(" in to_go("x % -2", spec.types, {"x"})


def test_requested_gpg_signing_fails_closed(monkeypatch):
    from verispec.model import load_spec

    monkeypatch.setenv("VERISPEC_NO_GPG", "1")
    with pytest.raises(SpecError, match="GPG signing was requested"):
        sign_spec(load_spec("examples/bank.yaml"), "alice", use_gpg=True)


def test_digest_only_signature_can_be_rejected_when_crypto_is_required(tmp_path):
    from verispec.model import load_spec

    spec = load_spec("examples/bank.yaml")
    signature_path = tmp_path / "spec.signature.json"
    signature_path.write_text(json.dumps(sign_spec(spec, "alice")))
    assert verify_signature(spec, signature_path)["ok"] is True
    result = verify_signature(spec, signature_path, require_cryptographic=True)
    assert result["ok"] is False
    assert result["reason"] == "cryptographic-signature-required"


@pytest.mark.parametrize("contents", ["", "null", "42", "{broken"])
def test_monitor_does_not_report_invalid_or_empty_input_as_conformant(tmp_path, contents):
    from verispec.model import load_spec

    trace_path = tmp_path / "trace.json"
    trace_path.write_text(contents)
    result = monitor_trace(load_spec("examples/bank.yaml"), trace_path)
    assert result["ok"] is False
    assert result["status"] == "invalid"


def test_monitor_rejects_invalid_watch_intervals():
    from verispec.model import load_spec
    from verispec.monitor import watch_trace

    spec = load_spec("examples/bank.yaml")
    for interval in (0, -1, float("nan"), float("inf"), True):
        with pytest.raises(ValueError, match="finite positive"):
            watch_trace(spec, Path("missing-trace.json"), interval=interval)


def test_monitor_malformed_step_and_runtime_evaluation_errors_fail_closed(tmp_path):
    from verispec.model import initial_state, load_spec

    bank = load_spec("examples/bank.yaml")
    trace_path = tmp_path / "trace.json"
    trace_path.write_text(json.dumps([{"state": initial_state(bank)}, {"state": initial_state(bank), "action": []}]))
    assert monitor_trace(bank, trace_path)["ok"] is False

    division_spec = load_spec_data(
        {
            "spec_version": "1.0",
            "name": "Observed division by zero",
            "vars": {
                "x": {"type": "int", "min": -5, "max": 5},
                "y": {"type": "int", "min": -5, "max": 5},
            },
            "init": {"x": 0, "y": 1},
            "invariants": [{"name": "safe", "expr": "x // y == 0"}],
        }
    )
    trace_path.write_text(json.dumps([{"state": {"x": 0, "y": 0}}]))
    result = monitor_trace(division_spec, trace_path)
    assert result["ok"] is False
    assert result["reason"] == "evaluation-error"


def test_trace_checker_rejects_missing_files_and_non_object_steps(tmp_path):
    from verispec.model import load_spec
    from verispec.trace import check_trace, check_trace_data

    spec = load_spec("examples/bank.yaml")
    assert check_trace(spec, tmp_path / "missing.json")["reason"] == "invalid-trace"
    assert check_trace_data(spec, [None])["reason"] == "invalid-trace"


def test_mcp_rejects_malformed_requests_and_aggregates_trace_failures():
    from verispec.mcp import _tool_invariants, _tool_verify, handle_request

    assert handle_request(None)["error"]["code"] == -32600
    assert handle_request({"jsonrpc": "1.0", "id": 1, "method": "ping"})["error"]["code"] == -32600
    assert handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": []})[
        "error"
    ]["code"] == -32602
    invalid_args = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "verify", "arguments": {"spec_path": None}},
        }
    )
    assert invalid_args["error"]["code"] == -32602

    bank = Path("examples/bank.yaml")
    invariant_text, invariant_error = _tool_invariants({"spec_path": str(bank)})
    invariant_payload = json.loads(invariant_text)
    assert invariant_error is False
    assert invariant_payload["invariants"][0]["guarantee"] == "not-checked"

    bad_trace = Path("examples/traces/bank-invalid.json")
    text, is_error = _tool_verify(
        {"spec_path": str(bank), "trace_path": str(bad_trace), "bound": 1}
    )
    result = json.loads(text)
    assert result["status"] == "violation"
    assert result["trace_check"]["ok"] is False
    assert is_error is True
