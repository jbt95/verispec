"""Tests for refinement checking between concrete and abstract specs."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from verispec.cli import EXIT_OK, EXIT_VIOLATION, main  # noqa: E402
from verispec.model import load_spec  # noqa: E402
from verispec.refinement import (  # noqa: E402
    check_refinement,
    load_refinement_mapping,
    resolve_abstract_spec,
)

CONCRETE = ROOT / "examples" / "bank-concrete-pending.yaml"
CONCRETE_BUGGY = ROOT / "examples" / "bank-concrete-pending-buggy.yaml"
BANK = ROOT / "examples" / "bank.yaml"


def _pair(concrete_path: Path):
    concrete = load_spec(concrete_path)
    mapping = load_refinement_mapping(concrete)
    abstract = resolve_abstract_spec(concrete, mapping)
    return concrete, abstract, mapping


def test_refinement_ok_bounded():
    concrete, abstract, mapping = _pair(CONCRETE)
    result = check_refinement(concrete, abstract, mapping, bound=6)
    assert result["status"] == "ok"
    assert "bounded-refinement-proved" in result["guarantee"]


def test_refinement_finds_buggy_concrete():
    concrete, abstract, mapping = _pair(CONCRETE_BUGGY)
    result = check_refinement(concrete, abstract, mapping, bound=8)
    assert result["status"] == "violation"
    assert result["concrete_trace"]
    assert result["abstract_trace"]


def test_cli_refines_ok(capsys):
    code = main(["refines", str(CONCRETE), "--bound", "5"])
    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "no refinement violation" in out


def test_cli_refines_violation_json(capsys):
    code = main(["refines", str(CONCRETE_BUGGY), "--bound", "8", "--format", "json"])
    out = capsys.readouterr().out
    assert code == EXIT_VIOLATION
    payload = json.loads(out)
    assert payload["status"] == "violation"
    assert payload["concrete"]["name"] == "BankConcretePendingBuggy"
    assert payload["abstract"]["name"] == "Bank"
    assert payload["concrete_trace"]


def test_load_mapping_requires_all_actions():
    concrete = load_spec(BANK)
    try:
        load_refinement_mapping(concrete)
    except Exception as e:
        assert "refines" in str(e).lower()
    else:
        raise AssertionError("expected SpecError for missing refines block")
