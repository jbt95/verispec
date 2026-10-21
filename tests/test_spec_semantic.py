"""VerusEval-style semantic strength checks."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from verispec.model import load_spec  # noqa: E402
from verispec.spec_semantic import (  # noqa: E402
    load_io_suite,
    run_io_validation,
    run_reference_compare,
    run_vacuity_checks,
)
from verispec.spec_strength import run_spec_strength  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "spec_semantic"


def test_vacuity_flags_tautology():
    spec = load_spec(FIX / "bank-vacuous.yaml")
    report = run_vacuity_checks(spec, timeout_ms=5000)
    codes = {f.code for f in report.findings if f.status == "proved"}
    assert "true-postcondition" in codes


def test_io_flags_weak_spec_accepting_bad_output():
    spec = load_spec(FIX / "bank-vacuous.yaml")
    cases = load_io_suite(FIX / "bank-io.json")
    report = run_io_validation(spec, cases, auto_mutate=False)
    wrong = [r for r in report.results if r.case.label == "negative-balance-weak-accepts"]
    assert wrong and not wrong[0].ok


def test_io_accepts_correct_on_sound_spec():
    from verispec.spec_semantic import IoCase

    spec = load_spec(ROOT / "examples" / "bank.yaml")
    correct = [
        IoCase(
            kind="correct",
            label="sound-transfer",
            trace=[
                {"action": "init", "params": {}, "state": {"balance_a": 50, "balance_b": 50}},
                {
                    "action": "transfer_a_to_b",
                    "params": {"amount": 10},
                    "state": {"balance_a": 40, "balance_b": 60},
                },
            ],
        )
    ]
    report = run_io_validation(spec, correct, auto_mutate=True, max_auto_per_case=1)
    assert all(r.ok for r in report.results if r.case.kind == "correct")


def test_reference_weaker_than_full_bank():
    weak = load_spec(FIX / "bank-vacuous.yaml")
    ref = load_spec(FIX / "bank-reference.yaml")
    cmp = run_reference_compare(weak, ref, bound=4, timeout_ms=8000)
    assert cmp.relation in ("weaker", "incomparable", "unknown")


def test_spec_strength_semantic_verdict_on_vacuous():
    spec = load_spec(FIX / "bank-vacuous.yaml")
    report = run_spec_strength(spec, walk_length=2, seed=0, max_mutants=2)
    assert report.semantic_verdict == "vacuous"


def test_bank_non_negative_redundant_with_domain():
    """Passes trace mutants today but semantic check flags redundant invariant."""
    spec = load_spec(ROOT / "examples" / "bank.yaml")
    report = run_vacuity_checks(spec, timeout_ms=5000)
    names = {f.where for f in report.findings if f.code == "true-postcondition"}
    assert "non_negative" in names
