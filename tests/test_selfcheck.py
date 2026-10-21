"""The tool auditing itself: `verispec self-check`."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from verispec.guarantees import (  # noqa: E402
    EVIDENCE_LEVELS,
    GUARANTEE_BOUNDED,
    GUARANTEE_CONFORMANCE,
    GUARANTEE_LIVENESS,
    GUARANTEE_PROVED,
    GUARANTEES,
    combine,
    scope_note,
    scope_of,
)
from verispec.selfcheck import run_self_check  # noqa: E402


def test_self_check_passes_on_a_healthy_checkout():
    result = run_self_check(root=str(ROOT))
    assert result["status"] == "ok", result["findings"]
    assert result["guarantee"] == "self-consistent"
    assert len(result["checks"]) > 10


def test_every_label_has_a_scope_and_a_meaning():
    for label in GUARANTEES:
        assert label in GUARANTEES
        assert scope_of(label) in {"contract", "implementation", "digest"}
        assert GUARANTEES[label].strip()


def test_a_contract_label_says_it_is_about_the_contract():
    for label in (GUARANTEE_BOUNDED, GUARANTEE_PROVED, GUARANTEE_LIVENESS):
        assert "not about the implementation" in scope_note(label)


def test_a_trace_label_says_it_is_about_the_trace():
    assert scope_of(GUARANTEE_CONFORMANCE) == "implementation"
    assert "not about every input" in scope_note(GUARANTEE_CONFORMANCE)


def test_labels_combine_without_losing_any():
    both = combine([GUARANTEE_BOUNDED, GUARANTEE_LIVENESS])
    assert GUARANTEE_BOUNDED in both
    assert GUARANTEE_LIVENESS in both
    assert combine([GUARANTEE_BOUNDED, GUARANTEE_BOUNDED]) == GUARANTEE_BOUNDED
    assert combine([]) != ""


def test_the_evidence_levels_are_described():
    for level in EVIDENCE_LEVELS:
        assert EVIDENCE_LEVELS[level].strip()
    assert "bounded-liveness-proved" in EVIDENCE_LEVELS
