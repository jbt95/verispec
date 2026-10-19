"""The examples have to keep working.

The complex example (a REST API with a database) lives in
`examples/orders-api` and has its own test suite, because it needs FastAPI.
This test runs it, so `pytest -q` covers the example too.

The simple examples are covered by the corpus runner
(`verispec corpus corpus`), which the CI also runs.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "orders-api"


def test_the_orders_api_example_passes():
    pytest.importorskip("fastapi", reason="the orders API example needs fastapi")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(EXAMPLE)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_example_spec_is_valid_and_clean():
    from verispec.model import load_spec

    spec = load_spec(EXAMPLE / "orders.verispec.yaml")
    assert spec.invariants, "the example must state the business rules"
    assert spec.has_action("place_order")
    assert spec.has_action("pay")


def test_the_example_traces_conform():
    import json

    from verispec.model import load_spec
    from verispec.trace import check_trace_data

    spec = load_spec(EXAMPLE / "orders.verispec.yaml")
    trace = json.loads((EXAMPLE / "traces" / "orders-session.json").read_text())
    result = check_trace_data(spec, trace)
    assert result["ok"], result["detail"]
