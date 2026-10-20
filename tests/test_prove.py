"""Unbounded proofs: k-induction and CLI wiring."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from verispec.cli import EXIT_OK, EXIT_VIOLATION, main  # noqa: E402
from verispec.expr import SpecError  # noqa: E402
from verispec.model import load_spec  # noqa: E402
from verispec.prove import GUARANTEE_PROVED, prove  # noqa: E402

BANK = ROOT / "examples" / "bank.yaml"
BANK_BUGGY = ROOT / "examples" / "bank-buggy.yaml"
NON_INDUCTIVE = ROOT / "tests" / "fixtures" / "non_inductive.yaml"
TEMPORAL = ROOT / "tests" / "fixtures" / "temporal-fair.yaml"


def spec(path):
    return load_spec(path)


def test_prove_bank():
    r = prove(spec(BANK), k=1, timeout_ms=60000)
    assert r["status"] == "ok", r
    assert r["guarantee"] == GUARANTEE_PROVED


def test_prove_safety_only_when_spec_has_temporal():
    r = prove(spec(TEMPORAL), k=1, precheck_bound=4, timeout_ms=60000)
    assert r.get("safety_only") is True
    assert r.get("temporal_scope") == "not-proved"
    assert r.get("temporal_note")


def test_prove_finds_real_violation_before_induction():
    r = prove(spec(BANK_BUGGY), k=1, timeout_ms=60000)
    assert r["status"] == "violation"
    assert r["guarantee"] == "violation-found"


def test_prove_reports_counterexample_to_induction():
    # shallow precheck passes; invariant is not inductive
    r = prove(spec(NON_INDUCTIVE), k=1, precheck_bound=1, timeout_ms=60000)
    assert r["status"] == "unknown"
    assert r["guarantee"] == "unknown"
    assert r["reason"] == "counterexample-to-induction"
    assert r.get("induction_cex")


def test_prove_cli_json(capsys):
    code, out, _ = _run(capsys, "prove", str(BANK), "--format", "json", "--timeout", "60000")
    assert code == EXIT_OK
    payload = json.loads(out)
    assert payload["guarantee"] == GUARANTEE_PROVED
    assert payload["command"] == "prove"


def test_check_unbounded_flag(capsys):
    code, out, _ = _run(
        capsys, "check", str(BANK), "--unbounded", "--format", "json", "--timeout", "60000"
    )
    assert code == EXIT_OK
    payload = json.loads(out)
    assert payload["command"] == "prove"
    assert payload["guarantee"] == GUARANTEE_PROVED


def test_prove_invalid_k():
    with pytest.raises(SpecError):
        prove(spec(BANK), k=0)


def test_prove_unknown_exits_nonzero(capsys):
    code, _, _ = _run(
        capsys, "prove", str(NON_INDUCTIVE), "--bound", "1", "--timeout", "60000"
    )
    assert code == EXIT_VIOLATION


def _run(capsys, *args):
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err
