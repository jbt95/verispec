"""Cross-feature integration tests for stabilization."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from verispec.cli import EXIT_OK, EXIT_SPEC_ERROR, EXIT_VIOLATION, main

BANK = str(ROOT / "examples" / "bank.yaml")
TEMPORAL = str(ROOT / "tests" / "fixtures" / "temporal-fair.yaml")
TRACE_OK = str(ROOT / "examples" / "traces" / "bank-valid.json")


def run(capsys, *args: str) -> tuple[int, str, str]:
    code = main(list(args))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_check_suite_cache_with_liveness_spec(capsys, tmp_path):
    cache = tmp_path / "cache"
    code, out, err = run(
        capsys,
        "check-suite",
        TEMPORAL,
        "--bound",
        "4",
        "--cache-dir",
        str(cache),
        "--format",
        "json",
    )
    assert code in (EXIT_OK, EXIT_VIOLATION)
    assert not err.strip()
    payload = json.loads(out)
    assert payload["command"] == "check-suite"
    assert payload["specs"] == 1
    code2, _, _ = run(
        capsys,
        "check-suite",
        TEMPORAL,
        "--bound",
        "4",
        "--cache-dir",
        str(cache),
        "--format",
        "json",
    )
    assert code2 == code
    assert payload["results"][0]["result"].get("from_cache") or cache.exists()


def test_policy_check_repo_policy(capsys):
    code, out, err = run(capsys, "policy", "check", "--format", "json")
    assert code == EXIT_OK
    assert not err.strip()
    payload = json.loads(out)
    assert payload["command"] == "policy-check"
    assert payload.get("ok") is True


def test_audit_report_json_includes_temporal_and_coverage(capsys):
    code, out, _ = run(
        capsys,
        "report",
        BANK,
        "--trace",
        TRACE_OK,
        "--bound",
        "4",
        "--format",
        "json",
    )
    assert code == EXIT_OK
    payload = json.loads(out)
    assert "temporal" in payload["spec"]
    assert "fairness" in payload["spec"]
    checks = {r["check"] for r in payload["results"]}
    assert "check" in checks
    assert "check-trace" in checks
    assert "coverage" in checks


def test_malformed_spec_exits_two_without_traceback(capsys, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("not: a: valid: spec\nvars:\n  x: {type: int\n")
    code, _, err = run(capsys, "validate", str(bad))
    assert code == EXIT_SPEC_ERROR
    assert "Traceback" not in err
    assert err.startswith("error:")
