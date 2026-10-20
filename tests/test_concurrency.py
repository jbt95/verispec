"""Concurrent interleaving exploration and trace actor labels."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from verispec.cli import EXIT_OK, EXIT_VIOLATION, main  # noqa: E402
from verispec.concurrency import check, parse_concurrency  # noqa: E402
from verispec.model import load_spec  # noqa: E402
from verispec.trace import check_trace_data  # noqa: E402

RACE = str(ROOT / "examples" / "bank-concurrent-race.yaml")
BANK = str(ROOT / "examples" / "bank.yaml")


def run(capsys, *args):
    code = main(list(args))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_concurrent_race_finds_violation():
    spec = load_spec(RACE)
    result = check(spec, bound=6)
    assert result["status"] == "violation"
    assert "non_negative" in result["violated"]
    actors = [e.get("actor") for e in result["trace"] if e.get("action")]
    assert "client1" in actors and "client2" in actors


def test_sequential_bank_ok_under_concurrent_checker_errors():
    import pytest

    from verispec.errors import SpecError

    spec = load_spec(BANK)
    with pytest.raises(SpecError, match="no concurrency"):
        check(spec, bound=3)


def test_independence_skips_redundant_interleaving():
    spec = load_spec(RACE)
    r1 = check(spec, bound=6)
    assert r1["status"] == "violation"
    depth = r1["depth"]
    assert depth <= 4


def test_cli_check_concurrent(capsys):
    code, out, _ = run(capsys, "check-concurrent", RACE, "--bound", "6")
    assert code == EXIT_VIOLATION
    assert "CONCURRENT COUNTEREXAMPLE" in out
    assert "client1" in out or "client2" in out


def test_cli_check_concurrent_ok_bound_zero(capsys):
    code, out, _ = run(capsys, "check-concurrent", RACE, "--bound", "0")
    assert code == EXIT_OK
    assert "no concurrent violation" in out


def test_cli_check_concurrent_json(capsys):
    code, out, _ = run(capsys, "check-concurrent", RACE, "--bound", "6", "--format", "json")
    assert code == EXIT_VIOLATION
    payload = json.loads(out)
    assert payload["command"] == "check-concurrent"
    assert payload["trace"]
    assert any(step.get("actor") for step in payload["trace"])


def test_trace_accepts_actor_when_concurrency_declared():
    spec = load_spec(RACE)
    init = {"balance_a": 30, "balance_b": 70}
    trace = [
        {"state": init},
        {
            "actor": "client1",
            "action": "xfer_a_to_b_check",
            "params": {"amount": 25},
            "state": init,
        },
        {
            "actor": "client1",
            "action": "xfer_a_to_b_commit",
            "params": {"amount": 25},
            "state": {"balance_a": 5, "balance_b": 95},
        },
    ]
    result = check_trace_data(spec, trace)
    assert result["ok"]


def test_trace_requires_actor_for_concurrent_spec():
    spec = load_spec(RACE)
    trace = [
        {"state": {"balance_a": 30, "balance_b": 70}},
        {
            "action": "xfer_a_to_b_check",
            "params": {"amount": 25},
            "state": {"balance_a": 30, "balance_b": 70},
        },
    ]
    result = check_trace_data(spec, trace)
    assert not result["ok"]
    assert result["reason"] == "missing-actor"


def test_bank_trace_still_valid_without_actor():
    from verispec.trace import check_trace

    result = check_trace(load_spec(BANK), ROOT / "examples" / "traces" / "bank-valid.json")
    assert result["ok"]


def test_parse_concurrency_rejects_duplicate_ids():
    data = {
        "name": "X",
        "vars": {"x": {"type": "int", "min": 0, "max": 1}},
        "init": {"x": "0"},
        "actions": [{"name": "noop", "requires": [], "effects": {}}],
        "invariants": [],
        "concurrency": {
            "actors": [
                {"id": "a", "program": [{"action": "noop"}]},
                {"id": "a", "program": [{"action": "noop"}]},
            ]
        },
    }
    from verispec.model import load_spec_data

    try:
        load_spec_data(data)
    except Exception as exc:
        assert "duplicate" in str(exc).lower()
    else:  # pragma: no cover
        raise AssertionError("expected duplicate actor error")


def test_config_round_trip():
    spec = load_spec(RACE)
    cfg = parse_concurrency(spec)
    assert cfg is not None
    assert len(cfg.actors) == 2
